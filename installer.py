#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, os, platform, shutil, stat, subprocess, sys

from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
HOST_NAME = "com.cookiebank.adapter"
DESCRIPTION = "Cookie Bank native messaging adapter"
DEFAULT_FIREFOX_EXTENSION_ID = "cookie-bank@local"
WINDOWS_PROGRAM_DIR_NAME = "Cookie Bank"
BIN_ENTRYPOINTS = (
    ROOT / "bin" / "cookie-bank-adapter.py",
    ROOT / "bin" / "cookie-bank-client.py",
)
ADAPTER_ENTRYPOINT = ROOT / "bin" / "cookie-bank-adapter.py"

SUPPORTED_BROWSERS = (
    "chrome",
    "firefox",
    "librewolf",
    "opera",
    "opera-gx",
    "edge",
    "chromium",
    "brave",
    "vivaldi",
)
FIREFOX_FAMILY_BROWSERS = {"firefox", "librewolf"}
CHROMIUM_FAMILY_BROWSERS = set(SUPPORTED_BROWSERS) - FIREFOX_FAMILY_BROWSERS
BROWSER_ALIASES = {
    "brave-browser": "brave",
    "bravebrowser": "brave",
    "google-chrome": "chrome",
    "googlechrome": "chrome",
    "libre-wolf": "librewolf",
    "microsoft-edge": "edge",
    "microsoftedge": "edge",
    "msedge": "edge",
    "operagx": "opera-gx",
    "vivaldi-stable": "vivaldi",
    "vivaldistable": "vivaldi",
}


def browser_arg(value: str) -> str:
    raw = value.strip().lower()
    normalized = raw.replace("_", "-").replace(" ", "-")
    normalized = BROWSER_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_BROWSERS:
        supported = ", ".join(SUPPORTED_BROWSERS)
        raise argparse.ArgumentTypeError(
            f"unsupported browser {value!r}; use one of: {supported}"
        )
    return normalized


def native_host_dirs(browser: str) -> list[Path]:
    system = platform.system().lower()
    home = Path.home()

    if system == "linux":
        mapping = {
            "chrome": [home / ".config" / "google-chrome" / "NativeMessagingHosts"],
            "firefox": [home / ".mozilla" / "native-messaging-hosts"],
            "librewolf": [home / ".librewolf" / "native-messaging-hosts"],
            "opera": [home / ".config" / "opera" / "NativeMessagingHosts"],
            "opera-gx": [home / ".config" / "opera-gx" / "NativeMessagingHosts"],
            "edge": [home / ".config" / "microsoft-edge" / "NativeMessagingHosts"],
            "chromium": [home / ".config" / "chromium" / "NativeMessagingHosts"],
            "brave": [
                home
                / ".config"
                / "BraveSoftware"
                / "Brave-Browser"
                / "NativeMessagingHosts"
            ],
            "vivaldi": [home / ".config" / "vivaldi" / "NativeMessagingHosts"],
        }
        return mapping[browser]

    if system == "darwin":
        support = home / "Library" / "Application Support"
        mapping = {
            "chrome": [support / "Google" / "Chrome" / "NativeMessagingHosts"],
            "firefox": [support / "Mozilla" / "NativeMessagingHosts"],
            "librewolf": [support / "LibreWolf" / "NativeMessagingHosts"],
            "opera": [support / "com.operasoftware.Opera" / "NativeMessagingHosts"],
            "opera-gx": [
                support / "com.operasoftware.OperaGX" / "NativeMessagingHosts"
            ],
            "edge": [support / "Microsoft Edge" / "NativeMessagingHosts"],
            "chromium": [support / "Chromium" / "NativeMessagingHosts"],
            "brave": [
                support / "BraveSoftware" / "Brave-Browser" / "NativeMessagingHosts"
            ],
            "vivaldi": [support / "Vivaldi" / "NativeMessagingHosts"],
        }
        return mapping[browser]

    if system == "windows":
        base = Path(os.environ.get("LOCALAPPDATA") or home)
        return [base / "Cookie Bank" / "native-hosts" / browser]

    raise SystemExit(f"Unsupported platform: {platform.system()}")


def manifest_install_paths(
    browser: str,
    manifest_path_override: Path | None,
    browser_count: int,
) -> list[Path]:
    if manifest_path_override is None:
        return [
            directory / f"{HOST_NAME}.json" for directory in native_host_dirs(browser)
        ]

    if browser_count != 1:
        raise SystemExit(
            "--manifest-path can only be used with one --browser value because "
            "native messaging manifest contents are browser-specific."
        )

    path = manifest_path_override.expanduser().resolve()
    if path.suffix.lower() == ".json":
        return [path]
    return [path / f"{HOST_NAME}.json"]


