# The 561 Torta Pounder World Cup Draft Dashboard

A Streamlit dashboard for tracking the World Cup fantasy draft.

## What it does

- Loads drafted teams from `draft_teams.csv`
- Pulls World Cup matches from football-data.org
- Calculates:
  - 3 points for a win
  - 1 point for a draw
  - 0 points for a loss
  - +1 for a win by 3+ goals
  - +1 for a shutout win
  - Advancement bonuses
- Displays owner standings, country standings, and match log

## Free API setup

1. Create a free account at football-data.org.
2. Get your API token.
3. In Streamlit Community Cloud, open:
   - App dashboard
   - Settings
   - Secrets
4. Add:

```toml
FOOTBALL_DATA_TOKEN = "your_token_here"
```

## Deploy to Streamlit Community Cloud

1. Create a GitHub repo.
2. Upload these files:
   - `app.py`
   - `requirements.txt`
   - `draft_teams.csv`
   - `.streamlit/secrets.toml.example`
3. Go to Streamlit Community Cloud.
4. Click **New app**.
5. Select your repo.
6. Main file path: `app.py`
7. Deploy.

## Notes

football-data.org free tier is rate-limited. The app caches API calls for 60 seconds to avoid burning requests.

The World Cup shootout rule is handled by ignoring penalties for match-result points. If an API does not expose knockout winners cleanly, update the `MANUAL_ADVANCEMENT_BONUSES` dictionary in `app.py`.
