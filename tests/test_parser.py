
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
        {"name": "TEAM ALPHA", "normalized_name": "team alpha", "position": 2},
        {"name": "Team Bravo", "normalized_name": "team bravo", "position": 3},
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


CURRENT_2026_WEDNESDAY_TEXT = """Welcome to the 2026 KVA Co-Ed League!
POOLS GH WILL NOT PLAY TONIGHT- MAKE UP DAY IN DEC

TCC
F POOL
A POOL
KCS
B POOL
D POOL
OLPH
C POOL
E POOL
Game 1
Game 2
Game 3
Game 4
Game 5
Wednesday
September 16, 2026
1
2
3
4
5
1
2
3
4
5
1
2
3
4
5
1
2
3
4
5
1
2
3
4
5
1
2
3
4
5
The VolleyBrawlers
Setting Ducks
Bet on the Net
Ace Holes
Volley Ballers
Watch My 6
Smash or Pass
Bit Tipsy
Play to Win
Bumpernickels
Set Destroyers
Schanzenblocks
No Non Sets
Smash Bros
I'd Hit That
Block Busters
Back Seat Sets
Chewblaccas
To Kill A Rocking Serve
Spikeachu
Valleypaulers
Setsy 3.0
Nice Tips
Safe Sets
Serves You Right
Thin Blue Line
Spike Up Your Life
Leisure Athletes
Holy Blockamole
Net Win
1v5
1v4
1v3
1v2
3v4
2v3
3v5
2v4
4v5
2v5
7:00-8:15
8:15-9:30
7:00-8:30
8:30-10:00
6:30-8:00
8:00- 9:30"""


def test_current_flattened_wednesday_schedule_reconstructs_chewblaccas_session():
    from src.parser import ScheduleParser
    event = ScheduleParser(
        CURRENT_2026_WEDNESDAY_TEXT,
        team_names=["Chewblockas", "Chewblaccas"],
        gyms=["Pacway", "KCS", "TCC", "OLPH", "Valleyview"],
    ).parse()[0]
    assert event["date"] == "2026-09-16"
    assert event["source_team"] == "Chewblaccas"
    assert event["gym"] == "KCS" and event["pool"] == "D POOL"
    assert event["pool_position"] == "3"
    assert event["start"] == datetime(2026, 9, 16, 20, 30)
    assert event["end"] == datetime(2026, 9, 16, 22, 0)
    assert event["pool_teams"] == [
        {"name": "Block Busters", "normalized_name": "block busters", "position": 1},
        {"name": "Back Seat Sets", "normalized_name": "back seat sets", "position": 2},
        {"name": "To Kill A Rocking Serve", "normalized_name": "to kill a rocking serve", "position": 4},
        {"name": "Spikeachu", "normalized_name": "spikeachu", "position": 5},
    ]
    assert event["rotation_matrix"] == [
        {"round": "Game 1", "pairings": [(1, 5), (2, 3)]},
        {"round": "Game 2", "pairings": [(1, 4), (3, 5)]},
        {"round": "Game 3", "pairings": [(1, 3), (2, 4)]},
        {"round": "Game 4", "pairings": [(1, 2), (4, 5)]},
        {"round": "Game 5", "pairings": [(3, 4), (2, 5)]},
    ]


def test_rotation_matrix_accepts_spacing_and_case_variants():
    from src.parser import ScheduleParser
    text = """Example Gym December 3, 2026
A POOL
1 Configured Team 7:00-8:00
2 Team Two
3 Team Three
Game 1: 1v2, 3 V 4
Game 2: 1 v 3, 2v4"""
    event = ScheduleParser(text, team_names=["Configured Team"], gyms=["Example Gym"]).parse()[0]
    assert event["rotation_matrix"] == [
        {"round": "Game 1", "pairings": [(1, 2), (3, 4)]},
        {"round": "Game 2", "pairings": [(1, 3), (2, 4)]},
    ]


def test_missing_or_malformed_rotation_never_invalidates_assignment():
    from src.parser import ScheduleParser
    base = "Example Gym December 3, 2026\nA POOL\n1 Configured Team 7:00-8:00\n2 Team Two"
    missing = ScheduleParser(base, team_names=["Configured Team"], gyms=["Example Gym"]).parse()
    malformed = ScheduleParser(base + "\nGame 1: not a matchup", team_names=["Configured Team"], gyms=["Example Gym"]).parse()
    assert len(missing) == len(malformed) == 1
    assert missing[0]["rotation_matrix"] == malformed[0]["rotation_matrix"] == []


def flattened_text(pools, target_pool):
    """Synthetic pdfminer-order schedule with a configurable active pool set."""
    headings = []
    for index, pool in enumerate(pools):
        headings.extend(["North Gym" if index < len(pools) // 2 else "South Gym", f"{pool} POOL"])
    slots = [str(position) for _pool in pools for position in range(1, 5)]
    teams = [f"{pool} Team {position}" for pool in pools for position in range(1, 5)]
    teams[pools.index(target_pool) * 4 + 2] = "Configured Team"
    times = [f"{6 + (index % 5)}:00-{7 + (index % 5)}:00" for index in range(len(pools))]
    return "\n".join(headings + ["Wednesday", "October 7, 2026"] + slots + teams + ["1v4", "2v3"] + times)


def test_flattened_full_week_detects_eight_blocks_without_a_six_pool_assumption():
    from src.parser import ScheduleParser
    event = ScheduleParser(
        flattened_text(list("ABCDEFGH"), "H"), team_names=["Configured Team"], gyms=["North Gym", "South Gym"]
    ).parse()[0]
    assert event["pool"] == "H POOL" and event["gym"] == "South Gym"
    assert event["start"] == datetime(2026, 10, 7, 20, 0)
    assert event["pool_teams"] == [
        {"name": "H Team 1", "normalized_name": "h team 1", "position": 1},
        {"name": "H Team 2", "normalized_name": "h team 2", "position": 2},
        {"name": "H Team 4", "normalized_name": "h team 4", "position": 4},
    ]


def test_flattened_reduced_week_handles_arbitrarily_omitted_pools():
    from src.parser import ScheduleParser
    event = ScheduleParser(
        flattened_text(["A", "C", "F", "H"], "F"), team_names=["Configured Team"], gyms=["North Gym", "South Gym"]
    ).parse()[0]
    assert event["pool"] == "F POOL" and event["gym"] == "South Gym"
    assert event["start"] == datetime(2026, 10, 7, 20, 0)
