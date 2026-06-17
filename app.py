import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="World Cup Draft Pool Sponsored By FFLT",
    page_icon="⚽",
    layout="wide",
)

API_BASE = "https://api.football-data.org/v4"
COMPETITION_CODE = "WC"
SEASON = 2026

TEAM_ALIASES = {
    "USA": "United States",
    "United States of America": "United States",
    "USMNT": "United States",
    "Côte d’Ivoire": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
    "Korea Republic": "South Korea",
    "Republic of Korea": "South Korea",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Bosnia": "Bosnia and Herzegovina",
    "Czech Republic": "Czechia",
    "Curaçao": "Curacao",
    "DR Congo": "Congo",
    "Congo DR": "Congo",
    "Cape Verde": "Cabo Verde",
    "Cabo Verde": "Cabo Verde",
    "Cape Verde Islands": "Cabo Verde",
    "Republic of Cabo Verde": "Cabo Verde",
    "CPV": "Cabo Verde",
    "Türkiye": "Turkey",
    "IR Iran": "Iran",
}

MANUAL_ADVANCEMENT_BONUSES = {}


def normalize_team(name: str) -> str:
    if not name:
        return ""
    name = str(name).strip()
    name = TEAM_ALIASES.get(name, name)
    return re.sub(r"\s+", " ", name)


def get_secret_token() -> str:
    try:
        return st.secrets["FOOTBALL_DATA_TOKEN"]
    except Exception:
        return os.getenv("FOOTBALL_DATA_TOKEN", "")


@st.cache_data(ttl=60, show_spinner=False)
def fetch_world_cup_matches(api_token: str) -> list[dict]:
    if not api_token:
        return []

    url = f"{API_BASE}/competitions/{COMPETITION_CODE}/matches"
    params = {"season": SEASON}
    headers = {"X-Auth-Token": api_token}

    response = requests.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json().get("matches", [])


def score_match_for_team(match: dict, team: str) -> dict | None:
    home = normalize_team(match.get("homeTeam", {}).get("name"))
    away = normalize_team(match.get("awayTeam", {}).get("name"))
    team = normalize_team(team)

    if team not in {home, away}:
        return None

    status = match.get("status", "")
    utc_date = match.get("utcDate", "")
    stage = match.get("stage", "")
    group = match.get("group", "")
    score = match.get("score", {}) or {}

    full_time = score.get("fullTime") or {}
    regular_time = score.get("regularTime") or {}
    extra_time = score.get("extraTime") or {}

    home_goals = full_time.get("home")
    away_goals = full_time.get("away")

    if home_goals is None or away_goals is None:
        home_goals = extra_time.get("home")
        away_goals = extra_time.get("away")

    if home_goals is None or away_goals is None:
        home_goals = regular_time.get("home")
        away_goals = regular_time.get("away")

    is_finished = status in {"FINISHED", "AWARDED"} and home_goals is not None and away_goals is not None
    is_live = status in {"IN_PLAY", "PAUSED", "LIVE"} and home_goals is not None and away_goals is not None

    points = 0
    result = ""
    record_delta = {"W": 0, "D": 0, "L": 0}
    bonus_3_plus = 0
    bonus_shutout = 0

    if is_finished or is_live:
        team_goals = home_goals if team == home else away_goals
        opp_goals = away_goals if team == home else home_goals

        if team_goals > opp_goals:
            points = 3
            result = "W"
            record_delta["W"] = 1 if is_finished else 0

        elif team_goals == opp_goals:
            points = 1
            result = "D"
            record_delta["D"] = 1 if is_finished else 0

        else:
            points = 0
            result = "L"
            record_delta["L"] = 1 if is_finished else 0

    opponent = away if team == home else home

    display_score = ""
    if home_goals is not None and away_goals is not None:
        display_score = f"{home_goals}-{away_goals}" if team == home else f"{away_goals}-{home_goals}"

    return {
        "team": team,
        "opponent": opponent,
        "date": utc_date,
        "stage": stage,
        "group": group,
        "status": status,
        "score": display_score,
        "result": result,
        "match_points": points,
        "win_by_3_bonus": bonus_3_plus,
        "shutout_win_bonus": bonus_shutout,
        "W": record_delta["W"],
        "D": record_delta["D"],
        "L": record_delta["L"],
        "finished": is_finished,
        "live": is_live,
    }


def compute_advancement_bonuses(matches: list[dict], drafted_teams: set[str]) -> dict[str, int]:
    """This league uses match-result scoring only: win = 3, draw = 1, loss = 0."""
    return {team: 0 for team in drafted_teams}


