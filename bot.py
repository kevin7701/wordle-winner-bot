"""Worlde Winner bot.

One-shot script, meant to be run once a day by a scheduler (GitHub Actions).
It wakes up, scans a Discord channel for yesterday's Wordle and Connections
scores (Pacific time), crowns everyone tied at the best score, records the
wins, announces the results, and exits.

Wordle: fewest guesses wins. A failed puzzle ("X/6") counts as 7, so it
always loses to any solved puzzle. Ties share the role, no tie-break.

Connections: fewest guesses wins, where guesses is the number of rows in
the share (4 is a perfect solve, 8 is a failure: 4 mistakes plus the groups
the game reveals). Ties share the role, no tie-break.

Structure: one client owns the shared flow (one login, one channel scan,
one combined announcement). Each game is a `Game` entry in `GAMES` below,
owning only its own parsing, scoring, role, wins log, and announcement
section. To add a game, add one entry; nothing else changes.

Configuration (environment variables; see .env.example):
    DISCORD_TOKEN         bot token from the Discord Developer Portal
    GUILD_ID              server ID
    CHANNEL_ID            Wordle/Connections channel ID
    WORDLE_ROLE_ID        optional; if empty, the Wordle role is found by name
    WORDLE_ROLE_NAME      optional, default "Worlde Winner"
    CONNECTIONS_ROLE_ID   optional; if empty, the Connections role is found by name
    CONNECTIONS_ROLE_NAME optional, default "Connections Connoisseur"

Usage:
    python bot.py                          # crown yesterday (Pacific time)
    python bot.py --date 2026-11-09       # crown a specific day (backfill/test)
    python bot.py --date 2026-11-09 --dry-run   # print what would happen, change nothing
    python bot.py --games connections --dry-run  # dry run Connections only
    python bot.py --games wordle                 # crown Wordle only, for real

Exit codes: 0 success (including "no scores today"), 1 error, 2 bad config.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()

PACIFIC = ZoneInfo("America/Los_Angeles")
BASE_DIR = Path(__file__).resolve().parent
WINS_PATH = BASE_DIR / "wins.json"
CONNECTIONS_WINS_PATH = BASE_DIR / "connections-wins.json"
README_PATH = BASE_DIR / "README.md"

DEFAULT_WORDLE_ROLE_NAME = "Worlde Winner"
DEFAULT_CONNECTIONS_ROLE_NAME = "Connections Connoisseur"
# A normal run takes seconds. Fail loudly instead of hanging forever, so a
# stuck scheduled run shows up as failed instead of burning Actions minutes.
RUN_TIMEOUT_SECONDS = 300

# Matches a Wordle share line, e.g. "Wordle 1,234 3/6" or "Wordle 987 X/6*".
# The puzzle number has a comma in the official share text, hence [\d,]+.
WORDLE_RE = re.compile(r"Wordle\s+([\d,]+)\s+([1-6X])/6\*?")

# Matches the header of a standard Connections share:
#   Connections
#   Puzzle #1,248
# The header line must be exactly "Connections", so the Sports Edition
# ("Connections: Sports Edition") is ignored. Rows follow on the next lines.
CONNECTIONS_HEADER_RE = re.compile(
    r"^Connections[ \t]*\nPuzzle[ \t]*#[ \t]*([\d,]+)[ \t]*$", re.MULTILINE
)
CONNECTIONS_SQUARES = "🟨🟩🟦🟪"

LEADERBOARD_START = "<!-- LEADERBOARD:START -->"
LEADERBOARD_END = "<!-- LEADERBOARD:END -->"
CONNECTIONS_LEADERBOARD_START = "<!-- CONNECTIONS_LEADERBOARD:START -->"
CONNECTIONS_LEADERBOARD_END = "<!-- CONNECTIONS_LEADERBOARD:END -->"


def parse_wordle(text):
    """Return (puzzle_number, score) for a Wordle share, or None.

    Score is the guess count, with a failed puzzle (X/6) counting as 7.
    """
    match = WORDLE_RE.search(text or "")
    if not match:
        return None
    puzzle = int(match.group(1).replace(",", ""))
    result = match.group(2)
    return puzzle, 7 if result == "X" else int(result)


def parse_connections(text):
    """Return (puzzle_number, guesses) for a Connections share, or None.

    Guesses is the number of rows in the share: 4 is a perfect solve, 8 is
    a failure (4 mistakes plus the groups the game reveals). Rows must be
    consecutive lines of exactly 4 colored squares right after the header.
    """
    if not text:
        return None
    match = CONNECTIONS_HEADER_RE.search(text)
    if not match:
        return None
    puzzle = int(match.group(1).replace(",", ""))
    rows = []
    for line in text[match.end() :].lstrip("\r\n").splitlines():
        stripped = line.strip()
        if len(stripped) == 4 and all(ch in CONNECTIONS_SQUARES for ch in stripped):
            rows.append(stripped)
        else:
            break
    if not 4 <= len(rows) <= 8:
        return None
    return puzzle, len(rows)


def expected_puzzle_number(day):
    """Wordle #0 was 19 June 2021; the number increments daily."""
    return (day - date(2021, 6, 19)).days


