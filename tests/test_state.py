from src.state import ScheduleState

def test_state_resumes_and_completes(tmp_path):
    state = ScheduleState(tmp_path / "state.json"); state.begin_candidate("abc", tmp_path / "a.pdf", "https://x")
    state.mark_stage("parsed"); state.mark_stage("calendar")
    assert not state.complete_if_ready()
    resumed = ScheduleState(state.path); assert resumed.data["candidate"]["hash"] == "abc"
    resumed.mark_stage("email"); assert resumed.complete_if_ready()

def test_corrupt_state_recovers(tmp_path):
    path = tmp_path / "state.json"; path.write_text("not json")
    assert ScheduleState(path).data == {"version": 1}
    assert path.with_suffix(".json.corrupt").exists()