def build_tables(matches: list[dict], draft_df: pd.DataFrame):
    draft_df = draft_df.copy()
    draft_df.columns = [c.strip().lower() for c in draft_df.columns]

    if "owner" not in draft_df.columns or "team" not in draft_df.columns:
        st.error("draft_teams.csv must have columns named owner and team.")
        st.stop()

    draft_df["team"] = draft_df["team"].map(normalize_team)

    drafted_teams = set(draft_df["team"])
    rows = []

    for _, drafted in draft_df.iterrows():
        team = drafted["team"]
        owner = drafted["owner"]

        for match in matches:
            scored = score_match_for_team(match, team)
            if scored:
                scored["owner"] = owner
                rows.append(scored)

    match_log = pd.DataFrame(rows)

    if match_log.empty:
        team_table = draft_df.copy()
        team_table["match_points"] = 0
        team_table["advancement_bonus"] = 0
        team_table["total_points"] = 0
        team_table["record"] = "0-0-0"
        team_table["live_games"] = 0

        owner_table = (
            team_table.groupby("owner", as_index=False)
            .agg(
                total_points=("total_points", "sum"),
                match_points=("match_points", "sum"),
                advancement_bonus=("advancement_bonus", "sum"),
                live_games=("live_games", "sum"),
            )
        )

        owner_table["record"] = "0-0-0"

        return team_table, owner_table, match_log

    advancement = compute_advancement_bonuses(matches, drafted_teams)

    grouped = (
        match_log.groupby(["owner", "team"], as_index=False)
        .agg(
            match_points=("match_points", "sum"),
            W=("W", "sum"),
            D=("D", "sum"),
            L=("L", "sum"),
            live_games=("live", "sum"),
        )
    )

    grouped["advancement_bonus"] = grouped["team"].map(advancement).fillna(0).astype(int)
    grouped["total_points"] = grouped["match_points"] + grouped["advancement_bonus"]
    grouped["record"] = (
        grouped["W"].astype(int).astype(str)
        + "-"
        + grouped["D"].astype(int).astype(str)
        + "-"
        + grouped["L"].astype(int).astype(str)
    )

    all_teams = draft_df.merge(grouped, on=["owner", "team"], how="left")

    all_teams = all_teams.fillna(
        {
            "match_points": 0,
            "W": 0,
            "D": 0,
            "L": 0,
            "live_games": 0,
            "advancement_bonus": 0,
            "total_points": 0,
            "record": "0-0-0",
        }
    )

    for col in ["match_points", "W", "D", "L", "live_games", "advancement_bonus", "total_points"]:
        all_teams[col] = all_teams[col].astype(int)

    all_teams["record"] = (
        all_teams["W"].astype(str)
        + "-"
        + all_teams["D"].astype(str)
        + "-"
        + all_teams["L"].astype(str)
    )

    owner_table = (
        all_teams.groupby("owner", as_index=False)
        .agg(
            total_points=("total_points", "sum"),
            match_points=("match_points", "sum"),
            advancement_bonus=("advancement_bonus", "sum"),
            W=("W", "sum"),
            D=("D", "sum"),
            L=("L", "sum"),
            live_games=("live_games", "sum"),
        )
        .sort_values(["total_points", "match_points"], ascending=False)
    )

    owner_table["record"] = (
        owner_table["W"].astype(int).astype(str)
        + "-"
        + owner_table["D"].astype(int).astype(str)
        + "-"
        + owner_table["L"].astype(int).astype(str)
    )

    return (
        all_teams.sort_values(["owner", "total_points"], ascending=[True, False]),
        owner_table,
        match_log,
    )


def pretty_owner_table(df):
    display = df.copy()
    return display.rename(
        columns={
            "owner": "Owner",
            "total_points": "Total",
            "match_points": "Match Pts",
            "record": "Record",
            "live_games": "Live",
        }
    )


def pretty_team_table(df):
    display = df.copy()
    return display.rename(
        columns={
            "owner": "Owner",
            "team": "Country",
            "total_points": "Total",
            "match_points": "Match Pts",
            "record": "Record",
            "live_games": "Live",
        }
    )


def pretty_match_log(df):
    display = df.copy()
    return display.rename(
        columns={
            "date": "Date",
            "owner": "Owner",
            "team": "Country",
            "opponent": "Opponent",
            "stage": "Stage",
            "group": "Group",
            "status": "Status",
            "score": "Score",
            "result": "Result",
            "match_points": "Pts",
            "win_by_3_bonus": "3+ Bonus",
            "shutout_win_bonus": "Shutout Bonus",
        }
    )




