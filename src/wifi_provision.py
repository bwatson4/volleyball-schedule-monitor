"""NetworkManager-backed Wi-Fi fallback provisioning for Raspberry Pi OS.

This module intentionally has no dependency on the volleyball application.  It
uses argument-list subprocess calls only; Wi-Fi credentials are never logged or
put in the volleyball settings file.
"""
from __future__ import annotations

import html
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs


LOG = logging.getLogger(__name__)
AP_CONNECTION = "VolleyballPi-Setup"
AP_SSID = "VolleyballPi-Setup"


class NmcliError(RuntimeError):
    """A deliberately non-verbose NetworkManager error.

    ``nmcli`` can echo command input in error output, so callers must not log
    its raw stdout/stderr after submitting a password.
    """


def _set_ui_service(action: str) -> None:
    """Keep the application UI unavailable on the untrusted setup subnet."""
    try:
        subprocess.run(["systemctl", action, "volleyball-ui.service"], check=False,
                       capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        # Network recovery is still more important than status UI availability.
        LOG.warning("Could not %s the normal UI while changing Wi-Fi mode", action)


def validate_ssid(value: str) -> str:
    ssid = str(value or "").strip()
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("SSID must contain 1 to 32 UTF-8 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise ValueError("SSID cannot contain control characters")
    return ssid


def validate_wifi_password(value: str) -> str:
    password = str(value or "")
    if not 8 <= len(password) <= 63:
        raise ValueError("Wi-Fi password must contain 8 to 63 characters")
    if "\x00" in password:
        raise ValueError("Wi-Fi password cannot contain NUL")
    return password


class NetworkManager:
    """Small, testable ``nmcli`` adapter for one Wi-Fi interface."""

    def __init__(self, interface: str = "wlan0", runner: Callable = subprocess.run):
        self.interface = interface
        self.runner = runner

    def _run(self, *args: str) -> str:
        try:
            result = self.runner(
                ["nmcli", "--terse", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NmcliError("NetworkManager command could not run") from exc
        if result.returncode != 0:
            raise NmcliError("NetworkManager command failed")
        return result.stdout.strip()

    def _try(self, *args: str) -> bool:
        try:
            self._run(*args)
            return True
        except NmcliError:
            return False

    def _activate_with_password(self, *args: str, password: str) -> str:
        """Pass a secret by a short-lived root-only file, not process argv."""
        descriptor, name = tempfile.mkstemp(prefix="volleyballpi-nmcli-", text=True)
        try:
            os.chmod(name, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(f"802-11-wireless-security.psk:{password}\n")
            return self._run(*args, "passwd-file", name)
        finally:
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass

    def client_connected(self) -> bool:
        """Return true only for an active infrastructure Wi-Fi connection."""
        try:
            name = self._run("-g", "GENERAL.CONNECTION", "device", "show", self.interface)
            if not name or name == "--":
                return False
            mode = self._run("-g", "802-11-wireless.mode", "connection", "show", "id", name)
            return mode.strip().lower() != "ap"
        except NmcliError:
            return False

    def scan(self) -> list[dict[str, str]]:
        """Return unique visible SSIDs when NetworkManager can scan the radio."""
        try:
            data = self._run("-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", self.interface, "--rescan", "auto")
        except NmcliError:
            return []
        found: dict[str, dict[str, str]] = {}
        for row in data.splitlines():
            fields = row.split(":", 2)
            if len(fields) != 3 or not fields[0]:
                continue
            ssid = fields[0].replace("\\:", ":").strip()
            try:
                validate_ssid(ssid)
            except ValueError:
                continue
            old = found.get(ssid)
            if old is None or int(fields[1] or 0) > int(old["signal"] or 0):
                found[ssid] = {"ssid": ssid, "signal": fields[1], "security": fields[2]}
        return sorted(found.values(), key=lambda item: int(item["signal"] or 0), reverse=True)

    def start_access_point(self, password: str) -> None:
        validate_wifi_password(password)
        # This profile is the only profile managed or replaced by this program.
        if not self._try("connection", "show", AP_CONNECTION):
            self._run("connection", "add", "type", "wifi", "ifname", self.interface,
                      "con-name", AP_CONNECTION, "autoconnect", "no", "ssid", AP_SSID)
        self._run("connection", "modify", AP_CONNECTION,
                  "connection.autoconnect", "no", "connection.autoconnect-priority", "-999",
                  "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
                  "802-11-wireless-security.key-mgmt", "wpa-psk",
                  "ipv4.method", "shared", "ipv4.addresses", "192.168.4.1/24",
                  "ipv6.method", "ignore")
        self._activate_with_password("--wait", "25", "connection", "up", "id", AP_CONNECTION,
                                     "ifname", self.interface, password=password)

    def stop_access_point(self) -> None:
        self._try("connection", "down", "id", AP_CONNECTION)

    def connect(self, ssid: str, password: str) -> None:
        ssid = validate_ssid(ssid)
        password = validate_wifi_password(password)
        # A dedicated, unbound client profile leaves every existing profile
        # untouched. Its secret reaches nmcli only via a 0600 temporary file.
        profile = str(uuid.uuid4())
        self._run("connection", "add", "type", "wifi", "ifname", self.interface,
                  "con-name", "VolleyballPi Wi-Fi", "connection.uuid", profile,
                  "connection.autoconnect", "no", "ssid", ssid,
                  "802-11-wireless-security.key-mgmt", "wpa-psk")
        self._activate_with_password("--wait", "25", "connection", "up", "uuid", profile,
                                     "ifname", self.interface, password=password)
        self._run("connection", "modify", "uuid", profile, "connection.autoconnect", "yes")


class ProvisioningController:
    def __init__(self, network: NetworkManager, ap_password: str, reconnect_seconds: int = 35,
                 ui_manager: Callable[[str], None] = _set_ui_service):
        self.network = network
        self.ap_password = validate_wifi_password(ap_password)
        self.reconnect_seconds = reconnect_seconds
        self.ui_manager = ui_manager

    def needs_setup(self) -> bool:
        return not self.network.client_connected()

    def start_setup(self, force: bool = False) -> None:
        if not force and self.network.client_connected():
            return
        # The setup AP is an untrusted local network. Do not leave the normal
        # configuration UI reachable at 192.168.4.1:8080 while it is active.
        self.ui_manager("stop")
        self.network.start_access_point(self.ap_password)
        LOG.info("Wi-Fi setup access point started")

    def submit(self, ssid: str, password: str) -> bool:
        """Attempt a new client connection and recover the AP on any failure."""
        validate_ssid(ssid)
        validate_wifi_password(password)
        self.network.stop_access_point()
        try:
            self.network.connect(ssid, password)
            deadline = time.monotonic() + self.reconnect_seconds
            while time.monotonic() < deadline:
                if self.network.client_connected():
                    LOG.info("Wi-Fi client connection established")
                    self.ui_manager("start")
                    return True
                time.sleep(2)
        except NmcliError:
            # Do not include the exception's command data: it may involve a PSK.
            LOG.warning("Wi-Fi connection attempt failed; returning to setup mode")
        self.start_setup()
        return False


def _portal_page(networks: list[dict[str, str]], message: str = "") -> str:
    options = "".join(
        f'<option value="{html.escape(item["ssid"], quote=True)}">'
        f'{html.escape(item["ssid"])} ({html.escape(item["signal"])}%)</option>'
        for item in networks
    )
    note = "" if networks else "<p>Enter the network name manually if no networks are listed.</p>"
    result = f'<p class="message">{html.escape(message)}</p>' if message else ""
    return f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VolleyballPi Wi-Fi setup</title><style>body{{font:16px sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem}}label,input,select,button{{display:block;width:100%;box-sizing:border-box;margin:.45rem 0}}input,select,button{{padding:.7rem}}.message{{color:#063}}</style></head><body>
<h1>Connect VolleyballPi to Wi-Fi</h1>{result}{note}<form method="post">
<label>Visible network <select name="listed_ssid"><option value="">Choose or enter below</option>{options}</select></label>
<label>Network name (for hidden networks) <input name="ssid" maxlength="32" autocomplete="off"></label>
<label>Wi-Fi password <input name="password" type="password" minlength="8" maxlength="63" autocomplete="current-password" required></label>
<button type="submit">Save and connect</button></form></body></html>'''


class ProvisioningPortal:
    """A short-lived HTTP server that exposes only Wi-Fi provisioning fields."""

    def __init__(self, controller: ProvisioningController, host: str = "0.0.0.0", port: int = 80):
        self.controller, self.host, self.port = controller, host, port
        self.server: ThreadingHTTPServer | None = None
        self._attempt_running = False

    def serve(self) -> None:
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: str, status: int = 200) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self) -> None:  # noqa: N802
                self._send(_portal_page(portal.controller.network.scan()))

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 4096:
                    self._send(_portal_page([], "Invalid request."), 400); return
                data = {key: values[0] for key, values in parse_qs(self.rfile.read(size).decode("utf-8", "replace")).items()}
                ssid = data.get("ssid", "") or data.get("listed_ssid", "")
                try:
                    validate_ssid(ssid); validate_wifi_password(data.get("password", ""))
                except ValueError as exc:
                    self._send(_portal_page(portal.controller.network.scan(), str(exc)), 400); return
                if portal._attempt_running:
                    self._send(_portal_page([], "A connection attempt is already in progress."), 409); return
                portal._attempt_running = True
                # Respond before turning off the AP so the browser receives this.
                self._send(_portal_page([], "Trying the network now. This page will disconnect; reconnect to your normal Wi-Fi and try volleyballpi.local:8080."), 202)
                threading.Thread(target=portal._attempt, args=(ssid, data["password"]), daemon=True).start()

            def log_message(self, *_: object) -> None:
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.serve_forever(poll_interval=1)

    def _attempt(self, ssid: str, password: str) -> None:
        success = self.controller.submit(ssid, password)
        self._attempt_running = False
        if success and self.server:
            self.server.shutdown()


def run_service() -> None:
    """Run the boot grace period and then the AP/portal only when necessary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    password = os.environ.get("WIFI_SETUP_PASSWORD", "")
    grace = max(0, int(os.environ.get("WIFI_BOOT_GRACE_SECONDS", "75")))
    network = NetworkManager(os.environ.get("WIFI_INTERFACE", "wlan0"))
    controller = ProvisioningController(network, password)
    force_setup = os.environ.get("WIFI_FORCE_SETUP", "").strip().lower() in {"1", "true", "yes"}
    if force_setup:
        LOG.warning("Forced Wi-Fi setup mode requested")
        controller.start_setup(force=True)
        ProvisioningPortal(controller, port=int(os.environ.get("WIFI_PORTAL_PORT", "80"))).serve()
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if network.client_connected():
            LOG.info("Normal Wi-Fi client connection is active; setup AP not needed")
            return
        time.sleep(5)
    if network.client_connected():
        LOG.info("Normal Wi-Fi client connection is active; setup AP not needed")
        return
    controller.start_setup()
    ProvisioningPortal(controller, port=int(os.environ.get("WIFI_PORTAL_PORT", "80"))).serve()
