"""Download players, ranking history and single results into data/.

Layout:
    data/raw/<personId>.json   one cached API response per player (makes scraping resumable)
    data/raw/season            the current official list when the cache was last completed
    data/players.parquet       currently licensed players (both genders)
    data/history.parquet       published ranking values per player and publication date
    data/matches.parquet       single results, one row per (player, match) from the player's view
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from .evaluate import publications
from .api import (
    CURRENT_SEASON_QUERY,
    HISTORY_QUERY,
    LIST_FLAGS_QUERY,
    PLAYERS_QUERY,
    RECENT_PLAYERS_QUERY,
    RESULTS_QUERY,
    Client,
)

PAGE_SIZE = 5000
RESULTS_LIMIT = 5000
GENDERS = {1: "M", 2: "F"}


def fetch_players(client: Client) -> pd.DataFrame:
    rows = []
    for gender in GENDERS:
        offset = 0
        while True:
            page = client.query(
                PLAYERS_QUERY,
                {
                    "offset": offset,
                    "limit": PAGE_SIZE,
                    "where": {
                        "kontingent": {"_eq": 1},
                        "currentLicenceStatusId": {"_lt": 3},
                        "person": {"gender": {"_eq": gender}},
                    },
                },
            )["lizenz_nehmer"]
            rows += page
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
    df = pd.json_normalize(rows)
    df = df.rename(
        columns={
            "person.id": "personId",
            "person.firstname": "firstname",
            "person.lastname": "lastname",
            "person.gender": "gender",
            "classificationValue": "C",
            "competitionValue": "W",
        }
    )
    df["gender"] = df["gender"].map(GENDERS)
    return df.drop_duplicates("personId").reset_index(drop=True)


def _fetch_player(client: Client, person_id: int, since: str, raw_dir: Path) -> None:
    path = raw_dir / f"{person_id}.json"
    if path.exists():
        return
    history = client.query(HISTORY_QUERY, {"id": person_id, "from": since})["list"]
    results = client.query(
        RESULTS_QUERY,
        {
            "where": {"playerPersonId": {"_eq": person_id}, "date": {"_gte": since}},
            "limit": RESULTS_LIMIT,
        },
    )["results"]
    if len(results) == RESULTS_LIMIT:
        raise RuntimeError(f"Player {person_id} hit the results limit; raise RESULTS_LIMIT")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"history": history, "results": results}))
    tmp.replace(path)  # atomic: an interrupted run never leaves a half-written cache file


def _fetch_all(client: Client, ids: set[int], since: str, raw_dir: Path, workers: int, desc: str) -> None:
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(_fetch_player, client, i, since, raw_dir) for i in sorted(ids)]
        for f in tqdm(futures, desc=desc):
            f.result()


def build_tables(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn the raw cache into (history, matches) tables."""
    history, matches = [], []
    for path in tqdm(sorted(raw_dir.glob("*.json")), desc="building tables"):
        person_id = int(path.stem)
        raw = json.loads(path.read_text())
        history += raw["history"]
        matches += [{"personId": person_id, **r} for r in raw["results"]]

    hist = pd.DataFrame(history)
    hist["date"] = pd.to_datetime(hist["date"], format="mixed")
    hist = hist.rename(columns={"competitionValue": "W", "classificationValue": "C"})

    m = pd.DataFrame(matches)
    m["date"] = pd.to_datetime(m["date"], format="mixed")
    m["adversaryPersonId"] = m["adversaryPersonId"].astype("Int64")
    return hist, m


def current_season(client: Client) -> pd.Timestamp:
    """Date of the current official list."""
    return pd.Timestamp(client.query(CURRENT_SEASON_QUERY, {})["RankSeasonRange"][0]["dateBegin"])


def recent_players(client: Client, since: pd.Timestamp) -> set[int]:
    ids, offset = set(), 0
    while True:
        page = client.query(
            RECENT_PLAYERS_QUERY,
            {"from": since.strftime("%Y-%m-%dT00:00:00"), "offset": offset, "limit": PAGE_SIZE},
        )["results"]
        ids |= {r["playerPersonId"] for r in page}
        if len(page) < PAGE_SIZE:
            return ids
        offset += PAGE_SIZE


