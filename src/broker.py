from __future__ import annotations

import argparse, os, signal, socket, threading, time, uuid

from multiprocessing.connection import Listener
from typing import Any

from ipc import (
    connection_family,
    recv_connection_json,
    recv_socket_json,
    send_connection_json,
    send_socket_json,
)
from locking import interprocess_lock
from logging_config import configure_file_logger
from paths import (
    adapter_address,
    broker_log_file,
    client_address,
    lock_file,
    remove_stale_socket,
    restrict_socket,
    runtime_dir,
    state_dir,
)
from protocol import error_response, ok_response, request_id, utc_timestamp


IDLE_EXIT_SECONDS = 10.0
DEFAULT_APPLY_TIMEOUT_SECONDS = 30.0


def install_signal_handlers(broker: "Broker") -> None:
    def handle_signal(signum: int, _frame: Any) -> None:
        broker.logger.info("Broker received signal %s; shutting down", signum)
        broker.shutdown.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, handle_signal)


class Broker:
    def __init__(self, idle_exit_seconds: float = IDLE_EXIT_SECONDS) -> None:
        self.idle_exit_seconds = idle_exit_seconds
        self.adapters: dict[str, dict[str, Any]] = {}
        self.adapters_lock = threading.RLock()
        self.pending: dict[str, dict[str, Any]] = {}
        self.pending_lock = threading.RLock()
        self.shutdown = threading.Event()
        self.logger = configure_file_logger("cookie-bank.broker", broker_log_file())
        self.adapter_listener: Listener | None = None
        self.client_listener: Listener | None = None
        self.client_socket: socket.socket | None = None

    def serve_forever(self) -> int:
        state_dir()
        runtime_dir()
        lock = interprocess_lock(str(lock_file()))
        if not lock.acquire(blocking=False):
            self.logger.info("Another broker already holds the singleton lock")
            return 0

        try:
            self._start_adapter_listener()
            self._start_client_listener()
            self.logger.info("Broker started")

            empty_since: float | None = None
            while not self.shutdown.is_set():
                time.sleep(0.25)
                with self.adapters_lock:
                    adapter_count = len(self.adapters)

                if adapter_count:
                    empty_since = None
                    continue

                if empty_since is None:
                    empty_since = time.monotonic()
                elif time.monotonic() - empty_since >= self.idle_exit_seconds:
                    self.logger.info("No adapters remain; broker exiting")
                    break

            return 0
        finally:
            self.shutdown.set()
            self._close_listeners()
            remove_stale_socket(adapter_address())
            remove_stale_socket(client_address())
            lock.release()

    def _start_adapter_listener(self) -> None:
        address = adapter_address()
        remove_stale_socket(address)
        self.adapter_listener = Listener(
            address, family=connection_family(), authkey=None
        )
        restrict_socket(address)

        thread = threading.Thread(
            target=self._adapter_accept_loop,
            name="cookie-bank-adapter-accept",
            daemon=True,
        )
        thread.start()

    def _start_client_listener(self) -> None:
        address = client_address()

        if os.name == "nt":
            self.client_listener = Listener(
                address, family=connection_family(), authkey=None
            )
            thread = threading.Thread(
                target=self._client_connection_accept_loop,
                name="cookie-bank-client-accept",
                daemon=True,
            )
            thread.start()
            return

        remove_stale_socket(address)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(address)
        restrict_socket(address)
        sock.listen(32)
        sock.settimeout(0.5)
        self.client_socket = sock

        thread = threading.Thread(
            target=self._client_socket_accept_loop,
            name="cookie-bank-client-accept",
            daemon=True,
        )
        thread.start()

    def _close_listeners(self) -> None:
        if self.adapter_listener:
            self.adapter_listener.close()
        if self.client_listener:
            self.client_listener.close()
        if self.client_socket:
            self.client_socket.close()

    def _adapter_accept_loop(self) -> None:
        assert self.adapter_listener is not None
        while not self.shutdown.is_set():
            try:
                conn = self.adapter_listener.accept()
            except (OSError, EOFError):
                if not self.shutdown.is_set():
                    self.logger.exception("Adapter listener failed")
                return

            thread = threading.Thread(
                target=self._handle_adapter,
                args=(conn,),
                name="cookie-bank-adapter",
                daemon=True,
            )
            thread.start()

    def _client_connection_accept_loop(self) -> None:
        assert self.client_listener is not None
        while not self.shutdown.is_set():
            try:
                conn = self.client_listener.accept()
            except (OSError, EOFError):
                if not self.shutdown.is_set():
                    self.logger.exception("Client listener failed")
                return

            thread = threading.Thread(
                target=self._handle_connection_client,
                args=(conn,),
                name="cookie-bank-client",
                daemon=True,
            )
            thread.start()

    def _client_socket_accept_loop(self) -> None:
        assert self.client_socket is not None
        while not self.shutdown.is_set():
            try:
                conn, _ = self.client_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.shutdown.is_set():
                    self.logger.exception("Client socket listener failed")
                return

            thread = threading.Thread(
                target=self._handle_socket_client,
                args=(conn,),
                name="cookie-bank-client",
                daemon=True,
            )
            thread.start()

    def _handle_adapter(self, conn: Any) -> None:
        identifier: str | None = None
        try:
            while not self.shutdown.is_set():
                message = recv_connection_json(conn)
                message_type = message.get("type")

                if message_type == "extension_register":
                    if identifier:
                        self._remove_adapter(identifier, conn)
                    identifier = self._register_adapter(conn, message)
                    send_connection_json(
                        conn,
                        {
                            "type": "broker_registered",
                            "identifier": identifier,
                            "registered_at": utc_timestamp(),
                        },
                    )
                elif message_type == "extension_status":
                    if identifier:
                        self._update_adapter_status(identifier, message)
                elif message_type == "apply_result":
                    self._complete_pending(message)
                elif message_type == "extension_disconnect":
                    break
                else:
                    self.logger.info("Ignoring adapter message type=%s", message_type)
        except EOFError:
            pass
        except Exception:
            self.logger.exception("Adapter connection failed")
        finally:
            if identifier:
                self._remove_adapter(identifier, conn)
            try:
                conn.close()
            except OSError:
                pass

    def _register_adapter(self, conn: Any, message: dict[str, Any]) -> str:
        identifier = str(uuid.UUID(str(message.get("identifier"))))
        now = utc_timestamp()
        browser_process = message.get("browser_process") or {}

        record = {
            "conn": conn,
            "send_lock": threading.Lock(),
            "connected_at": now,
            "last_seen": now,
            "identifier": identifier,
            "extension_id": message.get("extension_id"),
            "browser_process": browser_process,
            "browser_info": message.get("browser_info") or {},
            "adapter_pid": message.get("adapter_pid"),
        }

        with self.adapters_lock:
            self.adapters[identifier] = record

        self.logger.info(
            "Registered adapter identifier=%s browser=%s",
            identifier,
            self._record_browser_path(record) or self._record_browser_name(record),
        )
        return identifier

    def _update_adapter_status(self, identifier: str, message: dict[str, Any]) -> None:
        with self.adapters_lock:
            record = self.adapters.get(identifier)
            if not record:
                return
            record["last_seen"] = utc_timestamp()
            if "identifier" in message:
                new_identifier = str(uuid.UUID(str(message["identifier"])))
                record["identifier"] = new_identifier
                if new_identifier != identifier:
                    self.adapters.pop(identifier, None)
                    self.adapters[new_identifier] = record

    def _remove_adapter(self, identifier: str, conn: Any) -> None:
        with self.adapters_lock:
            record = self.adapters.get(identifier)
            if record and record["conn"] is conn:
                self.adapters.pop(identifier, None)
        self.logger.info("Removed adapter identifier=%s", identifier)

    def _complete_pending(self, message: dict[str, Any]) -> None:
        req_id = str(message.get("request_id") or "")
        with self.pending_lock:
            slot = self.pending.get(req_id)

        if not slot:
            self.logger.info("Received result for unknown request id=%s", req_id)
            return

        slot["result"] = message
        slot["event"].set()

    def _handle_connection_client(self, conn: Any) -> None:
        try:
            request = recv_connection_json(conn)
            response = self._handle_client_request(request)
            send_connection_json(conn, response)
        except Exception as exc:
            try:
                send_connection_json(
                    conn, error_response("client_error", f"{type(exc).__name__}: {exc}")
                )
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_socket_client(self, conn: socket.socket) -> None:
        with conn:
            file_obj = conn.makefile("rb")
            try:
                request = recv_socket_json(file_obj)
                response = self._handle_client_request(request)
                send_socket_json(conn, response)
            except Exception as exc:
                try:
                    send_socket_json(
                        conn,
                        error_response("client_error", f"{type(exc).__name__}: {exc}"),
                    )
                except Exception:
                    pass

    def _handle_client_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_type = request.get("type")
        if request_type == "list_targets":
            return ok_response(targets=self._public_targets())
        if request_type in {"cookie_update", "apply_cookies"} or (
            request_type is None and "cookies" in request
        ):
            return self._apply_cookie_update(request)
        return error_response(
            "unknown_request",
            "Expected `cookies` for a cookie update or request type `list_targets`.",
        )

    def _public_targets(self) -> list[dict[str, Any]]:
        with self.adapters_lock:
            records = list(self.adapters.values())

        return [self._public_target(record) for record in records]

    def _public_target(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "identifier": record.get("identifier"),
            "extension_id": record.get("extension_id"),
            "browser_process": record.get("browser_process"),
            "browser_info": record.get("browser_info"),
            "adapter_pid": record.get("adapter_pid"),
            "connected_at": record.get("connected_at"),
            "last_seen": record.get("last_seen"),
        }

    def _apply_cookie_update(self, request: dict[str, Any]) -> dict[str, Any]:
        cookies = request.get("cookies")
        if not isinstance(cookies, list):
            return error_response("invalid_cookies", "`cookies` must be an array.")

        route = self._route_from_request(request)
        if "error" in route:
            return route["error"]

        matches = self._match_target(route)
        if not matches:
            return error_response(
                "target_not_found", "No connected browser target matched."
            )

        base_req_id = str(request.get("request_id") or request_id("cookie"))
        timeout = float(request.get("timeout") or DEFAULT_APPLY_TIMEOUT_SECONDS)
        started_at = time.monotonic()
        results: list[dict[str, Any]] = []
        pending_items: list[tuple[dict[str, Any], str, dict[str, Any]]] = []

        for index, record in enumerate(matches):
            child_req_id = (
                base_req_id if len(matches) == 1 else f"{base_req_id}:{index}"
            )
            slot = {"event": threading.Event(), "result": None}

            with self.pending_lock:
                self.pending[child_req_id] = slot

            try:
                self._send_to_adapter(
                    record,
                    {
                        "type": "apply_cookies",
                        "request_id": child_req_id,
                        "cookies": cookies,
                        "target": route["target"],
                        "identifier": route.get("identifier"),
                    },
                )
            except Exception as exc:
                with self.pending_lock:
                    self.pending.pop(child_req_id, None)
                results.append(
                    {
                        "target": self._public_target(record),
                        "request_id": child_req_id,
                        "ok": False,
                        "error": {
                            "code": "send_failed",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    }
                )
                continue

            pending_items.append((record, child_req_id, slot))

        for record, child_req_id, slot in pending_items:
            remaining = max(0.0, timeout - (time.monotonic() - started_at))
            if not slot["event"].wait(remaining):
                with self.pending_lock:
                    self.pending.pop(child_req_id, None)
                results.append(
                    {
                        "target": self._public_target(record),
                        "request_id": child_req_id,
                        "ok": False,
                        "error": {
                            "code": "apply_timeout",
                            "message": "Timed out waiting for browser result.",
                        },
                    }
                )
                continue

            with self.pending_lock:
                self.pending.pop(child_req_id, None)

            result = slot["result"]
            if not isinstance(result, dict):
                results.append(
                    {
                        "target": self._public_target(record),
                        "request_id": child_req_id,
                        "ok": False,
                        "error": {
                            "code": "invalid_result",
                            "message": "Browser returned an invalid result.",
                        },
                    }
                )
                continue

            results.append(
                {
                    "target": self._public_target(record),
                    "request_id": child_req_id,
                    "ok": bool(result.get("ok")),
                    "result": result,
                }
            )

        total_applied = sum(
            int(item.get("result", {}).get("applied", 0))
            for item in results
            if isinstance(item.get("result"), dict)
        )
        total_failed = sum(
            int(item.get("result", {}).get("failed", 0))
            for item in results
            if isinstance(item.get("result"), dict)
        )
        all_ok = bool(results) and all(item.get("ok") for item in results)

        response = {
            "ok": all_ok,
            "request_id": base_req_id,
            "target_count": len(matches),
            "applied": total_applied,
            "failed": total_failed,
            "results": results,
        }
        if len(results) == 1 and "result" in results[0]:
            response["result"] = results[0]["result"]
        if not all_ok:
            response["error"] = {
                "code": (
                    "partial_failure"
                    if any(item.get("ok") for item in results)
                    else "apply_failed"
                ),
                "message": "One or more browser targets failed to apply the cookie update.",
            }
        return response

    def _route_from_request(self, request: dict[str, Any]) -> dict[str, Any]:
        target = request.get("target")
        identifier = request.get("identifier")

        if isinstance(target, dict):
            if identifier is None:
                identifier = target.get("identifier") or target.get("profile")
            target = (
                target.get("browser_path")
                or target.get("path")
                or target.get("browser")
                or target.get("executable")
            )

        if identifier is not None:
            try:
                identifier = str(uuid.UUID(str(identifier)))
            except (TypeError, ValueError):
                return {
                    "error": error_response(
                        "invalid_identifier",
                        "`identifier` must be a UUID string when provided.",
                    )
                }

        if not isinstance(target, str) or not target.strip():
            return {
                "error": error_response(
                    "invalid_target",
                    "`target` must be a browser executable name or executable path.",
                )
            }

        target = target.strip()
        return {
            "target": target,
            "identifier": identifier,
            "mode": "path" if self._looks_like_path(target) else "name",
        }

    def _send_to_adapter(self, record: dict[str, Any], payload: dict[str, Any]) -> None:
        with record["send_lock"]:
            send_connection_json(record["conn"], payload)

    def _match_target(self, route: dict[str, Any]) -> list[dict[str, Any]]:
        with self.adapters_lock:
            records = list(self.adapters.values())

        matches: list[dict[str, Any]] = []
        for record in records:
            if self._record_matches(record, route):
                matches.append(record)
        return matches

    def _record_matches(self, record: dict[str, Any], route: dict[str, Any]) -> bool:
        identifier = route.get("identifier")
        if identifier and str(identifier) != str(record.get("identifier")):
            return False

        mode = route.get("mode")
        target = str(route.get("target") or "")
        if mode == "path":
            return self._same_path(target, self._record_browser_path(record))
        if mode == "name":
            return self._browser_name_matches(target, record)
        return False

    def _looks_like_path(self, value: str) -> bool:
        return (
            os.path.isabs(value)
            or "/" in value
            or "\\" in value
            or (len(value) > 1 and value[1] == ":")
        )

    def _same_path(self, expected: Any, actual: Any) -> bool:
        if not expected or not actual:
            return False
        if os.name == "nt":
            return os.path.normcase(os.path.abspath(str(expected))) == os.path.normcase(
                os.path.abspath(str(actual))
            )
        return os.path.abspath(str(expected)) == os.path.abspath(str(actual))

    def _browser_name_matches(self, expected: str, record: dict[str, Any]) -> bool:
        expected_names = self._executable_name_candidates(expected)
        actual_path = self._record_browser_path(record)
        actual_name = (
            self._basename(str(actual_path))
            if actual_path
            else str(self._record_browser_name(record) or "")
        )
        actual_names = self._executable_name_candidates(actual_name)
        return bool(expected_names & actual_names)

    def _record_browser_path(self, record: dict[str, Any]) -> Any:
        browser_process = record.get("browser_process") or {}
        if isinstance(browser_process, dict):
            return browser_process.get("path")
        return None

    def _record_browser_name(self, record: dict[str, Any]) -> Any:
        browser_process = record.get("browser_process") or {}
        if isinstance(browser_process, dict):
            return browser_process.get("name")
        return None

    def _basename(self, value: str) -> str:
        return value.replace("\\", "/").rstrip("/").split("/")[-1]

    def _executable_name_candidates(self, value: str) -> set[str]:
        name = self._basename(value).lower()
        names = {name}
        for suffix in (".exe", ".bin"):
            if name.endswith(suffix):
                names.add(name[: -len(suffix)])
        return {candidate for candidate in names if candidate}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Cookie Bank broker.")
    parser.add_argument(
        "--idle-exit-seconds",
        type=float,
        default=IDLE_EXIT_SECONDS,
        help="Exit after this many seconds with no adapter connections.",
    )
    args = parser.parse_args(argv)

    broker = Broker(idle_exit_seconds=args.idle_exit_seconds)
    install_signal_handlers(broker)
    return broker.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
