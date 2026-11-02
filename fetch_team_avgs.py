"""Build 2025teamdata.csv: current-season regular-season points per game per team.
Source: ESPN public scoreboard (stats.nba.com / NBA CDN schedule block cloud IPs)."""
from datetime import date, timedelta
import requests
import pandas as pd

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
# ESPN abbreviations that differ from NBA tricodes used by nba_api live data
FIX = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHX": "PHX"}

def season_start(today):
    return date(today.year if today.month >= 9 else today.year - 1, 10, 1)

rows, d, today = [], season_start(date.today()), date.today()
while d <= today:
    events = requests.get(URL, params={"dates": d.strftime("%Y%m%d")}, timeout=30).json().get("events", [])
    for e in events:
        if e["season"]["type"] != 2 or not e["status"]["type"]["completed"]:
            continue  # regular season, final games only
        for c in e["competitions"][0]["competitors"]:
            ab = c["team"]["abbreviation"]
            rows.append({"Team": FIX.get(ab, ab), "pts": int(c["score"])})
    d += timedelta(days=1)

df = pd.DataFrame(rows).groupby("Team")["pts"].agg(Score="mean", GP="count").reset_index()
df["Score"] = df["Score"].round(1)
df.to_csv("2025teamdata.csv", index=False)
print(f"{len(df)} teams, {len(rows)//2} games")
print(df.sort_values("Score", ascending=False).to_string(index=False))