def expected_connections_number(day):
    """Connections #1 was 12 June 2023; the number increments daily."""
    return (day - date(2023, 6, 12)).days + 1


def day_bounds(day):
    """Start (inclusive) and end (exclusive) of a Pacific-time day, tz-aware."""
    start = datetime.combine(day, time.min, tzinfo=PACIFIC)
    return start, start + timedelta(days=1)


def choose_puzzle(puzzles, expected):
    """The day's puzzle number: the expected one if anyone posted it,
    otherwise the highest number seen (a late post of an old puzzle)."""
    return expected if expected in puzzles else max(puzzles)


def pick_winners(day_results):
    """(best score, [(user_id, message)]) for one day's results.

    day_results maps user_id -> (score, message). Lowest score wins;
    everyone tied at it wins.
    """
    best = min(score for score, _ in day_results.values())
    winners = [
        (user_id, message)
        for user_id, (score, message) in day_results.items()
        if score == best
    ]
    return best, winners


def load_wins(path):
    if not path.exists():
        return {"winners": []}
    return json.loads(path.read_text())


def save_wins(data, path):
    path.write_text(json.dumps(data, indent=2) + "\n")


def previous_holder_ids(data):
    """User IDs currently holding the role, from the most recent crowned day.

    The bot is the only thing that assigns this role, so the last day's
    winners are its holders. This deliberately avoids guild.chunk(), which
    waits for Discord to send the full member list and can hang forever
    when the Server Members intent is off in the Developer Portal.
    """
    if not data["winners"]:
        return set()
    latest = max(entry["date"] for entry in data["winners"])
    return {entry["user_id"] for entry in data["winners"] if entry["date"] == latest}


def standings(data):
    """All-time win counts as [(user_id, wins, display_name)], best first."""
    counts = {}
    names = {}
    for entry in data["winners"]:
        user_id = str(entry["user_id"])
        counts[user_id] = counts.get(user_id, 0) + 1
        names[user_id] = entry.get("display_name", user_id)
    return sorted(
        ((user_id, wins, names[user_id]) for user_id, wins in counts.items()),
        key=lambda item: (-item[1], item[2].lower()),
    )


def current_streak(data, user_id, through):
    """Consecutive winning days ending on `through` (a date)."""
    won_days = {
        entry["date"]
        for entry in data["winners"]
        if str(entry["user_id"]) == str(user_id)
    }
    streak = 0
    day = through
    while day.isoformat() in won_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def render_leaderboard(data, through):
    rows = standings(data)
    if not rows:
        return "_No winners yet._"
    lines = [
        "| Player | Wins | Current streak |",
        "| --- | ---: | ---: |",
    ]
    for user_id, wins, name in rows[:10]:
        lines.append(f"| {name} | {wins} | {current_streak(data, user_id, through)} |")
    return "\n".join(lines)


def update_readme(data, through, start_marker, end_marker):
    """Refresh a leaderboard table in README.md, if the markers exist."""
    if not README_PATH.exists():
        return False
    text = README_PATH.read_text()
    if start_marker not in text or end_marker not in text:
        return False
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker)
    new_text = (
        text[:start] + "\n" + render_leaderboard(data, through) + "\n" + text[end:]
    )
    README_PATH.write_text(new_text)
    return True


def format_standings(data, limit=5):
    lines = []
    for user_id, wins, name in standings(data)[:limit]:
        word = "win" if wins == 1 else "wins"
        lines.append(f"- {name}: {wins} {word}")
    return "\n".join(lines)


def resolve_role(guild, role_id, role_name):
    if role_id:
        return guild.get_role(int(role_id))
    return discord.utils.get(guild.roles, name=role_name)


async def apply_roles(guild, role, holder_ids, winner_ids):
    """Remove the role from its current holders, add it to the winners.

    Returns True on success, False on a 403 (with the cause printed).
    """
    try:
        for user_id in holder_ids:
            member = guild.get_member(user_id)
            if member is None:
                member = await guild.fetch_member(user_id)
            await member.remove_roles(role, reason=f"{role.name}: new day")
        for user_id in winner_ids:
            member = guild.get_member(user_id)
            if member is None:
                member = await guild.fetch_member(user_id)
            await member.add_roles(role, reason=role.name)
    except discord.Forbidden:
        print(
            "ERROR: Discord refused the role change (403). Check that the bot "
            'has the "Manage Roles" permission and that its own role sits '
            f'ABOVE "{role.name}" in Server Settings > Roles.'
        )
        return False
    return True


