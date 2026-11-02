import time
import math
import pandas as pd
from nba_api.live.nba.endpoints import scoreboard, boxscore
from termcolor import colored  # pip install termcolor
from tabulate import tabulate  # pip install tabulate

# Display settings
pd.set_option('display.max_columns', None)
pd.set_option('display.expand_frame_repr', False)

# Step 1: Load team data
file_path = "2025teamdata.csv"
allTeamsDF = pd.read_csv(file_path)

def colorize_projection(row):
    """Color code the Game Projected column relative to Season Projected."""
    try:
        gp = int(str(row["Current Proj"]).split()[0])  # remove color codes
        sp = int(str(row["Season Proj"]))
    except:
        return row["Current Proj"]

    if gp > sp:
        return colored(gp, "green")
    elif gp < sp:
        return colored(gp, "red")
    else:
        return colored(gp, "yellow")

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Default output is a narrow card per game (fits a phone screen).
# Run with --wide for the original desktop table.
WIDE = "--wide" in sys.argv
# TEMP test mode: --alert-all ignores lead/gap filters and posts every live game
# to Discord on every refresh (no de-duplication).
ALERT_ALL = "--alert-all" in sys.argv


def game_clock_label(period, minutes, seconds):
    """'Q3 4:07', 'HALF', 'END Q1', or 'OT1 2:10'."""
    if minutes == 0 and seconds == 0:
        return "HALF" if period == 2 else f"END Q{period}" if period <= 4 else f"END OT{period - 4}"
    q = f"Q{period}" if period <= 4 else f"OT{period - 4}"
    return f"{q} {minutes}:{seconds:02d}"


def print_mobile(df):
    """One compact card per game, ~32 chars wide."""
    print(colored(f"🏀 LIVE  {datetime.now(ZoneInfo('America/New_York')):%-I:%M %p} ET", attrs=["bold"]))
    for _, r in df.iterrows():
        cur, sea = r["Current Proj"], r["Season Proj"]
        if cur > sea:
            arrow, color = "▲", "green"
        elif cur < sea:
            arrow, color = "▼", "red"
        else:
            arrow, color = "=", "yellow"
        print()
        print(colored(f"{r['_away']} @ {r['_home']}", attrs=["bold"]))
        print(f"  {r['_clock']}  ·  {r['Mins Left']:.1f}m left")
        print(f"  Total  {r['Current Total']}")
        print(f"  Proj   " + colored(f"{cur} {arrow}", color, attrs=["bold"]) + f"  (season {sea})")
        print(f"  PPM    {r['_currPPM']} now · {r['_seasonPPM']} avg")
        if r["BET"]:
            print(colored(f"  {r['BET']}", "red", attrs=["bold"]))


import os
import json
import requests


# ── Discord alerts ────────────────────────────────────────────────
# Set DISCORD_WEBHOOK_URL in the environment or in a local .env file
# (.env is gitignored — never commit the webhook URL).
def _load_dotenv(path=".env"):
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


_load_dotenv()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
# ── Under tiers ───────────────────────────────────────────────────
# gap  = current-pace projection − season-pace projection
# lead = absolute score difference (must be >= MIN_LEAD for any tier)
#   Lean       gap >= 5   (3rd quarter)
#   Strong     gap >= 5 in the 4th quarter, or gap >= 10 any time
#   SMASH 💥   gap >= 15  (any time)
MIN_LEAD = 12
TIER_RANK = {"Info": 0, "Lean": 1, "Strong": 2, "Smash": 3}
TIER_LABEL = {"Info": "📊 UPDATE (test)", "Lean": "Lean", "Strong": "Strong", "Smash": "SMASH 💥"}
TIER_COLOR = {"Info": 0x95A5A6, "Lean": 0xF1C40F, "Strong": 0xE67E22, "Smash": 0xE03C31}


def under_tier(gap, lead, period):
    if lead < MIN_LEAD:
        return ""
    if gap >= 15:
        return "Smash"
    if gap >= 10 or (gap >= 5 and period == 4):
        return "Strong"
    if gap >= 5 and period == 3:
        return "Lean"
    return ""


# game_id -> highest tier already alerted; survives restarts.
# A game re-alerts only when it upgrades (Lean -> Strong -> Smash).
ALERTED_FILE = ".alerted_games.json"
try:
    alerted_games = json.load(open(ALERTED_FILE))
    if not isinstance(alerted_games, dict):  # old format: list of ids
        alerted_games = {g: "Strong" for g in alerted_games}
except (OSError, ValueError):
    alerted_games = {}


def send_discord_alert(game_id, tier, away, home, clock, mins_left, total, cur_proj, season_proj, lead, gap):
    """Post to Discord when a game first reaches a tier, or upgrades to a higher one."""
    prev = alerted_games.get(game_id)
    if not DISCORD_WEBHOOK_URL:
        return
    if not ALERT_ALL and prev and TIER_RANK[tier] <= TIER_RANK[prev]:
        return
    payload = {
        "username": "NBA Live",
        "embeds": [{
            "title": f"UNDER {TIER_LABEL[tier]} — {away} @ {home}" if tier != "Info" else f"{TIER_LABEL[tier]} — {away} @ {home}",
            "description": f"Upgraded from {prev}" if prev and not ALERT_ALL else None,
            "color": TIER_COLOR[tier],
            "fields": [
                {"name": "Clock", "value": f"{clock} ({mins_left:.1f}m left)", "inline": True},
                {"name": "Total", "value": str(total), "inline": True},
                {"name": "Lead", "value": str(lead), "inline": True},
                {"name": "Gap", "value": f"{gap:+d}", "inline": True},
                {"name": "Proj (curr / season)", "value": f"{cur_proj} / {season_proj}", "inline": True},
            ],
            "timestamp": datetime.now(ZoneInfo("UTC")).isoformat(),
        }],
    }
    if not payload["embeds"][0]["description"]:
        del payload["embeds"][0]["description"]
    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        r.raise_for_status()
        if not ALERT_ALL:  # test mode must not affect real de-duplication
            alerted_games[game_id] = tier
        json.dump(alerted_games, open(ALERTED_FILE, "w"))
        print(colored(f"📣 Discord alert sent: {tier} — {away} @ {home}", "magenta"))
    except requests.RequestException as e:
        print(colored(f"⚠️  Discord alert failed: {e}", "yellow"))


