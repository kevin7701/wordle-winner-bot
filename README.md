# Wordle Winner bot

Once a day, just after midnight Pacific time, this bot scans a Discord channel
for yesterday's Wordle and Connections scores, gives everyone tied at the
best score the prize role, records the win, and announces it in the channel.

- **Wordle:** lowest guess count wins; a failed puzzle (`X/6`) counts as 7.
  Ties share the **Wordle Winner** role, there is no tie-break.
- **Connections:** fewest guesses wins, where guesses is the number of rows
  in the share (4 is a perfect solve, 8 is a failure). Ties share the
  **Connections Connoisseur** role, there is no tie-break.

There is no always-on server. A scheduled GitHub Actions run wakes the bot up,
it does its job, and exits. Stats live in `wins.json` (Wordle) and
`connections-wins.json` (Connections) in this repo.

## Leaderboard

### Wordle

<!-- LEADERBOARD:START -->
| Player | Wins | Current streak |
| --- | ---: | ---: |
| lacasse | 4 | 3 |
| duskyh | 3 | 2 |
| Kev | 3 | 2 |
| bluenuke0 | 1 | 0 |
<!-- LEADERBOARD:END -->

### Connections

<!-- CONNECTIONS_LEADERBOARD:START -->
| Player | Wins | Current streak |
| --- | ---: | ---: |
| lacasse | 4 | 3 |
| Kev | 2 | 1 |
<!-- CONNECTIONS_LEADERBOARD:END -->

## Setup

### 1. Create the bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications),
   create a New Application, and open the **Bot** page.
2. Click **Reset Token** and copy the token. This is the bot's password:
   never commit it, never paste it in chat.
3. On the same page, under **Privileged Gateway Intents**, enable
   **Message Content Intent** and **Server Members Intent**. Without the first
   one the bot cannot read scores; without the second it cannot reliably
   resolve members to give them the role.

### 2. Create the roles and invite the bot

1. In your server, create the roles **Wordle Winner** and
   **Connections Connoisseur** (any color, no special permissions needed).
2. Invite the bot with this link (replace `YOUR_CLIENT_ID` with the
   Application ID from the portal's General Information page):

   `https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=268504064&scope=bot`

   That grants View Channel, Send Messages, Read Message History, and
   Manage Roles.
3. In **Server Settings > Roles**, drag the bot's role **above** both prize
   roles. Discord will not let a bot manage a role ranked above its own,
   and the failure shows up as a confusing 403 error.

### 3. Get the IDs

Turn on Developer Mode in Discord (Settings > Advanced), then right-click
the server, the Wordle channel, and each role, and choose **Copy ID** for each.

### 4. Put the code on GitHub

Push this folder to a repo, then add repository secrets
(Settings > Secrets and variables > Actions):

| Secret | Value |
| --- | --- |
| `DISCORD_TOKEN` | the bot token from step 1 |
| `GUILD_ID` | the server ID |
| `CHANNEL_ID` | the channel ID (Wordle and Connections are posted here) |
| `WORDLE_ROLE_ID` | the Wordle Winner role ID (optional; if empty the bot finds it by name) |
| `CONNECTIONS_ROLE_ID` | the Connections Connoisseur role ID (optional; if empty the bot finds it by name) |

Then go to Settings > Actions > General > Workflow permissions and set
**Read and write permissions**, so the workflow can commit the updated
wins logs and leaderboards back to the repo.

The schedule is `12 8 * * *` (08:05 UTC daily). That is 12:12am PST in
winter and 1:12am PDT in summer. The announcement @mentions the winners but
is sent silently, so the midnight run does not wake anyone up. The script
always looks back at the full previous Pacific day, so daylight saving needs
no handling and the schedule never changes.

### 5. Test it

In the repo's **Actions** tab, run the workflow manually
("Run workflow"). It accepts an optional date to crown a specific day,
which is also how you backfill past winners: run it once per past day.
Tick the dry run box to test without changing anything: the bot still logs
in and scans the channel, but it changes no roles, writes no files, and
sends no announcement, and the log shows what it would have done. Use the
games dropdown to run only Wordle or only Connections, which is handy for
testing one game without touching the other. Check the run log and the
channel to confirm the roles were assigned.

## Running it locally

```bash
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in the values
python bot.py --date 2026-11-09 --dry-run   # prints what would happen, changes nothing
python bot.py --date 2026-11-09             # crowns that day for real
python bot.py --games connections --dry-run  # dry run Connections only
python bot.py --games wordle                 # crown Wordle only, for real
```

With no `--date`, it crowns yesterday (Pacific time).

## Troubleshooting

- Run stuck after "connected to Gateway" with no further logs: older versions
  called `guild.chunk()`, which waits for Discord to send the full member
  list and hangs if the Server Members intent is off in the Developer
  Portal. The current code no longer calls it, so pull the latest code if
  you see this. The script also has a 5-minute timeout, so a stuck run fails
  loudly instead of running until GitHub kills it.
- 403 Forbidden on role changes: the bot's own role must sit above the
  prize role in Server Settings > Roles, and it needs Manage Roles. Check
  the error message for which role it is, and make sure the role ID in
  secrets points at the prize role, not the bot's own role.
- Run fails after 5 minutes: check the logs for the last printed step; the
  scan prints which channel and day it is working on before it starts.

## How it works

- `bot.py` is a one-shot script, not a long-running process. It logs in,
  scans the channel history between Pacific midnight and the next midnight,
  and logs out. One history pass serves every game.
- Games are defined in one place (`GAMES` in `bot.py`): each entry holds
  its parser, puzzle numbering, role, wins log, and announcement section.
  To add a game, add one entry; the client handles the shared scan,
  crowning, and announcement. The `--games` flag and the Actions dropdown
  offer one choice per game, plus `both`.
- Wordle scores are parsed from the share text (`Wordle 1,234 3/6`,
  `X/6` for a failed puzzle). A user posting twice keeps their best score.
  The puzzle number is checked against the expected number for the day
  (Wordle #0 was 19 June 2021), so a late post of an old puzzle does not
  count.
- Connections scores are parsed from the share text (`Connections`,
  `Puzzle #1,248`, then one row of four colored squares per guess). The
  score is the row count: 4 is perfect, 8 is a failure. The puzzle number
  is checked the same way (Connections #1 was 12 June 2023). The Sports
  Edition is ignored.
- Each win appends one record to `wins.json` (Wordle) or
  `connections-wins.json` (Connections): date, puzzle, user ID, display
  name, score, message ID. Standings, current streaks, and best scores are
  computed from those logs, and the README tables above are refreshed on
  every run.
- The two games are crowned independently in the same run and announced in
  one combined, silent message. If one game has no scores that day, its
  section is left out; if a game's role is missing or a role change fails,
  the other game still crowns and the run reports the error.
- Run `python test_bot.py` to check the parsing and scoring logic.