def wordle_section(target_day, info):
    winners, best, data = info["winners"], info["best"], info["data"]
    mentions = ", ".join(message.author.mention for _, message in winners)
    if len(winners) == 1:
        headline = f"{mentions} takes the crown with {best}/6!"
    else:
        headline = (
            f"{mentions} share the crown with {best}/6! "
            "(Tied scores share the role.)"
        )
    return (
        f"🏆 **Worlde Winner, {target_day:%B %d, %Y}**\n"
        f"{headline}\n\n"
        f"**All-time standings**\n{format_standings(data)}"
    )


def connections_section(target_day, info):
    winners, best, data = info["winners"], info["best"], info["data"]
    mentions = ", ".join(message.author.mention for _, message in winners)
    if len(winners) == 1:
        headline = f"{mentions} takes the crown with {best} guesses!"
    else:
        headline = (
            f"{mentions} share the crown with {best} guesses! "
            "(Tied scores share the role.)"
        )
    if best == 8:
        headline += " (Nobody solved it today.)"
    return (
        f"🧩 **Connections, {target_day:%B %d, %Y}**\n"
        f"{headline}\n\n"
        f"**All-time standings**\n{format_standings(data)}"
    )


@dataclass
class Game:
    """Everything that differs between games.

    The client owns the shared flow: one login, one channel scan, one
    combined announcement. A Game owns only its own parsing, scoring, role,
    wins log, and announcement section.

    To add a game (Strands, Sudoku, ...): write a parse function that turns
    a share's text into (puzzle_number, score) or None (lower score wins),
    an expected_number function for the puzzle of a given day, and a section
    builder for the announcement, then add one Game entry to GAMES below.
    Nothing else changes.
    """

    key: str  # "wordle", used by --games and the Actions dropdown
    name: str  # "Wordle", display name
    parse: Callable[[str], tuple[int, int] | None]
    expected_number: Callable[[date], int]
    role_id_env: str  # env var holding the role ID, e.g. "WORDLE_ROLE_ID"
    role_name_env: str  # env var holding the role name override
    default_role_name: str
    wins_path: Path
    leaderboard_markers: tuple[str, str]
    score_field: str  # field name for the score in the wins log
    section: Callable[[date, dict], str]  # (target_day, info) -> announcement text


GAMES = [
    Game(
        key="wordle",
        name="Wordle",
        parse=parse_wordle,
        expected_number=expected_puzzle_number,
        role_id_env="WORDLE_ROLE_ID",
        role_name_env="WORDLE_ROLE_NAME",
        default_role_name=DEFAULT_WORDLE_ROLE_NAME,
        wins_path=WINS_PATH,
        leaderboard_markers=(LEADERBOARD_START, LEADERBOARD_END),
        score_field="score",
        section=wordle_section,
    ),
    Game(
        key="connections",
        name="Connections",
        parse=parse_connections,
        expected_number=expected_connections_number,
        role_id_env="CONNECTIONS_ROLE_ID",
        role_name_env="CONNECTIONS_ROLE_NAME",
        default_role_name=DEFAULT_CONNECTIONS_ROLE_NAME,
        wins_path=CONNECTIONS_WINS_PATH,
        leaderboard_markers=(
            CONNECTIONS_LEADERBOARD_START,
            CONNECTIONS_LEADERBOARD_END,
        ),
        score_field="guesses",
        section=connections_section,
    ),
]

GAME_KEYS = [game.key for game in GAMES]


def games_to_run(games):
    """Set of game keys to run for a --games value."""
    if games in GAME_KEYS:
        return {games}
    return set(GAME_KEYS)


