from pathlib import Path


INSTALLER = (Path(__file__).parents[1] / "deploy" / "install.sh").read_text()


def test_installer_excludes_virtualenv_from_permission_normalization():
    assert 'chown -R root:root "$APP_DIR"' not in INSTALLER
    assert 'find "$APP_DIR" -path "$APP_DIR/.venv" -prune -o' in INSTALLER


def test_installer_recreates_unusable_virtualenv_and_uses_python_pip():
    assert '[[ ! -x "$APP_DIR/.venv/bin/python" ]]' in INSTALLER
    assert '"$APP_DIR/.venv/bin/python" -m pip --version' in INSTALLER
    assert 'rm -rf "$APP_DIR/.venv"' in INSTALLER
    assert '"$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir' in INSTALLER


def test_installer_uses_networkmanager_shared_dns_and_safe_update_restarts():
    assert "dnsmasq-shared.d/volleyballpi-captive.conf" in INSTALLER
    assert "systemctl try-restart volleyball-ui.service" in INSTALLER
    assert "systemctl restart volleyball-schedule.timer" in INSTALLER
    assert "NetworkManager" not in INSTALLER.split("systemctl daemon-reload", 1)[1]