def pretty_competition_label(value: str) -> str:
    """Convert API labels like GROUP_STAGE or GROUP_A into clean display text."""
    if not value:
        return ""

    value = str(value).strip()

    overrides = {
        "GROUP_STAGE": "Group Stage",
        "LAST_32": "Round of 32",
        "ROUND_OF_32": "Round of 32",
        "LAST_16": "Round of 16",
        "ROUND_OF_16": "Round of 16",
        "QUARTER_FINALS": "Quarterfinals",
        "SEMI_FINALS": "Semifinals",
        "FINAL": "Final",
        "THIRD_PLACE": "Third Place",
    }

    upper_value = value.upper()

    if upper_value in overrides:
        return overrides[upper_value]

    if upper_value.startswith("GROUP_") and len(upper_value.split("_")) == 2:
        return f"Group {upper_value.split('_')[1]}"

    return value.replace("_", " ").title()


def pretty_status(status: str) -> str:
    """Convert API statuses into user-facing labels."""
    labels = {
        "FINISHED": "✅ Finished",
        "IN_PLAY": "🟢 Live",
        "LIVE": "🟢 Live",
        "PAUSED": "🟡 Paused",
        "TIMED": "🕒 Scheduled",
        "SCHEDULED": "🕒 Scheduled",
        "POSTPONED": "Postponed",
        "SUSPENDED": "Suspended",
        "CANCELLED": "Cancelled",
        "AWARDED": "Awarded",
    }
    return labels.get(str(status).upper(), pretty_competition_label(status))


def format_match_datetime(utc_date: str, timezone_name: str = "America/New_York") -> str:
    """Format football-data.org UTC dates as Eastern time for the league."""
    if not utc_date:
        return ""

    try:
        dt = datetime.fromisoformat(str(utc_date).replace("Z", "+00:00"))
        local_dt = dt.astimezone(ZoneInfo(timezone_name))
        return local_dt.strftime("%b %-d, %Y • %-I:%M %p ET")
    except Exception:
        try:
            dt = datetime.fromisoformat(str(utc_date).replace("Z", "+00:00"))
            local_dt = dt.astimezone(ZoneInfo(timezone_name))
            return local_dt.strftime("%b %d, %Y • %I:%M %p ET").replace(" 0", " ")
        except Exception:
            return str(utc_date)


def match_context(row: pd.Series) -> str:
    """Build a clean subtitle for match cards."""
    parts = []

    status = row.get("status", "")
    if status:
        parts.append(pretty_status(status))

    match_time = format_match_datetime(row.get("date", ""))
    if match_time:
        parts.append(match_time)

    stage = pretty_competition_label(row.get("stage", ""))
    group = pretty_competition_label(row.get("group", ""))

    if stage:
        parts.append(stage)
    if group:
        parts.append(group)

    return " • ".join(parts)




def scoring_lines(row: pd.Series) -> list[str]:
    """Return readable scoring components for one team/match row."""
    result = row.get("result", "")

    if result == "W":
        return ["+3 Win"]
    if result == "D":
        return ["+1 Draw"]
    if result == "L":
        return ["+0 Loss"]

    return ["No fantasy points yet"]


def match_title(row: pd.Series) -> str:
    team = row.get("team", "")
    opponent = row.get("opponent", "")
    score = row.get("score", "")

    if score:
        return f"{team} {score} vs {opponent}"
    return f"{team} vs {opponent}"


