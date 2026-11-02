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
            "Confidence": confidence
        })

    # Convert to DataFrame
    results_df = pd.DataFrame(game_rows)

    if not results_df.empty:
        # Sort by minutes remaining (soonest ending first)
        results_df = results_df.sort_values(by="Mins Left")

        # Colorize Game Projected column
        results_df["Current Proj"] = results_df.apply(colorize_projection, axis=1)

        # Print as Markdown-style table
        print(tabulate(results_df, headers="keys", tablefmt="github", showindex=False))
    else:
        print("No live games currently in progress.")

    print("\n" + "*" * 140)
    print("\n" + "*" * 140)
    print("\n" + "*" * 140 + "\n")

    time.sleep(30)
