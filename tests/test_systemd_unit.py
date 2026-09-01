from pathlib import Path


UNIT = (Path(__file__).parents[1] / "deploy" / "volleyball-schedule.service").read_text()


def test_schedule_service_uses_systemd_runtime_directory_for_lock():
    assert "User=volleyball" in UNIT
    assert "Group=volleyball" in UNIT
    assert "RuntimeDirectory=volleyball-schedule-monitor" in UNIT
    assert "RuntimeDirectoryMode=0755" in UNIT
    assert "/run/volleyball-schedule-monitor/monitor.lock" in UNIT
    assert "/run/volleyball-schedule-monitor.lock" not in UNIT
