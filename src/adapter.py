from __future__ import annotations

import argparse, os, subprocess, sys, threading, time

from pathlib import Path
from typing import Any

from broker import main as broker_main
from constants import NATIVE_HOST_NAME
from ipc import connect_connection, recv_connection_json, send_connection_json
from logging_config import configure_file_logger
from native_messaging import read_message, write_message
from paths import adapter_address, adapter_log_file
from peer import parent_process_info


BROKER_CONNECT_TIMEOUT_SECONDS = 5.0


class Adapter:
    def __init__(self) -> None:
        self.browser_process = parent_process_info()
        self.conn: Any | None = None
        self.conn_send_lock = threading.Lock()
        self.stdout_lock = threading.Lock()
        self.stop = threading.Event()
        self.logger = configure_file_logger("cookie-bank.adapter", adapter_log_file())

    def run(self) -> int:
        self.logger.info(
            "Adapter started pid=%s browser=%s",
            os.getpid(),
            self.browser_process.get("path") or self.browser_process.get("name"),
        )
        try:
            self.conn = self._connect_or_spawn_broker()
        except Exception:
            self.logger.exception("Unable to connect to broker")
            return 1

        broker_thread = threading.Thread(
            target=self._broker_read_loop,
            name="cookie-bank-broker-read",
            daemon=True,
        )
        broker_thread.start()

        try:
            self._browser_read_loop()
        finally:
            self.stop.set()
            if self.conn:
                try:
                    send_connection_json(
                        self.conn,
                        {
                            "type": "extension_disconnect",
                        },
                    )
                except Exception:
                    pass
                try:
                    self.conn.close()
                except OSError:
                    pass

        return 0

    def _connect_or_spawn_broker(self) -> Any:
        try:
            return connect_connection(adapter_address(), timeout=0.5)
        except Exception:
            self._spawn_broker()
            return connect_connection(
                adapter_address(), timeout=BROKER_CONNECT_TIMEOUT_SECONDS
            )

    def _spawn_broker(self) -> None:
        command = self._broker_command()
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        time.sleep(0.15)

    def _broker_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run-broker"]

        local_adapter = (
            Path(__file__).resolve().parents[1] / "bin" / "cookie-bank-adapter.py"
        )
        if local_adapter.exists():
            return [sys.executable, str(local_adapter), "--run-broker"]
        return [sys.executable, "-m", "adapter", "--run-broker"]

    def _browser_read_loop(self) -> None:
        assert self.conn is not None
        while not self.stop.is_set():
            try:
                message = read_message()
            except EOFError:
                return
            except Exception:
                self.logger.exception("Failed to read native message from browser")
                return

            enriched = self._enrich_browser_message(message)
            try:
                with self.conn_send_lock:
                    send_connection_json(self.conn, enriched)
            except Exception:
                self.logger.exception("Failed to send message to broker")
                return

    def _broker_read_loop(self) -> None:
        assert self.conn is not None
        while not self.stop.is_set():
            try:
                message = recv_connection_json(self.conn)
            except EOFError:
                return
            except OSError:
                return
            except Exception:
                self.logger.exception("Failed to read message from broker")
                return

            try:
                with self.stdout_lock:
                    write_message(message)
            except Exception:
                self.logger.exception("Failed to write native message to browser")
                self.stop.set()
                return

    def _enrich_browser_message(self, message: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(message)
        enriched["adapter_pid"] = os.getpid()
        enriched["native_host"] = NATIVE_HOST_NAME

        if enriched.get("type") == "extension_register":
            enriched["browser_process"] = self.browser_process

        return enriched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Cookie Bank native adapter.")
    parser.add_argument(
        "--run-broker",
        action="store_true",
        help="Run the internal broker process instead of the native messaging adapter.",
    )
    parser.add_argument(
        "native_messaging_args",
        nargs="*",
        metavar="native-messaging-arg",
        help=(
            "Arguments passed by the browser when launching a native messaging "
            "host, such as the manifest path or extension id. These are accepted "
            "for browser compatibility and ignored."
        ),
    )
    args = parser.parse_args(argv)
    if args.run_broker:
        return broker_main([])
    return Adapter().run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