def parse_match_datetime(utc_date: str):
    """Return a timezone-aware ET datetime for a match, or None if parsing fails."""
    if not utc_date:
        return None
    try:
        dt = datetime.fromisoformat(str(utc_date).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return None


def match_sort_datetime(row: pd.Series):
    dt = parse_match_datetime(row.get("date", ""))
    return dt or datetime.max.replace(tzinfo=ZoneInfo("America/New_York"))


def match_card_key(row: pd.Series) -> str:
    teams = sorted([str(row.get("team", "")), str(row.get("opponent", ""))])
    return f"{row.get('date', '')}|{teams[0]}|{teams[1]}"


def match_bucket(row: pd.Series) -> str:
    status = str(row.get("status", "")).upper()
    dt = parse_match_datetime(row.get("date", ""))
    today = datetime.now(ZoneInfo("America/New_York")).date()

    if status in {"IN_PLAY", "LIVE", "PAUSED"}:
        return "live"
    if dt and dt.date() == today:
        return "today"
    if status in {"TIMED", "SCHEDULED"}:
        return "upcoming"
    if status in {"FINISHED", "AWARDED"}:
        return "completed"
    return "other"


def render_match_card(match_rows: pd.DataFrame):
    """Render one user-friendly match card from one or more drafted-team rows."""
    if match_rows.empty:
        return

    first = match_rows.iloc[0]
    status = str(first.get("status", "")).upper()
    team = first.get("team", "")
    opponent = first.get("opponent", "")
    score = first.get("score", "")

    if score:
        title = f"{team} {score} {opponent}"
    else:
        title = f"{team} vs {opponent}"

    context_parts = []
    pretty_time = format_match_datetime(first.get("date", ""))
    if pretty_time:
        context_parts.append(pretty_time)
    stage = pretty_competition_label(first.get("stage", ""))
    group = pretty_competition_label(first.get("group", ""))
    if stage:
        context_parts.append(stage)
    if group:
        context_parts.append(group)

    status_label = pretty_status(status)
    status_class = "status-live" if status in {"IN_PLAY", "LIVE", "PAUSED"} else "status-finished" if status in {"FINISHED", "AWARDED"} else "status-scheduled"

    # Build a cleaner matchup line from the drafted teams involved.
    participants = []
    seen_teams = set()
    for _, row in match_rows.sort_values(["team", "owner"]).iterrows():
        country = row.get("team", "")
        owner = row.get("owner", "")
        if country and country not in seen_teams:
            participants.append((country, owner))
            seen_teams.add(country)

    matchup_html = ""
    if participants:
        participant_html = "<span class='matchup-chip'>" + "</span><span class='matchup-vs'>vs</span><span class='matchup-chip'>".join(
            f"{country} <span class='matchup-owner'>({owner})</span>" for country, owner in participants
        ) + "</span>"
        matchup_html = f"""<div class="matchup-block">
    <div class="impact-title">Fantasy Matchup</div>
    <div class="matchup-line">{participant_html}</div>
</div>"""

    impact_items = []
    for _, row in match_rows.sort_values(["owner", "team"]).iterrows():
        owner = row.get("owner", "")
        country = row.get("team", "")
        pts = int(row.get("match_points", 0) or 0)
        row_status = str(row.get("status", "")).upper()
        details = "; ".join(scoring_lines(row))

        if row_status in {"TIMED", "SCHEDULED"}:
            impact_items.append(f"<li><strong>{owner}</strong> has {country}</li>")
        elif pts > 0:
            impact_items.append(f"<li><strong>{owner}</strong>: <span class='positive-points'>+{pts}</span> from {country} <span class='impact-detail'>({details})</span></li>")
        elif row.get("result", ""):
            impact_items.append(f"<li><strong>{owner}</strong>: +0 from {country} <span class='impact-detail'>({details})</span></li>")
        else:
            impact_items.append(f"<li><strong>{owner}</strong> has {country}</li>")

    impact_html = "".join(impact_items) if impact_items else "<li>No drafted-team impact yet.</li>"
    context_html = " • ".join(context_parts)

    st.markdown(
        f"""
<div class="match-card">
    <div class="match-card-topline">
        <span class="status-pill {status_class}">{status_label}</span>
        <span class="match-context">{context_html}</span>
    </div>
    <div class="match-title">{title}</div>
    {matchup_html}
    <div class="impact-title">Fantasy Impact</div>
    <ul class="impact-list">{impact_html}</ul>
</div>
""",
        unsafe_allow_html=True,
    )

def render_match_section(title: str, rows: pd.DataFrame, empty_message: str, limit: int | None = None):
    st.markdown(f"### {title}")
    if rows.empty:
        st.info(empty_message)
        return

    rows = rows.copy()
    rows["_sort_dt"] = rows.apply(match_sort_datetime, axis=1)
    rows["_match_key"] = rows.apply(match_card_key, axis=1)

    ordered_keys = (
        rows.sort_values("_sort_dt")
        .drop_duplicates("_match_key")["_match_key"]
        .tolist()
    )

    if limit is not None:
        ordered_keys = ordered_keys[:limit]

    for key in ordered_keys:
        render_match_card(rows[rows["_match_key"] == key].drop(columns=["_sort_dt", "_match_key"], errors="ignore"))


def owner_upcoming_match_rows(match_log: pd.DataFrame, owner: str) -> pd.DataFrame:
    """Return all rows for matches involving the selected owner's teams that are not finished yet."""
    if match_log.empty:
        return pd.DataFrame()

    working = match_log.copy()
    working["_match_key"] = working.apply(match_card_key, axis=1)
    working["_bucket"] = working.apply(match_bucket, axis=1)

    owner_keys = working.loc[
        (working["owner"] == owner) & (working["_bucket"].isin(["live", "today", "upcoming"])),
        "_match_key",
    ].dropna().unique()

    if len(owner_keys) == 0:
        return pd.DataFrame()

    return working[working["_match_key"].isin(owner_keys)].copy()


def render_owner_upcoming_matches(owner: str, match_log: pd.DataFrame):
    """Render upcoming/live match cards for one owner inside the Owner Dashboard."""
    rows = owner_upcoming_match_rows(match_log, owner)

    st.markdown("### Upcoming Matches")

    if rows.empty:
        st.info("No upcoming matches found for this owner right now.")
        return

    rows["_sort_dt"] = rows.apply(match_sort_datetime, axis=1)
    rows["_match_key"] = rows.apply(match_card_key, axis=1)

    ordered_keys = (
        rows.sort_values("_sort_dt")
        .drop_duplicates("_match_key")["_match_key"]
        .tolist()
    )

    for key in ordered_keys[:8]:
        render_match_card(rows[rows["_match_key"] == key].drop(columns=["_sort_dt", "_match_key", "_bucket"], errors="ignore"))


def next_match_text(country_matches: pd.DataFrame) -> str:
    """Small summary for a country card: live match or next scheduled opponent/time."""
    if country_matches.empty:
        return "No upcoming match listed"

    working = country_matches.copy()
    working["_bucket"] = working.apply(match_bucket, axis=1)
    working["_sort_dt"] = working.apply(match_sort_datetime, axis=1)

    live_rows = working[working["_bucket"] == "live"].sort_values("_sort_dt")
    if not live_rows.empty:
        row = live_rows.iloc[0]
        score = row.get("score", "")
        score_text = f" • {score}" if score else ""
        return f"Live vs {row.get('opponent', '')}{score_text}"

    future_rows = working[working["_bucket"].isin(["today", "upcoming"])].sort_values("_sort_dt")
    if not future_rows.empty:
        row = future_rows.iloc[0]
        when = format_match_datetime(row.get("date", ""))
        return f"Next: vs {row.get('opponent', '')} • {when}"

    return "No upcoming match listed"


def render_owner_summary_cards(owner_row: pd.Series):
    values = [
        ("Total Points", int(owner_row.get("total_points", 0))),
        ("Match Points", int(owner_row.get("match_points", 0))),
        ("Record", owner_row.get("record", "0-0-0")),
    ]
    summary_cols = st.columns(len(values))

    for col, (label, value) in zip(summary_cols, values):
        with col:
            st.markdown(
                f"""
<div class="mini-stat-card">
    <div class="mini-stat-label">{label}</div>
    <div class="mini-stat-value">{value}</div>
</div>
""",
                unsafe_allow_html=True,
            )


def render_country_breakdown_card(row: pd.Series, country_matches: pd.DataFrame):
    live_badge = '<span class="live-pill">LIVE</span>' if int(row.get("live_games", 0)) > 0 else ''
    next_text = next_match_text(country_matches)

    st.markdown(
        f"""
<div class="dashboard-country-card">
    <div class="dashboard-country-topline">
        <div>
            <div class="country-name">{row["team"]} {live_badge}</div>
            <div class="country-owner">Record: {row["record"]}</div>
            <div class="country-next-match">{next_text}</div>
        </div>
        <div class="country-points">{int(row["total_points"])} pts</div>
    </div>
    <div class="country-meta">
        Match Points: {int(row["match_points"])}
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.expander(f"Point breakdown for {row['team']}"):
        if country_matches.empty:
            st.write("No completed, live, or upcoming matches found.")
            return

        country_matches = country_matches.sort_values("date")

        for _, match in country_matches.iterrows():
            lines = scoring_lines(match)
            line_html = "".join(f"<li>{line}</li>" for line in lines)
            subtitle = match_context(match)

            st.markdown(
                f"""
<div class="breakdown-card">
    <div class="breakdown-title">{match_title(match)}</div>
    <div class="breakdown-subtitle">{subtitle}</div>
    <ul class="breakdown-list">
        {line_html}
    </ul>
    <div class="breakdown-total">Match Total: +{int(match.get("match_points", 0) or 0)}</div>
</div>
""",
                unsafe_allow_html=True,
            )



st.markdown(
    """
<style>
.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

.hero {
    padding: 1.35rem 1.6rem;
    border-radius: 22px;
    background: linear-gradient(135deg, #12372A 0%, #0B1F1A 100%);
    border: 1px solid rgba(255,255,255,0.12);
    margin-bottom: 1.25rem;
}

.hero h1 {
    margin: 0;
    font-size: 42px;
    line-height: 1.1;
    letter-spacing: -0.02em;
}

.hero p {
    margin-top: 0.6rem;
    color: #D1D5DB;
    font-size: 15px;
}

.league-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1.1rem 1.25rem;
    margin-bottom: 0.75rem;
    border-radius: 18px;
    background: #111827;
    border: 1px solid #374151;
}

.rank-label {
    color: #FACC15;
    font-size: 18px;
    font-weight: 800;
    margin-bottom: 0.25rem;
}

.owner-name {
    font-size: 25px;
    font-weight: 850;
}

.total-points {
    font-size: 34px;
    font-weight: 900;
    white-space: nowrap;
}

.small-muted {
    color: #9CA3AF;
    font-size: 13px;
}

.country-card {
    padding: 1rem;
    border-radius: 18px;
    background: #111827;
    border: 1px solid #374151;
    margin-bottom: 1rem;
    min-height: 150px;
}

.country-name {
    font-size: 22px;
    font-weight: 850;
    margin-bottom: 0.25rem;
}

.country-owner {
    color: #9CA3AF;
    font-size: 13px;
    margin-bottom: 0.35rem;
}

.country-next-match {
    color: #BFDBFE;
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 0.8rem;
}

.country-points {
    font-size: 34px;
    font-weight: 900;
    margin-bottom: 0.35rem;
}

.country-meta {
    color: #D1D5DB;
    font-size: 13px;
    line-height: 1.6;
}

.live-pill {
    display: inline-block;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    background: rgba(34,197,94,0.15);
    color: #86EFAC;
    font-size: 12px;
    font-weight: 700;
    margin-left: 0.35rem;
}

.dead-pill {
    display: inline-block;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    background: rgba(156,163,175,0.12);
    color: #D1D5DB;
    font-size: 12px;
    font-weight: 700;
    margin-left: 0.35rem;
}

.section-note {
    color: #9CA3AF;
    margin-bottom: 1rem;
}


.mini-stat-card {
    padding: 1rem;
    border-radius: 16px;
    background: #111827;
    border: 1px solid #374151;
    min-height: 100px;
}

.mini-stat-label {
    color: #9CA3AF;
    font-size: 13px;
    margin-bottom: 0.35rem;
}

.mini-stat-value {
    font-size: 30px;
    font-weight: 900;
}

.dashboard-country-card {
    padding: 1rem;
    border-radius: 18px;
    background: #111827;
    border: 1px solid #374151;
    margin-top: 1rem;
    margin-bottom: 0.4rem;
}

.dashboard-country-topline {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    align-items: flex-start;
}

.breakdown-card {
    padding: 0.9rem 1rem;
    border-radius: 14px;
    background: #0B1220;
    border: 1px solid #1F2937;
    margin-bottom: 0.75rem;
}

.breakdown-title {
    font-size: 17px;
    font-weight: 800;
    margin-bottom: 0.2rem;
}

.breakdown-subtitle {
    color: #9CA3AF;
    font-size: 13px;
    margin-bottom: 0.45rem;
}

.breakdown-list {
    margin-top: 0.35rem;
    margin-bottom: 0.35rem;
    color: #D1D5DB;
}

.breakdown-total {
    font-weight: 900;
    color: #FACC15;
    margin-top: 0.35rem;
}


.match-card {
    padding: 1rem 1.15rem;
    border-radius: 18px;
    background: #111827;
    border: 1px solid #374151;
    margin-bottom: 0.9rem;
}

.match-card-topline {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    margin-bottom: 0.65rem;
}

.status-pill {
    display: inline-block;
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 800;
}

.status-live {
    background: rgba(34,197,94,0.16);
    color: #86EFAC;
}

.status-finished {
    background: rgba(59,130,246,0.16);
    color: #BFDBFE;
}

.status-scheduled {
    background: rgba(250,204,21,0.14);
    color: #FDE68A;
}

.match-context {
    color: #9CA3AF;
    font-size: 13px;
}

.match-title {
    font-size: 24px;
    font-weight: 900;
    margin-bottom: 0.75rem;
}

.impact-title {
    color: #9CA3AF;
    font-size: 13px;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.35rem;
}

.impact-list {
    margin: 0;
    padding-left: 1.1rem;
    color: #D1D5DB;
    line-height: 1.6;
}

.impact-detail {
    color: #9CA3AF;
}

.matchup-block {
    margin-bottom: 0.75rem;
}

.matchup-line {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
    align-items: center;
}

.matchup-chip {
    display: inline-block;
    padding: 0.35rem 0.6rem;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.9);
    border: 1px solid #374151;
    font-weight: 800;
    color: #E5E7EB;
}

.matchup-owner {
    color: #9CA3AF;
    font-weight: 700;
}

.matchup-vs {
    color: #9CA3AF;
    font-size: 12px;
    font-weight: 900;
    text-transform: uppercase;
}

.positive-points {
    color: #86EFAC;
    font-weight: 900;
}

div[data-testid="stDataFrame"] {
    border-radius: 16px;
    overflow: hidden;
}


/* Larger Owner Dashboard sub-tabs */
div[data-testid="stTabs"] button[role="tab"] {
    font-size: 1.1rem;
    font-weight: 800;
    padding: 0.7rem 1.15rem;
}

</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="hero">
    <h1>The World Cup Draft Pool Sponsored By FFLT ⚽️</h1>
    <p>Live fantasy standings powered by football-data.org.</p>
</div>
""",
    unsafe_allow_html=True,
)

token = get_secret_token()

try:
    draft = pd.read_csv("draft_teams.csv")
except FileNotFoundError:
    st.error("draft_teams.csv was not found. Make sure it is uploaded to the GitHub repo root.")
    st.stop()

matches = []

if not token:
    st.warning("Add your FOOTBALL_DATA_TOKEN in Streamlit secrets to pull live World Cup data.")
else:
    try:
        with st.spinner("Pulling latest World Cup scores..."):
            matches = fetch_world_cup_matches(token)
    except Exception as e:
        st.error(f"Could not pull API data: {e}")

team_table, owner_table, match_log = build_tables(matches, draft)

tabs = st.tabs(["🏆 Standings", "👤 Owner Dashboard", "🌎 All Teams", "⚽ Match Center", "📖 Rules"])

with tabs[0]:
    st.subheader("League Standings")

    if owner_table.empty:
        st.info("No standings yet.")
    else:
        ranked = owner_table.sort_values(["total_points", "match_points"], ascending=False).reset_index(drop=True)

        for idx, row in ranked.iterrows():
            rank = idx + 1
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else f"#{rank}"

            st.markdown(
                f"""
<div class="league-row">
    <div>
        <div class="rank-label">{medal}</div>
        <div class="owner-name">{row["owner"]}</div>
        <div class="small-muted">Record: {row["record"]} • Match Pts: {int(row["match_points"])}</div>
    </div>
    <div class="total-points">{int(row["total_points"])} pts</div>
</div>
""",
                unsafe_allow_html=True,
            )

        with st.expander("Detailed Standings Table"):
            display = pretty_owner_table(owner_table)
            wanted_cols = ["Owner", "Total", "Match Pts", "Record", "Live"]
            display = display[[c for c in wanted_cols if c in display.columns]]
            st.dataframe(display, use_container_width=True, hide_index=True)

with tabs[1]:
    st.subheader("Owner Dashboard")
    st.markdown('<div class="section-note">Pick an owner to see their full roster and exactly how each country has earned points.</div>', unsafe_allow_html=True)

    owners = sorted(team_table["owner"].dropna().unique())

    if not owners:
        st.info("No owners found yet.")
    else:
        selected_dashboard_owner = st.selectbox("Select owner", owners, key="owner_dashboard_select")

        owner_row_df = owner_table[owner_table["owner"] == selected_dashboard_owner]
        owner_teams = team_table[team_table["owner"] == selected_dashboard_owner].sort_values(
            ["total_points", "match_points"], ascending=False
        )

        if owner_row_df.empty:
            st.info("No owner data found.")
        else:
            owner_row = owner_row_df.iloc[0]
            st.markdown(f"### {selected_dashboard_owner}")
            render_owner_summary_cards(owner_row)

            roster_tab, upcoming_tab = st.tabs(["Roster", "Upcoming Matches"])

            with roster_tab:
                st.markdown("### Roster")

                for _, country_row in owner_teams.iterrows():
                    country = country_row["team"]

                    if match_log.empty:
                        country_matches = pd.DataFrame()
                    else:
                        country_matches = match_log[
                            (match_log["owner"] == selected_dashboard_owner)
                            & (match_log["team"] == country)
                        ].copy()

                    render_country_breakdown_card(country_row, country_matches)

            with upcoming_tab:
                render_owner_upcoming_matches(selected_dashboard_owner, match_log)

with tabs[2]:
    st.subheader("All Team Cards")
    st.markdown('<div class="section-note">Filter by owner to view drafted countries as cards.</div>', unsafe_allow_html=True)

    owners = sorted(team_table["owner"].dropna().unique())
    selected_owner = st.selectbox("Select an owner", ["All Owners"] + owners, key="all_teams_owner_select")

    filtered = team_table.copy()

    if selected_owner != "All Owners":
        filtered = filtered[filtered["owner"] == selected_owner]

    filtered = filtered.sort_values(["owner", "total_points", "match_points"], ascending=[True, False, False])

    card_cols = st.columns(3)

    for i, (_, row) in enumerate(filtered.iterrows()):
        live_badge = '<span class="live-pill">LIVE</span>' if int(row["live_games"]) > 0 else '<span class="dead-pill">IDLE</span>'

        with card_cols[i % 3]:
            st.markdown(
                f"""
<div class="country-card">
    <div class="country-name">{row["team"]} {live_badge}</div>
    <div class="country-owner">Owned by {row["owner"]}</div>
    <div class="country-points">{int(row["total_points"])} pts</div>
    <div class="country-meta">
        Record: {row["record"]}<br>
        Match Points: {int(row["match_points"])}
    </div>
</div>
""",
                unsafe_allow_html=True,
            )

    with st.expander("Detailed Team Table"):
        display = pretty_team_table(filtered)
        wanted_cols = ["Owner", "Country", "Total", "Match Pts", "Record", "Live"]
        display = display[[c for c in wanted_cols if c in display.columns]]
        st.dataframe(display, use_container_width=True, hide_index=True)

with tabs[3]:
    st.subheader("Match Center")
    st.markdown('<div class="section-note">See live, upcoming, and completed matches with fantasy impact for drafted teams.</div>', unsafe_allow_html=True)

    if match_log.empty:
        st.info("No match data yet.")
    else:
        match_center_all = match_log.copy()
        match_center_all["_match_key"] = match_center_all.apply(match_card_key, axis=1)
        match_center_all["_bucket"] = match_center_all.apply(match_bucket, axis=1)

        match_owners = sorted(match_center_all["owner"].dropna().unique())
        selected_match_owner = st.selectbox(
            "Filter Match Center by owner",
            ["All Owners"] + match_owners,
            key="match_center_owner_filter",
        )

        if selected_match_owner != "All Owners":
            owner_match_keys = set(
                match_center_all.loc[
                    match_center_all["owner"] == selected_match_owner,
                    "_match_key",
                ]
            )
            match_center = match_center_all[match_center_all["_match_key"].isin(owner_match_keys)].copy()
            st.markdown(
                f'<div class="section-note">Showing matches involving <strong>{selected_match_owner}</strong>\'s drafted teams.</div>',
                unsafe_allow_html=True,
            )
        else:
            match_center = match_center_all.copy()

        live_rows = match_center[match_center["_bucket"] == "live"]
        today_rows = match_center[match_center["_bucket"] == "today"]
        upcoming_rows = match_center[match_center["_bucket"] == "upcoming"]
        completed_rows = match_center[match_center["_bucket"] == "completed"]

        render_match_section("Live Now", live_rows, "No live matches right now.")
        render_match_section("Today’s Matches", today_rows, "No drafted-team matches today.")
        render_match_section("Upcoming Matches", upcoming_rows, "No upcoming drafted-team matches found.", limit=12)

        # Completed matches are shown newest first.
        if completed_rows.empty:
            st.markdown("### Completed Matches")
            st.info("No completed drafted-team matches yet.")
        else:
            completed_rows = completed_rows.copy()
            completed_rows["_sort_dt"] = completed_rows.apply(match_sort_datetime, axis=1)
            completed_rows = completed_rows.sort_values("_sort_dt", ascending=False).drop(columns=["_sort_dt"], errors="ignore")
            render_match_section("Completed Matches", completed_rows, "No completed drafted-team matches yet.", limit=12)

with tabs[4]:
    st.subheader("Scoring Rules")

    st.markdown(
        """
### Match Scoring
- **3 pts**: Win
- **1 pt**: Draw
- **0 pts**: Loss

The owner with the most total fantasy points at the end of the tournament wins the pool.
"""
    )
