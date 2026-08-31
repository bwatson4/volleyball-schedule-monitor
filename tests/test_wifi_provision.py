import logging
from types import SimpleNamespace

import pytest

from src.wifi_provision import (
    AP_CONNECTION,
    NetworkManager,
    ProvisioningController,
    _portal_page,
    validate_ssid,
)


class Runner:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        key = tuple(args[2:])
        reply = self.replies.get(key, (0, ""))
        return SimpleNamespace(returncode=reply[0], stdout=reply[1], stderr="")


def test_connected_normal_boot_does_not_start_access_point():
    network = type("Network", (), {"client_connected": lambda self: True})()
    controller = ProvisioningController(network, "safe-password")
    assert controller.needs_setup() is False
    controller.start_setup()
    assert not hasattr(network, "start_access_point")


def test_disconnected_boot_requests_setup_mode():
    class Network:
        def __init__(self): self.started = False
        def client_connected(self): return False
        def start_access_point(self, _password): self.started = True
    network = Network()
    ProvisioningController(network, "safe-password").start_setup()
    assert network.started


def test_scan_parsing_and_shell_like_ssid_is_an_argument_not_a_command():
    scan_key = ("-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", "wlan0", "--rescan", "auto")
    runner = Runner({scan_key: (0, "Home:72:WPA2\nCafe:41:WPA2")})
    network = NetworkManager(runner=runner)
    assert [item["ssid"] for item in network.scan()] == ["Home", "Cafe"]
    network._run("device", "wifi", "connect", "a;touch /never", "ifname", "wlan0")
    assert "a;touch /never" in runner.calls[-1]


def test_client_password_is_not_passed_in_the_nmcli_argument_list(monkeypatch, tmp_path):
    runner = Runner({})
    monkeypatch.setattr("src.wifi_provision.tempfile.mkstemp", lambda **_kwargs: (os.open(tmp_path / "secret", os.O_CREAT | os.O_WRONLY), str(tmp_path / "secret")))
    import os
    NetworkManager(runner=runner).connect("Home", "client-secret")
    assert all("client-secret" not in call for call in runner.calls)


@pytest.mark.parametrize("value", ["", "x" * 33, "bad\nssid"])
def test_malformed_ssid_rejected(value):
    with pytest.raises(ValueError):
        validate_ssid(value)


def test_failed_provisioning_recovers_setup_and_password_not_logged(caplog):
    class Network:
        def __init__(self): self.recovered = False
        def stop_access_point(self): pass
        def connect(self, _ssid, _password): raise RuntimeError("should not leak password")
        def client_connected(self): return False
        def start_access_point(self, _password): self.recovered = True
    network = Network()
    # The adapter's explicit error type represents a safe nmcli failure.
    network.connect = lambda _ssid, _password: (_ for _ in ()).throw(__import__("src.wifi_provision", fromlist=["NmcliError"]).NmcliError("failed"))
    with caplog.at_level(logging.INFO):
        assert ProvisioningController(network, "setup-secret").submit("Home", "client-secret") is False
    assert network.recovered
    assert "client-secret" not in caplog.text and "setup-secret" not in caplog.text


def test_successful_provision_transitions_to_client_without_deleting_profiles():
    class Network:
        def __init__(self): self.connected = False; self.calls = []
        def stop_access_point(self): self.calls.append("down")
        def connect(self, ssid, _password): self.calls.append(("connect", ssid)); self.connected = True
        def client_connected(self): return self.connected
        def start_access_point(self, _password): raise AssertionError("must not recover")
    network = Network()
    ui_actions = []
    assert ProvisioningController(network, "setup-secret", reconnect_seconds=1, ui_manager=ui_actions.append).submit("New network", "client-secret")
    assert network.calls == ["down", ("connect", "New network")]
    assert ui_actions == ["start"]
    assert "delete" not in " ".join(map(str, network.calls))


def test_portal_html_contains_no_application_secrets():
    page = _portal_page([{"ssid": "Home", "signal": "80", "security": "WPA2"}])
    assert "ICLOUD_PASSWORD" not in page
    assert "GMAIL_APP_PASSWORD" not in page
    assert "settings.json" not in page
    assert "Home" in page
