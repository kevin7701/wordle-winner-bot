"""Tests for bot.py parsing and scoring. No Discord connection needed.

Run: python test_bot.py (needs discord.py and python-dotenv installed).
"""

from datetime import date

from bot import (
    already_crowned,
    choose_puzzle,
    day_bounds,
    expected_connections_number,
    expected_puzzle_number,
    games_to_run,
    parse_connections,
    parse_wordle,
    pick_winners,
)


def test_parse_wordle():
    assert parse_wordle("Wordle 1,234 3/6") == (1234, 3)
    assert parse_wordle("Wordle 987 X/6*") == (987, 7)
    assert parse_wordle("Wordle 100 6/6") == (100, 6)
    assert parse_wordle("Wordle 1,234 1/6") == (1234, 1)
    assert parse_wordle("just chatting, no score") is None
    assert parse_wordle("") is None
    # Share with commentary around it still parses.
    assert parse_wordle("finally!\nWordle 1,200 4/6\n🟩⬜⬜🟨⬜") == (1200, 4)


def test_expected_wordle_number():
    assert expected_puzzle_number(date(2021, 6, 19)) == 0
    assert expected_puzzle_number(date(2021, 6, 20)) == 1


def test_parse_connections_perfect():
    share = (
        "Connections\n"
        "Puzzle #1,248\n"
        "🟨🟨🟨🟨\n"
        "🟩🟩🟩🟩\n"
        "🟦🟦🟦🟦\n"
        "🟪🟪🟪🟪"
    )
    assert parse_connections(share) == (1248, 4)


def test_parse_connections_with_mistakes():
    share = (
        "Connections\n"
        "Puzzle #1248\n"
        "🟨🟨🟨🟨\n"
        "🟩🟪🟦🟨\n"
        "🟩🟩🟩🟩\n"
        "🟦🟦🟦🟦\n"
        "🟪🟪🟪🟪"
    )
    assert parse_connections(share) == (1248, 5)


def test_parse_connections_failed():
    # 4 mistakes plus the 4 groups the game reveals: 8 rows.
    share = (
        "Connections\n"
        "Puzzle #1248\n"
        "🟨🟨🟨🟨\n"
        "🟩🟪🟦🟨\n"
        "🟩🟩🟩🟩\n"
        "🟪🟦🟪🟦\n"
        "🟦🟩🟦🟨\n"
        "🟪🟨🟪🟩\n"
        "🟦🟦🟦🟦\n"
        "🟪🟪🟪🟪"
    )
    assert parse_connections(share) == (1248, 8)


def test_parse_connections_with_commentary():
    share = (
        "that purple group was evil\n"
        "Connections\n"
        "Puzzle #1248\n"
        "🟨🟨🟨🟨\n"
        "🟩🟩🟩🟩\n"
        "🟦🟦🟦🟦\n"
        "🟪🟪🟪🟪\n"
        "posting my wordle next"
    )
    assert parse_connections(share) == (1248, 4)


def test_parse_connections_rejects():
    # Sports Edition is a different game.
    assert parse_connections(
        "Connections: Sports Edition\nPuzzle #10\n"
        "🟨🟨🟨🟨\n🟩🟩🟩🟩\n🟦🟦🟦🟦\n🟪🟪🟪🟪"
    ) is None
    # Too few rows is not a finished share.
    assert parse_connections(
        "Connections\nPuzzle #1248\n🟨🟨🟨🟨\n🟩🟩🟩🟩\n🟦🟦🟦🟦"
    ) is None
    # A row that is not exactly 4 squares ends the block early.
    assert parse_connections(
        "Connections\nPuzzle #1248\n🟨🟨🟨\n🟩🟩🟩🟩\n🟦🟦🟦🟦\n🟪🟪🟪🟪"
    ) is None
    assert parse_connections("no puzzle here") is None
    assert parse_connections("") is None


def test_expected_connections_number():
    # Verified against dated answer guides: #367 was 12 June 2024,
    # #1245 was 7 Nov 2026, #1248 was 10 Nov 2026.
    assert expected_connections_number(date(2023, 6, 12)) == 1
    assert expected_connections_number(date(2023, 6, 13)) == 2
    assert expected_connections_number(date(2024, 6, 12)) == 367
    assert expected_connections_number(date(2026, 11, 7)) == 1245
    assert expected_connections_number(date(2026, 11, 10)) == 1248


def test_choose_puzzle():
    assert choose_puzzle({1248, 1200}, 1248) == 1248
    assert choose_puzzle({1200}, 1248) == 1200


def test_pick_winners():
    best, winners = pick_winners({1: (3, "m1"), 2: (5, "m2")})
    assert best == 3
    assert winners == [(1, "m1")]
    # Ties share.
    best, winners = pick_winners({1: (4, "a"), 2: (4, "b"), 3: (6, "c")})
    assert best == 4
    assert sorted(user_id for user_id, _ in winners) == [1, 2]
    # A failed Connections solve (8 rows) loses to any finished solve.
    best, winners = pick_winners({1: (8, "a"), 2: (7, "b")})
    assert best == 7
    assert winners == [(2, "b")]


def test_games_to_run():
    assert games_to_run("wordle") == {"wordle"}
    assert games_to_run("connections") == {"connections"}
    assert games_to_run("both") == {"wordle", "connections"}
    assert games_to_run("") == {"wordle", "connections"}


def test_game_registry():
    from bot import GAMES

    keys = [game.key for game in GAMES]
    assert keys == ["wordle", "connections"]
    assert games_to_run("both") == set(keys)
    wordle, connections = GAMES
    assert wordle.role_id_env == "WORDLE_ROLE_ID"
    assert wordle.role_name_env == "WORDLE_ROLE_NAME"
    assert connections.role_id_env == "CONNECTIONS_ROLE_ID"
    assert connections.role_name_env == "CONNECTIONS_ROLE_NAME"
    for game in GAMES:
        assert callable(game.parse)
        assert callable(game.expected_number)
        assert callable(game.section)
        assert game.default_role_name
        assert game.score_field
        assert game.wins_path.name.endswith("wins.json")
        assert len(game.leaderboard_markers) == 2


def test_already_crowned():
    data = {"winners": [{"date": "2026-11-11", "user_id": 1}]}
    assert already_crowned(data, date(2026, 11, 11)) is True
    assert already_crowned(data, date(2026, 11, 12)) is False
    assert already_crowned({"winners": []}, date(2026, 11, 11)) is False


def test_day_bounds():
    start, end = day_bounds(date(2026, 11, 9))
    assert (end - start).days == 1
    assert start.tzinfo is not None
    assert start.hour == 0 and start.minute == 0


if __name__ == "__main__":
    tests = [
        test_parse_wordle,
        test_expected_wordle_number,
        test_parse_connections_perfect,
        test_parse_connections_with_mistakes,
        test_parse_connections_failed,
        test_parse_connections_with_commentary,
        test_parse_connections_rejects,
        test_expected_connections_number,
        test_choose_puzzle,
        test_pick_winners,
        test_games_to_run,
        test_game_registry,
        test_already_crowned,
        test_day_bounds,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"ALL {len(tests)} TESTS PASSED")
