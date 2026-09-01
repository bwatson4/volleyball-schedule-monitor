from datetime import date

from src.season import season_for_date


def test_season_boundary_is_september_through_august():
    assert season_for_date(date(2027, 8, 31)) == "2026-27"
    assert season_for_date(date(2027, 9, 1)) == "2027-28"


def test_games_from_september_through_following_april_share_a_season():
    assert {season_for_date(value) for value in (date(2026, 9, 1), date(2027, 1, 15), date(2027, 4, 30))} == {"2026-27"}
