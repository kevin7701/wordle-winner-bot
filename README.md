# Worlde Winner bot

Once a day, just after midnight Pacific time, this bot scans a Discord channel
for yesterday's Wordle scores, gives everyone tied at the best score the
**Worlde Winner** role, records the win, and announces it in the channel.
Lowest guess count wins; a failed puzzle (`X/6`) counts as 7. Ties share the
role, there is no tie-break.

There is no always-on server. A scheduled GitHub Actions run wakes the bot up,
it does its job, and exits. Stats live in `wins.json` in this repo.

## Leaderboard

<!-- LEADERBOARD:START -->
| Player | Wins | Current streak |
| --- | ---: | ---: |
| lacasse | 1 | 1 |
<!-- LEADERBOARD:END -->

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

### 2. Create the role and invite the bot

1. In your server, create the role **Worlde Winner** (any color, no special
   permissions needed).
2. Invite the bot with this link (replace `YOUR_CLIENT_ID` with the
   Application ID from the portal's General Information page):

   `https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=268504064&scope=bot`

   That grants View Channel, Send Messages, Read Message History, and
   Manage Roles.
3. In **Server Settings > Roles**, drag the bot's role **above** the
   Worlde Winner role. Discord will not let a bot manage a role ranked
   above its own, and the failure shows up as a confusing 403 error.

### 3. Get the IDs

Turn on Developer Mode in Discord (Settings > Advanced), then right-click
the server, the Wordle channel, and the role, and choose **Copy ID** for each.

### 4. Put the code on GitHub

Push this folder to a repo, then add repository secrets
(Settings > Secrets and variables > Actions):

| Secret | Value |
| --- | --- |
| `DISCORD_TOKEN` | the bot token from step 1 |
| `GUILD_ID` | the server ID |
| `CHANNEL_ID` | the Wordle channel ID |
| `ROLE_ID` | the role ID (optional; if empty the bot finds it by name) |

Then go to Settings > Actions > General > Workflow permissions and set
**Read and write permissions**, so the workflow can commit the updated
`wins.json` and leaderboard back to the repo.

The schedule is `5 8 * * *` (08:05 UTC daily). That is 12:05am PST in
winter and 1:05am PDT in summer. The announcement @mentions the winners but
is sent silently, so the midnight run does not wake anyone up. The script
always looks back at the full previous Pacific day, so daylight saving needs
no handling and the schedule never changes.

### 5. Test it

In the repo's **Actions** tab, run the workflow manually
("Run workflow"). It accepts an optional date to crown a specific day,
which is also how you backfill past winners: run it once per past day.
Tick the dry run box to test without changing anything: the bot still logs
in and scans the channel, but it changes no roles, writes no files, and
sends no announcement, and the log shows what it would have done. Check
the run log and the channel to confirm the role was assigned.

## Running it locally

```bash
python -m venv venv
source venv/bin/activate        # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # fill in the values
python bot.py --date 2026-11-09 --dry-run   # prints what would happen, changes nothing
python bot.py --date 2026-11-09             # crowns that day for real
```

With no `--date`, it crowns yesterday (Pacific time).

## Troubleshooting

- Run stuck after "connected to Gateway" with no further logs: older versions
  called `guild.chunk()`, which waits for Discord to send the full member
  list and hangs if the Server Members intent is off in the Developer
  Portal. The current code no longer calls it, so pull the latest code if
  you see this. The script also has a 5-minute timeout, so a stuck run fails
  loudly instead of running until GitHub kills it.
- 403 Forbidden on role changes: the bot's own role must sit above
  "Worlde Winner" in Server Settings > Roles, and it needs Manage Roles.
- Run fails after 5 minutes: check the logs for the last printed step; the
  scan prints which channel and day it is working on before it starts.

## How it works

- `bot.py` is a one-shot script, not a long-running process. It logs in,
  scans the channel history between Pacific midnight and the next midnight,
  and logs out.
- Scores are parsed from the Wordle share text (`Wordle 1,234 3/6`,
  `X/6` for a failed puzzle). A user posting twice keeps their best score.
- The winner's puzzle number is checked against the expected number for the
  day (Wordle #0 was 19 June 2021), so a late post of an old puzzle does
  not count.
- Each win appends one record to `wins.json` (date, puzzle, user ID,
  display name, score, message ID). Standings, current streaks, and best
  scores are computed from that log, and the README table above is
  refreshed on every run.
