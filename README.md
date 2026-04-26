<p align="center">
  <img src="firefox/icons/icon-1024.png" alt="Cookie Bank icon" width="512" height="512">
</p>

# Cookie Bank

Cookie Bank is a browser extension plus native Python broker. External Web API clients send cookie updates to the broker, and the active browser extension writes those cookies into the matching browser profile through the browser `cookies` API.

The broker never reads browser cookies. Its write path is:

1. Browser extension opens native messaging host `com.cookiebank.adapter`.
2. The Python adapter starts or joins the singleton broker.
3. External programs send a JSON cookie update request to the broker.
4. The broker chooses the target browser/profile and forwards only the requested cookie writes to that extension.
5. The extension calls `cookies.set` or `cookies.remove`.

## Layout

- `firefox/`: Firefox extension directory with `manifest.json` and browser-loadable copies of the shared files.
- `chromium/`: Chromium extension directory with `manifest.json` and browser-loadable copies of the shared files.
- `bin/cookie-bank-adapter.py`: native messaging adapter entrypoint.
- `bin/cookie-bank-client.py`: CLI for external programs and manual testing.
- `src/`: Python implementation modules.
- `installer.py`: writes native messaging host manifests.

## Run from Source

No pip install step is required. Cookie Bank uses only the Python standard library at runtime, and the scripts in `bin/` load the local modules under `src/` directly.
The commands below use explicit `python ... .py` invocations so they work consistently across Linux, macOS, and Windows source checkouts.

Check the CLI from the repository root:

```bash
python bin/cookie-bank-client.py --help
```

For Firefox:

```bash
python installer.py --browser firefox
```

Then open `about:debugging`, choose "This Firefox", click "Load Temporary Add-on", and select `firefox/manifest.json`. The Firefox extension id is fixed to `cookie-bank@local`, which matches the native host manifest.

This `about:debugging` flow is temporary. Firefox removes temporary add-ons after a browser restart.

For a persistent development install on Firefox Developer Edition, Nightly, or ESR, first open `about:config` in that browser profile and set `xpinstall.signatures.required` to `false`.

Then build an unsigned XPI from the repository root:

```bash
python - <<'PY'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path("firefox")
out = Path("cookie-bank.xpi")

with ZipFile(out, "w", ZIP_DEFLATED) as zf:
    for path in root.rglob("*"):
        if path.is_file():
            zf.write(path, path.relative_to(root))

print(out)
PY
```

Install the generated `cookie-bank.xpi` from `about:addons` with the gear menu, "Install Add-on From File...". The XPI is unsigned, so regular Firefox release builds may block it; use Mozilla signing for a normal release install.

For LibreWolf, use the same Firefox-compatible extension directory and install the native host for LibreWolf:

```bash
python installer.py --browser librewolf
```

For Chromium-based browsers:

Open `chrome://extensions`, enable "Developer mode", click "Load unpacked", and select the `chromium/` directory. Copy the generated extension id, then install the native host manifest:

```bash
python installer.py --browser chromium --extension-id <extension-id>
```

Supported Chromium-family browser values are `chrome`, `opera`, `opera-gx`, `edge`, `chromium`, `brave`, and `vivaldi`. Use the same `chromium/` extension directory for these browsers, then pass the matching `--browser` value to `installer.py`. The browser option can be repeated.

`--chrome-extension-id` is still accepted as an alias for `--extension-id`.

Use `--manifest-path <path>` to override where the native messaging host manifest is written. `--manifest-install-path` is accepted as an alias. If the path ends in `.json`, it is used as the exact manifest file path; otherwise it is treated as a directory and `com.cookiebank.adapter.json` is written under it. Because native messaging manifest contents differ by browser family, the manifest path override can only be used with one `--browser` value at a time.

The browser-specific extension directories contain real files rather than symlinks. This keeps temporary or unpacked extension loading reliable in browsers that reject or ignore extension resources outside the selected directory.

