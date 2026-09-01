
from datetime import datetime
import pytest

# ----------------------------
# 1️⃣ Test: Initialization
# ----------------------------
def test_initialization():
    from src.parser import ScheduleParser
    parser = ScheduleParser(
        text="""2025 KVA Co-Ed League
        Final Week of 2025 League
        Wednesday
        Example Community Centre* December 3, 2025
        A POOL- East Gym
        1 Example Spikers
        2 Parents Night Out
        3 Smash Or Pass 8:00-9:45
        4 All Sets Are Off
        5 Nice Tips
        """,
        team_names="Example Spikers",
        gyms=["Example Community Centre"],
        pools=["A POOL", "B POOL", "C POOL", "D POOL", "E POOL", "F POOL", "G POOL", "H POOL"]
    )
    assert parser.team_names == ["Example Spikers"]
    assert parser.gyms == ["Example Community Centre"]
    assert parser.pools == ["A POOL", "B POOL", "C POOL", "D POOL", "E POOL", "F POOL", "G POOL", "H POOL"]
    assert parser.current_date is None
    assert parser.current_gym is None
    assert parser.current_pool is None

# ----------------------------
# 2️⃣ Test: _normalize_lines
# ----------------------------
def test_normalize_lines():
    from src.parser import ScheduleParser
    text = """
        2025 KVA Co-Ed League
        Final Week of 2025 League
        Wednesday
        Example Community Centre* December 3, 2025
        A POOL- East Gym
        1 Watch my 6
        3 Smash Or Pass 8:00-9:45
        """
    parser = ScheduleParser(text=text)
    normalized = parser._normalize_lines(text)
    assert normalized[0] == "2025 KVA Co-Ed League"
    assert normalized[1] == "Final Week of 2025 League"
    assert normalized[3] == "Example Community Centre* December 3, 2025"
    assert normalized[6] == "3 Smash Or Pass 8:00-9:45"

# ----------------------------
# 3️⃣ Test: parse (case-insensitive)
# ----------------------------
def test_parse_case_insensitive():
    from src.parser import ScheduleParser
    text = """
        2025 KVA Co-Ed League
        Final Week of 2025 League
        Wednesday
        example community centre* December 3, 2025
        a pool- East Gym
        1 example spikers
        3 Smash Or Pass 8:00-9:45
        """
    parser = ScheduleParser(
        text=text,
        team_names="EXAMPLE SPIKERS",  # different case
        gyms=["EXAMPLE COMMUNITY CENTRE"],  # different case
        pools=["A POOL"]              # different case
    )
    events = parser.parse()
    
    assert len(events) == 1
    event = events[0]
    
    assert event["summary"] == "example spikers Volleyball"
    assert event["season"] == "2025-26"
    assert event["description"] == "Team: example spikers; Gym: EXAMPLE COMMUNITY CENTRE, Pool: A POOL"
    assert event["start"] == datetime(2025, 12, 3, 20, 0)
    assert event["end"] == datetime(2025, 12, 3, 21, 45)

def test_multiple_aliases_match_exactly_after_case_and_whitespace_normalization():
    from src.parser import ScheduleParser
    text = "Example Gym December 3, 2026\nA POOL\n1  Sets   On The Beach  7:00-8:00"
    events = ScheduleParser(text, team_names=["Example Spikers", "SETS ON THE BEACH"], gyms=["Example Gym"], pools=["A POOL"]).parse()
    assert len(events) == 1
    assert events[0]["summary"] == "Sets On The Beach Volleyball"

def test_alias_matching_is_not_substring_or_fuzzy():
    from src.parser import ScheduleParser
    text = "Example Gym December 3, 2026\nA POOL\n1 Example Spikers 2 7:00-8:00"
    events = ScheduleParser(text, team_names=["Example Spikers"], gyms=["Example Gym"], pools=["A POOL"]).parse()
    assert events == []

def test_pool_teams_preserve_display_spelling_and_exclude_configured_aliases():
    from src.parser import ScheduleParser
    text = "Example Gym December 3, 2026\nA POOL\n1 Example Team 7:00-8:00\n2  TEAM   ALPHA\n3 Team Bravo"
    event = ScheduleParser(text, team_names=["example  team", "Example Team"], gyms=["Example Gym"], pools=["A POOL"]).parse()[0]
    assert event["pool_teams"] == [
        {"name": "TEAM ALPHA", "normalized_name": "team alpha"},
        {"name": "Team Bravo", "normalized_name": "team bravo"},
    ]

def test_alias_choice_does_not_change_stable_event_identity():
    from src.parser import ScheduleParser
    text = "Example Gym December 3, 2026\nA POOL\n1 Example Team 2 7:00-8:00"
    one = ScheduleParser(text, team_names=["Example Team 2"], gyms=["Example Gym"], pools=["A POOL"]).parse()[0]
    two = ScheduleParser(text, team_names=["Example Team", "Example Team 2"], gyms=["Example Gym"], pools=["A POOL"]).parse()[0]
    assert one["uid"] == two["uid"]


@pytest.mark.parametrize("heading, expected", [("A POOL", "A POOL"), ("H POOL", "H POOL"), ("I POOL", "I POOL")])
def test_dynamic_kva_pool_headings_preserve_source_label(heading, expected):
    from src.parser import ScheduleParser
    text = f"Example Gym December 3, 2026\n{heading}\n1 Example Team 7:00-8:00"
    event = ScheduleParser(text, team_names=["Example Team"], gyms=["Example Gym"]).parse()[0]
    assert event["pool"] == expected and event["pool_position"] == "1"


def test_non_pool_line_does_not_become_a_pool_heading():
    from src.parser import ScheduleParser
    parser = ScheduleParser("A POOL PARTY", team_names=["Example"], gyms=[])
    assert parser.detect_pool("A POOL PARTY") is False
