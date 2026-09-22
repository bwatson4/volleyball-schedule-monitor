from pathlib import Path


UPDATER = (Path(__file__).parents[1] / "scripts" / "update-pi.sh").read_text()


def test_pi_updater_uses_safe_git_and_protects_persistent_data():
    assert "set -Eeuo pipefail" in UPDATER
    assert "git fetch --prune origin" in UPDATER
    assert 'git merge --ff-only "origin/$branch"' in UPDATER
    assert "git reset --hard" not in UPDATER
    assert "git clean" not in UPDATER
    assert "git archive --format=tar HEAD" in UPDATER
    assert "runtime files, settings, credentials, PDFs, or a" in UPDATER


def test_pi_updater_uses_existing_venv_and_verifies_actual_runtime_units():
    assert '"$APP_DIR/.venv/bin/python" -m pip --version' in UPDATER
    assert "Run sudo ./deploy/install.sh to repair this installation." in UPDATER
    assert "volleyball-schedule.timer" in UPDATER
    assert "volleyball-ui.service" in UPDATER
    assert "systemctl daemon-reload" in UPDATER
    assert "journalctl -u" in UPDATER
    assert "http://127.0.0.1:$ui_port/" in UPDATER