On Windows, native messaging hosts are discovered through registry keys. The installer writes the manifest file under `%LOCALAPPDATA%\Cookie Bank\native-hosts` and registers the current-user HKCU registry key by default; use `--no-registry` to skip registry registration. When no `--adapter-path` is provided on Windows, `--exe-builder auto` tries PyInstaller first, Nuitka second, and finally falls back to the same source-script style used on Linux and macOS. If that final fallback is selected automatically, the installer prints a warning.

When PyInstaller or Nuitka is used, the generated exe files are installed under `%LOCALAPPDATA%\Programs\Cookie Bank`:

```text
%LOCALAPPDATA%\Programs\Cookie Bank\cookie-bank-adapter.exe
%LOCALAPPDATA%\Programs\Cookie Bank\cookie-bank-client.exe
```

Install PyInstaller or Nuitka first when you want standalone exe launchers. Use `--exe-builder PyInstaller` or `--exe-builder nuitka` to force one builder, `--exe-builder none` to force the source-script mode, and `--exe-install-dir <path>` to override the per-user install directory used for generated exe files.

## Extension Operation

The popup contains:

- A central power button. On starts the native adapter and participates in broker routing. Off disconnects the adapter.
- The extension profile identifier UUID.
- A regenerate button for the identifier.

The extension starts in the On state after first install. The identifier and power state are stored in `storage.local`, so every browser profile and extension profile gets its own targetable value and remembers its own power setting.

## External Request Protocol

POSIX client endpoint:

- Address: `$XDG_RUNTIME_DIR/cookie-bank/client.sock`, or `/tmp/cookie-bank-<uid>/client.sock` when `XDG_RUNTIME_DIR` is absent.
- Transport: `AF_UNIX` stream socket.
- Framing: one JSON object per line, UTF-8.

Windows client endpoint:

- Address: `\\.\pipe\com.cookiebank.adapter.<user>.client`.
- Transport: `AF_PIPE` via `multiprocessing.connection`.
- Framing: UTF-8 JSON bytes using Python connection frames.

Request object:

```json
{
  "target": "firefox",
  "identifier": "550e8400-e29b-41d4-a716-446655440000",
  "cookies": [
    {
      "name": "sessionid",
      "value": "new-value",
      "domain": ".example.com",
      "path": "/",
      "secure": true,
      "httpOnly": true,
      "sameSite": "lax",
      "expires": 1790000000
    }
  ]
}
```

Fields:

- `target` is required. It must be either a browser executable name, such as `firefox`, or a full executable path, such as `/usr/bin/firefox`.
- When `target` is a name, the broker compares it against the filename part of each connected peer executable path and broadcasts the update to every matching target.
- When `target` is a full path, the broker matches the peer executable path exactly and broadcasts to every connected profile using that executable.
- `identifier` is optional. When present, it must be a UUID string and narrows the matched targets to that extension profile identifier. When omitted, routing is decided only by peer executable path information.
- `cookies` is required and must be an array.

Set a cookie by providing a `value` key. Delete a cookie by omitting the `value` key and providing only the target cookie `name` and `url`; extra keys in a delete object are ignored. For compatibility, `"value": null` is also treated as a delete command. The extension accepts Python `http.cookiejar.Cookie`-style fields such as `expires`, `discard`, `rest.HttpOnly`, and `rest.SameSite`, plus browser-style fields such as `url`, `expirationDate`, `sameSite`, `httpOnly`, and `storeId` for set operations.

The previous object-shaped target form is still accepted for compatibility, but new clients should use the top-level string `target` and optional top-level `identifier`.

## CLI Examples

List connected browser targets:

```bash
python bin/cookie-bank-client.py --list
```

Apply cookies from a file:

```bash
python bin/cookie-bank-client.py --target firefox --identifier <uuid> cookies.json
```

Read from stdin:

```bash
printf '%s\n' '[{"name":"sid","value":"abc","domain":"example.com","path":"/","secure":true}]' \
  | python bin/cookie-bank-client.py --target firefox -
```

## Security Notes

- The broker is guarded by a standard-library exclusive file lock for the current OS user.
- Runtime directories and socket files are restricted to the current OS user where the platform supports POSIX permissions.
- Browser extensions send cookies only toward the browser. The broker has no command that asks an extension to read cookies.
- Native messaging manifests restrict which extension ids may launch the adapter.