if not DISCORD_WEBHOOK_URL:
    print(colored("ℹ️  DISCORD_WEBHOOK_URL not set — Discord alerts off", "yellow"))


while True:
    board = scoreboard.ScoreBoard()
    games = board.games.get_dict()
    filtered_games = [game for game in games if game["gameStatus"] == 2]  # only live games

    game_rows = []

    for game in filtered_games:
        game_id = game['gameId']
        homeTeamName = game['homeTeam']['teamTricode']
        awayTeamName = game['awayTeam']['teamTricode']

        # Fetch team averages
        homeTeamDF = allTeamsDF[allTeamsDF['Team'] == homeTeamName]
        awayTeamDF = allTeamsDF[allTeamsDF['Team'] == awayTeamName]
        homeAvgSeasonPPM = homeTeamDF['Score'].iloc[0] / 48
        awayAvgSeasonPPM = awayTeamDF['Score'].iloc[0] / 48
        gameSeasonPPM = homeAvgSeasonPPM + awayAvgSeasonPPM

        # Live box score
        boxScore = boxscore.BoxScore(game_id).game.get_dict()
        gameClock = boxScore['gameClock']
        minutes_str, seconds_str = gameClock[2:-1].split('M')
        minutes = int(minutes_str)
        seconds = int(seconds_str.split('.')[0])
        minRemaining = (4 - boxScore['period']) * 12 + minutes + seconds / 60

        # Current totals
        homeTeamCurrentTotal = boxScore['homeTeam']['score']
        awayTeamCurrentTotal = boxScore['awayTeam']['score']
        gameTotal = homeTeamCurrentTotal + awayTeamCurrentTotal

        currPPM = round(gameTotal / (48 - minRemaining), 2)
        seasonProjectedTotal = math.ceil(
            gameTotal + (homeAvgSeasonPPM * minRemaining) + (awayAvgSeasonPPM * minRemaining)
        )
        currProjectedTotal = math.ceil(gameTotal + currPPM * minRemaining)

        # 🧮 Under Bet Logic
        projected_diff = currProjectedTotal - seasonProjectedTotal
        score_diff = abs(homeTeamCurrentTotal - awayTeamCurrentTotal)

        # Under tiers (lead must be >= MIN_LEAD for any tier)
        tier = under_tier(projected_diff, score_diff, boxScore['period'])
        if ALERT_ALL and not tier:
            tier = "Info"
        if tier:
            under_indicator = TIER_LABEL[tier] if tier == "Info" else f"UNDER · {TIER_LABEL[tier]}"
            send_discord_alert(
                game_id, tier,
                f"{awayTeamName} {awayTeamCurrentTotal}", f"{homeTeamName} {homeTeamCurrentTotal}",
                game_clock_label(boxScore['period'], minutes, seconds), minRemaining,
                gameTotal, currProjectedTotal, seasonProjectedTotal, score_diff, projected_diff,
            )
        else:
            under_indicator = ""

        # 🆕 Updated Matchup field to include scores
        matchup_display = f"{awayTeamName} ({awayTeamCurrentTotal}) vs {homeTeamName} ({homeTeamCurrentTotal})"

        # Add row
        game_rows.append({
            "Matchup": matchup_display,
            "Mins Left": round(minRemaining, 2),
            "Season Proj": seasonProjectedTotal,
            "Current Proj": currProjectedTotal,
            "Current Total": gameTotal,
            "PPM (Season : Curr)": f"{round(gameSeasonPPM, 2)} : {currPPM}",
            "BET": under_indicator,
            # extra fields for the mobile card view
            "_away": f"{awayTeamName} {awayTeamCurrentTotal}",
            "_home": f"{homeTeamName} {homeTeamCurrentTotal}",
            "_clock": game_clock_label(boxScore['period'], minutes, seconds),
            "_seasonPPM": round(gameSeasonPPM, 2),
            "_currPPM": currPPM,
        })

    # Convert to DataFrame
    results_df = pd.DataFrame(game_rows)

    if not results_df.empty:
        # Sort by minutes remaining (soonest ending first)
        results_df = results_df.sort_values(by="Mins Left")

        if WIDE:
            # Original desktop table
            results_df["Current Proj"] = results_df.apply(colorize_projection, axis=1)
            table_df = results_df[[c for c in results_df.columns if not c.startswith("_")]]
            print(tabulate(table_df, headers="keys", tablefmt="github", showindex=False))
        else:
            print_mobile(results_df)
    else:
        print("No live games currently in progress.")

    if WIDE:
        print("\n" + "*" * 140 + "\n")
    else:
        print(colored("─" * 32, "dark_grey") + "\n")

    time.sleep(30)