class WorldeBot(discord.Client):
    def __init__(self, *, target_day, dry_run, games="both"):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.target_day = target_day
        self.dry_run = dry_run
        self.games = games_to_run(games)
        self.exit_code = 0

    async def on_ready(self):
        try:
            self.exit_code = await asyncio.wait_for(
                self.crown(), timeout=RUN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            print(
                f"ERROR: timed out after {RUN_TIMEOUT_SECONDS // 60} minutes. "
                "The run was stuck; see the last printed step above."
            )
            self.exit_code = 1
        except Exception:
            traceback.print_exc()
            self.exit_code = 1
        finally:
            await self.close()

    async def crown_game(self, guild, game, results):
        """Crown one game. Returns (info dict or None, had_error bool).

        info has winners [(user_id, message)], best score, and the updated
        wins data, for the announcement. A game with no scores, or in dry
        run mode, returns (None, False) after printing what happened.
        """
        role = resolve_role(
            guild,
            os.environ.get(game.role_id_env) or "",
            os.environ.get(game.role_name_env) or game.default_role_name,
        )
        if role is None:
            print(
                f'ERROR: could not find role "{game.default_role_name}". '
                "Create it first."
            )
            return None, True
        if not results:
            print(
                f"No {game.name} scores found for {self.target_day}. "
                "Nothing to crown."
            )
            return None, False

        expected = game.expected_number(self.target_day)
        puzzles = {puzzle for puzzle, _ in results}
        puzzle = choose_puzzle(puzzles, expected)
        day_results = {
            user_id: (score, message)
            for (result_puzzle, user_id), (score, message) in results.items()
            if result_puzzle == puzzle
        }
        best, winners = pick_winners(day_results)
        winner_names = ", ".join(
            message.author.display_name for _, message in winners
        )
        print(
            f"{game.name} {self.target_day} (#{puzzle}): best score {best}, "
            f"winner(s): {winner_names}"
        )

        if self.dry_run:
            return None, False

        data = load_wins(game.wins_path)

        # Remove the role from its current holders, then crown today's winners.
        # Holders come from the wins log (the bot is the only thing that
        # assigns this role), plus anyone in role.members if the cache has
        # them. Nothing here needs the full member list.
        holder_ids = previous_holder_ids(data)
        holder_ids.update(member.id for member in role.members)
        ok = await apply_roles(
            guild, role, holder_ids, [user_id for user_id, _ in winners]
        )
        if not ok:
            return None, True

        for user_id, message in winners:
            data["winners"].append(
                {
                    "date": self.target_day.isoformat(),
                    "puzzle": puzzle,
                    "user_id": user_id,
                    "display_name": message.author.display_name,
                    game.score_field: best,
                    "message_id": message.id,
                }
            )
        save_wins(data, game.wins_path)
        update_readme(data, self.target_day, *game.leaderboard_markers)
        return {"winners": winners, "best": best, "data": data}, False

    async def crown(self):
        """Do the day's crowning for the selected games. Returns an exit code."""
        guild_id = int(os.environ["GUILD_ID"])
        channel_id = int(os.environ["CHANNEL_ID"])

        guild = self.get_guild(guild_id)
        if guild is None:
            print(f"ERROR: could not find server {guild_id}. Is the bot in it?")
            return 1

        channel = guild.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)

        selected = [game for game in GAMES if game.key in self.games]
        start, end = day_bounds(self.target_day)
        expected_bits = ", ".join(
            f"{game.name} #{game.expected_number(self.target_day)}"
            for game in selected
        )
        print(
            f"Scanning #{channel.name} for {self.target_day} ({expected_bits})..."
        )

        # Best score per (puzzle, user), per game. A user posting twice
        # keeps their best. One history pass serves every game.
        results = {game.key: {} for game in selected}
        async for message in channel.history(
            after=start, before=end, oldest_first=True
        ):
            if message.author.bot:
                continue
            for game in selected:
                parsed = game.parse(message.content)
                if parsed is not None:
                    puzzle, score = parsed
                    key = (puzzle, message.author.id)
                    if key not in results[game.key] or score < results[game.key][key][0]:
                        results[game.key][key] = (score, message)

        errors = False
        sections = []
        for game in selected:
            info, error = await self.crown_game(guild, game, results[game.key])
            errors = errors or error
            if info is not None:
                sections.append(game.section(self.target_day, info))

        if self.dry_run:
            print("Dry run: no roles changed, no files written, no announcement sent.")
            return 1 if errors else 0

        if not sections:
            print("No scores found today. Nothing to announce.")
            return 1 if errors else 0

        announcement = "\n\n".join(sections)
        # silent=True keeps the @mentions visible but sends no push or
        # desktop notification, so nobody gets woken up by the announcement.
        await channel.send(announcement, silent=True)
        print("Crowned and announced (silent, no ping).")
        return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description="Crown the daily Worlde Winner.")
    parser.add_argument(
        "--date",
        help="Target day as YYYY-MM-DD (default: yesterday, Pacific time)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without changing roles, files, or messages",
    )
    parser.add_argument(
        "--games",
        choices=[*GAME_KEYS, "both"],
        default="both",
        help="Which games to crown (default: both)",
    )
    args = parser.parse_args()

    if args.date:
        target_day = date.fromisoformat(args.date)
    else:
        target_day = datetime.now(PACIFIC).date() - timedelta(days=1)

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in,")
        print("or set the DISCORD_TOKEN, GUILD_ID, and CHANNEL_ID secrets in GitHub.")
        return 2
    for variable in ("GUILD_ID", "CHANNEL_ID"):
        if not os.environ.get(variable):
            print(f"{variable} is not set. See .env.example.")
            return 2

    bot = WorldeBot(target_day=target_day, dry_run=args.dry_run, games=args.games)
    bot.run(token)
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
