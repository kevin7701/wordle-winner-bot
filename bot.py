"""Worlde Winner bot.

One-shot script, meant to be run once a day by a scheduler (GitHub Actions).
It wakes up, scans a Discord channel for yesterday's Wordle scores (Pacific
time), crowns everyone tied at the best score with the "Worlde Winner" role,
records the win in wins.json, announces the result, and exits.

Scoring: fewest guesses wins. A failed puzzle ("X/6") counts as 7, so it
always loses to any solved puzzle. Ties share the role, no tie-break.

Configuration (environment variables; see .env.example):
    DISCORD_TOKEN   bot token from the Discord Developer Portal
    GUILD_ID        server ID
    CHANNEL_ID      Wordle channel ID
    ROLE_ID         optional; if empty, the role is found by name
    ROLE_NAME       optional, default "Worlde Winner"

Usage:
    python bot.py                          # crown yesterday (Pacific time)
    python bot.py --date 2026-11-09       # crown a specific day (backfill/test)
    python bot.py --date 2026-11-09 --dry-run   # print what would happen, change nothing
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()

PACIFIC = ZoneInfo("America/Los_Angeles")
BASE_DIR = Path(__file__).resolve().parent
WINS_PATH = BASE_DIR / "wins.json"
README_PATH = BASE_DIR / "README.md"

DEFAULT_ROLE_NAME = "Worlde Winner"

# Matches a Wordle share line, e.g. "Wordle 1,234 3/6" or "Wordle 987 X/6*".
# The puzzle number has a comma in the official share text, hence [\d,]+.
WORDLE_RE = re.compile(r"Wordle\s+([\d,]+)\s+([1-6X])/6\*?")

LEADERBOARD_START = "<!-- LEADERBOARD:START -->"
LEADERBOARD_END = "<!-- LEADERBOARD:END -->"


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


def expected_puzzle_number(day):
    """Wordle #0 was 19 June 2021; the number increments daily."""
    return (day - date(2021, 6, 19)).days


def day_bounds(day):
    """Start (inclusive) and end (exclusive) of a Pacific-time day, tz-aware."""
    start = datetime.combine(day, time.min, tzinfo=PACIFIC)
    return start, start + timedelta(days=1)


def load_wins():
    if not WINS_PATH.exists():
        return {"winners": []}
    return json.loads(WINS_PATH.read_text())


def save_wins(data):
    WINS_PATH.write_text(json.dumps(data, indent=2) + "\n")


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


def update_readme(data, through):
    """Refresh the leaderboard table in README.md, if the markers exist."""
    if not README_PATH.exists():
        return False
    text = README_PATH.read_text()
    if LEADERBOARD_START not in text or LEADERBOARD_END not in text:
        return False
    start = text.index(LEADERBOARD_START) + len(LEADERBOARD_START)
    end = text.index(LEADERBOARD_END)
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


class WorldeBot(discord.Client):
    def __init__(self, *, target_day, dry_run):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.target_day = target_day
        self.dry_run = dry_run

    async def on_ready(self):
        try:
            await self.crown()
        finally:
            await self.close()

    async def crown(self):
        guild_id = int(os.environ["GUILD_ID"])
        channel_id = int(os.environ["CHANNEL_ID"])
        role_id = os.environ.get("ROLE_ID") or ""
        role_name = os.environ.get("ROLE_NAME") or DEFAULT_ROLE_NAME

        guild = self.get_guild(guild_id)
        if guild is None:
            print(f"Could not find server {guild_id}. Is the bot in it?")
            raise SystemExit(1)
        # Make sure the member cache is populated so role.members is accurate.
        await guild.chunk()

        channel = guild.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)

        if role_id:
            role = guild.get_role(int(role_id))
        else:
            role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            print(f'Could not find role "{role_name}". Create it first.')
            raise SystemExit(1)

        start, end = day_bounds(self.target_day)
        expected = expected_puzzle_number(self.target_day)

        # Best score per (puzzle, user). A user posting twice keeps their best.
        results = {}
        async for message in channel.history(
            after=start, before=end, oldest_first=True
        ):
            if message.author.bot:
                continue
            parsed = parse_wordle(message.content)
            if parsed is None:
                continue
            puzzle, score = parsed
            key = (puzzle, message.author.id)
            if key not in results or score < results[key][0]:
                results[key] = (score, message)

        if not results:
            print(f"No Wordle scores found for {self.target_day}. Nothing to crown.")
            return

        puzzles = {puzzle for puzzle, _ in results}
        puzzle = expected if expected in puzzles else max(puzzles)
        day_results = {
            user_id: (score, message)
            for (result_puzzle, user_id), (score, message) in results.items()
            if result_puzzle == puzzle
        }
        best = min(score for score, _ in day_results.values())
        winners = [
            (user_id, message)
            for user_id, (score, message) in day_results.items()
            if score == best
        ]

        winner_names = ", ".join(
            message.author.display_name for _, message in winners
        )
        print(
            f"{self.target_day} (Wordle #{puzzle}): best score {best}/6, "
            f"winner(s): {winner_names}"
        )

        if self.dry_run:
            print("Dry run: no roles changed, no files written, no announcement sent.")
            return

        # Remove the role from yesterday's holders, then crown today's winners.
        try:
            for member in list(role.members):
                await member.remove_roles(role, reason="Worlde Winner: new day")
            for user_id, _ in winners:
                member = guild.get_member(user_id)
                if member is None:
                    member = await guild.fetch_member(user_id)
                await member.add_roles(role, reason="Worlde Winner")
        except discord.Forbidden:
            print(
                "Discord refused the role change (403). Check that the bot has the "
                '"Manage Roles" permission and that its own role sits ABOVE '
                f'"{role.name}" in Server Settings > Roles.'
            )
            raise SystemExit(1)

        data = load_wins()
        for user_id, message in winners:
            data["winners"].append(
                {
                    "date": self.target_day.isoformat(),
                    "puzzle": puzzle,
                    "user_id": user_id,
                    "display_name": message.author.display_name,
                    "score": best,
                    "message_id": message.id,
                }
            )
        save_wins(data)
        update_readme(data, self.target_day)

        mentions = ", ".join(message.author.mention for _, message in winners)
        if len(winners) == 1:
            headline = f"{mentions} takes the crown with {best}/6!"
        else:
            headline = (
                f"{mentions} share the crown with {best}/6! "
                "(Tied scores share the role.)"
            )
        announcement = (
            f"\U0001f3c6 **Worlde Winner, {self.target_day:%B %d, %Y}**\n"
            f"{headline}\n\n"
            f"**All-time standings**\n{format_standings(data)}"
        )
        await channel.send(announcement)
        print("Crowned and announced.")


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

    bot = WorldeBot(target_day=target_day, dry_run=args.dry_run)
    bot.run(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