def windows_program_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "Programs" / WINDOWS_PROGRAM_DIR_NAME


def entrypoint_exe_name(entrypoint: Path) -> str:
    return f"{entrypoint.stem}.exe"


def module_available(module_name: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", module_name, "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def choose_exe_builder(requested: str) -> tuple[str, bool]:
    if requested == "auto":
        for builder in ("PyInstaller", "nuitka"):
            if module_available(builder):
                return builder, False
        return "none", True

    if requested == "none":
        return requested, False

    builders = (requested,)
    for builder in builders:
        if module_available(builder):
            return builder, False

    raise SystemExit(f"Requested exe builder is not available: {requested}")


def build_with_pyinstaller(entrypoint: Path, build_dir: Path) -> Path:
    name = entrypoint.stem
    dist_dir = build_dir / "pyinstaller" / "dist"
    work_dir = build_dir / "pyinstaller" / "work" / name
    spec_dir = build_dir / "pyinstaller" / "spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(ROOT / "src"),
        str(entrypoint),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    return dist_dir / entrypoint_exe_name(entrypoint)


def build_with_nuitka(entrypoint: Path, build_dir: Path) -> Path:
    output_dir = build_dir / "nuitka" / entrypoint.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--assume-yes-for-downloads",
        "--output-dir",
        str(output_dir),
        "--output-filename",
        entrypoint_exe_name(entrypoint),
        str(entrypoint),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    return output_dir / entrypoint_exe_name(entrypoint)


def build_windows_exe(entrypoint: Path, builder: str, build_dir: Path) -> Path:
    if not entrypoint.exists():
        raise SystemExit(f"Entrypoint does not exist: {entrypoint}")
    if builder == "PyInstaller":
        return build_with_pyinstaller(entrypoint, build_dir)
    if builder == "nuitka":
        return build_with_nuitka(entrypoint, build_dir)
    raise SystemExit(f"Unsupported exe builder: {builder}")


def install_windows_exes(
    builder_request: str,
    install_dir: Path,
    build_dir: Path,
) -> dict[Path, Path]:
    builder, used_auto_source_fallback = choose_exe_builder(builder_request)
    if builder == "none":
        for entrypoint in BIN_ENTRYPOINTS:
            if not entrypoint.exists():
                raise SystemExit(f"Entrypoint does not exist: {entrypoint}")
        if used_auto_source_fallback:
            print(
                "WARNING: PyInstaller and Nuitka are not available; "
                "falling back to source .py entrypoints. Windows native "
                "messaging will rely on this source checkout and Python "
                "script execution.",
                file=sys.stderr,
            )
        return {entrypoint: entrypoint.resolve() for entrypoint in BIN_ENTRYPOINTS}

    install_dir.mkdir(parents=True, exist_ok=True)
    installed: dict[Path, Path] = {}

    for entrypoint in BIN_ENTRYPOINTS:
        built_exe = build_windows_exe(entrypoint, builder, build_dir)
        destination = install_dir / entrypoint_exe_name(entrypoint)
        shutil.copy2(built_exe, destination)
        installed[entrypoint] = destination
        print(f"Installed executable: {destination}")

    return installed


def extension_directory_for(browser: str) -> Path:
    if browser in FIREFOX_FAMILY_BROWSERS:
        return ROOT / "firefox"
    return ROOT / "chromium"


def print_install_summary(
    browser: str,
    manifest_path: Path,
    adapter_path: Path,
    manifest: dict[str, object],
    registry_updated: bool,
) -> None:
    print(f"Installed native messaging host for {browser}:")
    print(f"  manifest: {manifest_path}")
    print(f"  adapter:  {adapter_path}")

    allowed_extensions = manifest.get("allowed_extensions")
    if isinstance(allowed_extensions, list):
        print(f"  allowed extension: {', '.join(str(v) for v in allowed_extensions)}")

    allowed_origins = manifest.get("allowed_origins")
    if isinstance(allowed_origins, list):
        print(f"  allowed origin: {', '.join(str(v) for v in allowed_origins)}")

    if os.name == "nt":
        status = "updated" if registry_updated else "skipped"
        print(f"  registry: {status}")

    print(f"  extension directory: {extension_directory_for(browser)}")


def manifest_for(
    browser: str,
    adapter_path: Path,
    chromium_extension_ids: Iterable[str],
    firefox_extension_id: str,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "name": HOST_NAME,
        "description": DESCRIPTION,
        "path": str(adapter_path),
        "type": "stdio",
    }

    if browser in CHROMIUM_FAMILY_BROWSERS:
        origins = [
            f"chrome-extension://{extension_id}/"
            for extension_id in chromium_extension_ids
        ]
        if not origins:
            raise SystemExit(
                f"--extension-id is required when installing for {browser}."
            )
        manifest["allowed_origins"] = origins
    else:
        manifest["allowed_extensions"] = [firefox_extension_id]

    return manifest


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def register_windows(browser: str, manifest_path: Path) -> None:
    if os.name != "nt":
        return

    import winreg

    key_map = {
        "chrome": [r"Software\Google\Chrome\NativeMessagingHosts"],
        "firefox": [r"Software\Mozilla\NativeMessagingHosts"],
        "librewolf": [r"Software\LibreWolf\NativeMessagingHosts"],
        "opera": [r"Software\Opera Software\NativeMessagingHosts"],
        "opera-gx": [r"Software\Opera Software\Opera GX\NativeMessagingHosts"],
        "edge": [r"Software\Microsoft\Edge\NativeMessagingHosts"],
        "chromium": [r"Software\Chromium\NativeMessagingHosts"],
        "brave": [r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"],
        "vivaldi": [r"Software\Vivaldi\NativeMessagingHosts"],
    }
    for key_base in key_map[browser]:
        key_path = rf"{key_base}\{HOST_NAME}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(manifest_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Cookie Bank native messaging host manifests."
    )
    parser.add_argument(
        "--browser",
        action="append",
        type=browser_arg,
        metavar="{chrome,firefox,librewolf,opera,opera-gx,edge,chromium,brave,vivaldi}",
        required=True,
        help="Browser to install for. Can be repeated.",
    )
    parser.add_argument(
        "--adapter-path",
        type=Path,
        default=None,
        help="Absolute path to the native adapter executable.",
    )
    parser.add_argument(
        "--manifest-path",
        "--manifest-install-path",
        type=Path,
        dest="manifest_path",
        default=None,
        help=(
            "Override native messaging host manifest install path. A .json "
            "path is used as the file path; any other path is treated as a "
            "directory."
        ),
    )
    parser.add_argument(
        "--exe-builder",
        choices=["auto", "PyInstaller", "nuitka", "none"],
        default="auto",
        help=(
            "Windows only: exe builder to use for bin/*.py entrypoints. "
            "Use 'none' to reference the source .py scripts directly."
        ),
    )
    parser.add_argument(
        "--exe-install-dir",
        type=Path,
        default=None,
        help="Windows only: install generated exe files here.",
    )
    parser.add_argument(
        "--exe-build-dir",
        type=Path,
        default=ROOT / "build" / "native-exe",
        help="Windows only: temporary build output directory for exe generation.",
    )
    parser.add_argument(
        "--extension-id",
        "--chrome-extension-id",
        action="append",
        dest="chromium_extension_ids",
        default=[],
        help="Allowed Chromium-family extension id. Can be repeated.",
    )
    parser.add_argument(
        "--firefox-extension-id",
        default=DEFAULT_FIREFOX_EXTENSION_ID,
        help="Allowed Firefox extension id.",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="Windows only: skip HKCU native messaging registry registration.",
    )
    args = parser.parse_args(argv)

    browsers = args.browser
    if args.manifest_path is not None and len(browsers) != 1:
        parser.error(
            "--manifest-path can only be used with one --browser value because "
            "native messaging manifest contents are browser-specific."
        )

    write_registry = os.name == "nt" and not args.no_registry

    if os.name == "nt" and args.adapter_path is None:
        install_dir = (args.exe_install_dir or windows_program_dir()).expanduser()
        installed = install_windows_exes(
            args.exe_builder,
            install_dir.resolve(),
            args.exe_build_dir.expanduser().resolve(),
        )
        adapter_path = installed[ADAPTER_ENTRYPOINT]
    else:
        adapter_path = (
            (args.adapter_path if args.adapter_path is not None else ADAPTER_ENTRYPOINT)
            .expanduser()
            .resolve()
        )

    if not adapter_path.exists():
        raise SystemExit(f"Adapter path does not exist: {adapter_path}")

    print("Cookie Bank native host installer")
    print(f"Host name: {HOST_NAME}")
    print()

    for browser in browsers:
        manifest = manifest_for(
            browser,
            adapter_path,
            args.chromium_extension_ids,
            args.firefox_extension_id,
        )
        for path in manifest_install_paths(browser, args.manifest_path, len(browsers)):
            write_manifest(path, manifest)
            if write_registry:
                register_windows(browser, path)
            print_install_summary(browser, path, adapter_path, manifest, write_registry)
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
