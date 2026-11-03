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
# TEMP test mode: --alert-all ignores the win%/gap/window filters and posts every
# live game to Discord on every refresh (no de-duplication).
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
        if r["_winPct"] is not None:
            print(f"  Win    {r['_winPct']:.0%}")
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
# A game qualifies only when the leading team is a clear favorite
# (win% >= MIN_WIN_PCT) AND we're in the end-game window:
#   - 3rd or 4th quarter only (never Q1/Q2 or OT)
#   - in the 4th, stop once fewer than Q4_CUTOFF_MIN minutes remain
# Given those gates, the GAP (how far the live pace projects above the
# season-pace baseline) decides the tier:
#   Lean       gap >= 5
#   Strong     gap >= 8
#   SMASH 💥   gap >= 12
MIN_WIN_PCT = 0.88      # leading team's ESPN live win probability
Q4_CUTOFF_MIN = 5       # no new alerts inside the last 5 min of Q4
GAP_TIERS = [("Smash", 12), ("Strong", 8), ("Lean", 5)]  # checked high -> low
TIER_RANK = {"Info": 0, "Lean": 1, "Strong": 2, "Smash": 3}
TIER_LABEL = {"Info": "📊 UPDATE (test)", "Lean": "Lean", "Strong": "Strong", "Smash": "SMASH 💥"}
TIER_COLOR = {"Info": 0x95A5A6, "Lean": 0xF1C40F, "Strong": 0xE67E22, "Smash": 0xE03C31}


def under_tier(gap, win_pct, period, min_remaining):
    """Return '', 'Lean', 'Strong', or 'Smash'."""
    if period not in (3, 4):                       # Q3/Q4 only
        return ""
    if period == 4 and min_remaining < Q4_CUTOFF_MIN:
        return ""
    if win_pct is None or win_pct < MIN_WIN_PCT:   # must be a decided game
        return ""
    for name, threshold in GAP_TIERS:
        if gap >= threshold:
            return name
    return ""


# ── Live win probability (ESPN) ───────────────────────────────────
# nba_api has no win probability, so we match each live game to its ESPN
# event by team tricodes and read ESPN's live model. Reuses the fetch
# script's abbreviation fixups so ESPN tricodes line up with nba_api's.
ESPN_FIX = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"


def espn_event_map():
    """{frozenset(away_tri, home_tri): (event_id, home_tri)} for today's games."""
    out = {}
    try:
        events = requests.get(ESPN_SCOREBOARD, timeout=10).json().get("events", [])
    except Exception as e:
        print(colored(f"⚠️  ESPN scoreboard fetch failed: {e}", "yellow"))
        return out
    for e in events:
        try:
            comp = e["competitions"][0]["competitors"]
            tri = {c["homeAway"]: ESPN_FIX.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
                   for c in comp}
            out[frozenset((tri["home"], tri["away"]))] = (e["id"], tri["home"])
        except (KeyError, IndexError):
            continue
    return out


def leading_team_win_pct(away_tri, away_score, home_tri, home_score, event_map):
    """ESPN live win% for whichever team is currently ahead, or None."""
    hit = event_map.get(frozenset((away_tri, home_tri)))
    if not hit:
        return None
    event_id, espn_home = hit
    try:
        wp = requests.get(ESPN_SUMMARY, params={"event": event_id}, timeout=10).json().get("winprobability", [])
        if not wp:
            return None
        last = wp[-1]
        home_pct = last.get("homeWinPercentage")
        tie = last.get("tiePercentage") or 0
        if home_pct is None:
            return None
    except Exception:
        return None
    # winprobability is keyed to ESPN's home team; align to our home tricode
    if espn_home != home_tri:
        home_pct = 1 - home_pct - tie
    away_pct = 1 - home_pct - tie
    return home_pct if home_score >= away_score else away_pct


# game_id -> highest tier already alerted; survives restarts.
# A game re-alerts only when it upgrades (Lean -> Strong -> Smash).
ALERTED_FILE = ".alerted_games.json"
try:
    alerted_games = json.load(open(ALERTED_FILE))
    if not isinstance(alerted_games, dict):  # old format: list of ids
        alerted_games = {g: "Strong" for g in alerted_games}
except (OSError, ValueError):
    alerted_games = {}


def send_discord_alert(game_id, tier, away, home, clock, mins_left, total, cur_proj, season_proj, win_pct, gap):
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
                {"name": "Win%", "value": f"{win_pct:.0%}" if win_pct is not None else "—", "inline": True},
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


HAS_DEFENSE = "Allowed" in allTeamsDF.columns
if not HAS_DEFENSE:
    print(colored("ℹ️  2025teamdata.csv has no 'Allowed' column — run fetch_team_avgs.py "
                  "for defense-adjusted projections (using points scored only for now)", "yellow"))


def expected_ppg(team_df, opp_df):
    """Blend a team's scoring with the opponent's defense (points allowed)."""
    scored = team_df['Score'].iloc[0]
    if HAS_DEFENSE:
        return (scored + opp_df['Allowed'].iloc[0]) / 2
    return scored


while True:
    board = scoreboard.ScoreBoard()
    games = board.games.get_dict()
    filtered_games = [game for game in games if game["gameStatus"] == 2]  # only live games

    event_map = espn_event_map() if filtered_games else {}
    game_rows = []

    for game in filtered_games:
        game_id = game['gameId']
        homeTeamName = game['homeTeam']['teamTricode']
        awayTeamName = game['awayTeam']['teamTricode']

        # Fetch team averages (offense blended with opponent's defense)
        homeTeamDF = allTeamsDF[allTeamsDF['Team'] == homeTeamName]
        awayTeamDF = allTeamsDF[allTeamsDF['Team'] == awayTeamName]
        homeAvgSeasonPPM = expected_ppg(homeTeamDF, awayTeamDF) / 48
        awayAvgSeasonPPM = expected_ppg(awayTeamDF, homeTeamDF) / 48
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
        period = boxScore['period']

        # Win probability only matters in the Q3/Q4 window — skip the extra
        # ESPN call (and the whole lookup) outside it.
        in_window = period in (3, 4) and not (period == 4 and minRemaining < Q4_CUTOFF_MIN)
        if in_window:
            winPct = leading_team_win_pct(
                awayTeamName, awayTeamCurrentTotal, homeTeamName, homeTeamCurrentTotal, event_map
            )
        else:
            winPct = None

        tier = under_tier(projected_diff, winPct, period, minRemaining)
        if ALERT_ALL and not tier:
            tier = "Info"
        if tier:
            under_indicator = TIER_LABEL[tier] if tier == "Info" else f"UNDER · {TIER_LABEL[tier]}"
            send_discord_alert(
                game_id, tier,
                f"{awayTeamName} {awayTeamCurrentTotal}", f"{homeTeamName} {homeTeamCurrentTotal}",
                game_clock_label(period, minutes, seconds), minRemaining,
                gameTotal, currProjectedTotal, seasonProjectedTotal, winPct, projected_diff,
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
            "_clock": game_clock_label(period, minutes, seconds),
            "_seasonPPM": round(gameSeasonPPM, 2),
            "_currPPM": currPPM,
            "_winPct": winPct,
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