def expire_cache(client: Client, raw_dir: Path, recent_days: int) -> None:
    """Delete the cached players whose data changed, so that they are fetched again.

    A new official list adds a row to everyone's history: then every file cached before it is
    stale (the refresh start is kept in `refresh_since` so an interrupted run resumes). Otherwise
    only players with a result dated in the last `recent_days` days (results are entered late).
    """
    season = current_season(client)
    marker, since_file = raw_dir / "season", raw_dir / "refresh_since"
    if not marker.exists() or pd.Timestamp(marker.read_text()) != season:
        if not since_file.exists():
            since_file.write_text(str(time.time()))
        cutoff = float(since_file.read_text())
        stale = [f for f in raw_dir.glob("*.json") if f.stat().st_mtime < cutoff]
        print(f"new list {season.date()}: fetching all {len(stale)} cached players again")
    else:
        ids = recent_players(client, pd.Timestamp.now().normalize() - pd.Timedelta(days=recent_days))
        stale = [f for i in ids if (f := raw_dir / f"{i}.json").exists()]
        print(f"{len(stale)} players with recent results")
    for f in stale:
        f.unlink()


def _with_current_list(client: Client, hist: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """RankingHistory lags one list behind; add the current list from the licence table."""
    date = current_season(client)
    if (hist["date"] == date).any():
        return hist
    current = players.assign(date=date).rename(columns={"ranking": "rank"})
    return pd.concat([hist, current[["personId", "firstname", "lastname", "date", "classification", "rank", "W", "C"]]])


def add_list_flags(client: Client, hist: pd.DataFrame) -> pd.DataFrame:
    """Add the quota flag (kontingent: 1 inside, 0 foreigner) of every full list to the history.

    One paged bulk query per list; rows of individual corrections on other dates and of the
    current list (taken from the licence table, which holds only players inside the quota) stay NaN.
    """
    day = hist["date"].dt.normalize()
    frames = []
    for date in tqdm(publications(hist), desc="list flags"):
        rows, offset = [], 0
        while True:
            page = client.query(
                LIST_FLAGS_QUERY, {"date": date.strftime("%Y-%m-%dT00:00:00"), "offset": offset, "limit": PAGE_SIZE}
            )["list"]
            rows += page
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        frames.append(pd.DataFrame(rows, columns=["personId", "kontingent"]).assign(_day=date))
    flags = pd.concat(frames).drop_duplicates(["personId", "_day"])
    out = hist.drop(columns="kontingent", errors="ignore").assign(_day=day)
    return out.merge(flags, on=["personId", "_day"], how="left").drop(columns="_day")


def _write(table: pd.DataFrame, data_dir: Path, name: str) -> None:
    """Write-then-rename: a reader (the web server) never sees a half-written table."""
    tmp = data_dir / f"{name}.tmp"
    table.to_parquet(tmp)
    tmp.replace(data_dir / f"{name}.parquet")


def scrape(data_dir: Path, since: str, workers: int, recent_days: int | None = None) -> None:
    """Download everything not cached yet; with recent_days, first expire_cache()."""
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = Client()
    if recent_days is not None:
        expire_cache(client, raw_dir, recent_days)

    players = fetch_players(client)
    _write(players, data_dir, "players")
    print(f"{len(players)} licensed players ({players['gender'].value_counts().to_dict()})")

    _fetch_all(client, set(players["personId"]), since, raw_dir, workers, "players")

    # Opponents who are no longer licensed still carry a ranking value; fetch them once.
    # Their own opponents are not followed further: those matches fall back to the
    # adversary value stored with the result.
    _, matches = build_tables(raw_dir)
    cached = {int(p.stem) for p in raw_dir.glob("*.json")}
    missing = set(matches["adversaryPersonId"].dropna().astype(int)) - cached
    _fetch_all(client, missing, since, raw_dir, workers, "unlicensed opponents")

    hist, matches = build_tables(raw_dir)
    hist = _with_current_list(client, hist, players)
    hist = add_list_flags(client, hist)
    _write(hist, data_dir, "history")
    _write(matches, data_dir, "matches")
    (raw_dir / "season").write_text(current_season(client).isoformat())
    (raw_dir / "refresh_since").unlink(missing_ok=True)
    print(f"{hist['personId'].nunique()} players with history, {len(matches)} match rows")
