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
            print(colored(f"  🔻 UNDER  ·  conf {r['Confidence']}", "red", attrs=["bold"]))


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

        # Base criteria for "Under"
        if projected_diff > 10 and score_diff > 15:
            under_indicator = "🔻 UNDER CANDIDATE"
            confidence = min(100, round((projected_diff + score_diff) / 2, 1))
        else:
            under_indicator = ""
            confidence = ""

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
            "Confidence": confidence,
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
