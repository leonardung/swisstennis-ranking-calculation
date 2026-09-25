"""Download players, ranking history and single results into data/.

Layout:
    data/raw/<personId>.json   one cached API response per player (makes scraping resumable)
    data/players.parquet       currently licensed players (both genders)
    data/history.parquet       published ranking values per player and publication date
    data/matches.parquet       single results, one row per (player, match) from the player's view
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from .api import CURRENT_SEASON_QUERY, HISTORY_QUERY, PLAYERS_QUERY, RESULTS_QUERY, Client

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


def _with_current_list(client: Client, hist: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """RankingHistory lags one list behind; add the current list from the licence table."""
    season = client.query(CURRENT_SEASON_QUERY, {})["RankSeasonRange"][0]["dateBegin"]
    date = pd.Timestamp(season)
    if (hist["date"] == date).any():
        return hist
    current = players.assign(date=date).rename(columns={"ranking": "rank"})
    return pd.concat([hist, current[["personId", "firstname", "lastname", "date", "classification", "rank", "W", "C"]]])


def scrape(data_dir: Path, since: str, workers: int) -> None:
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    client = Client()

    players = fetch_players(client)
    players.to_parquet(data_dir / "players.parquet")
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
    hist.to_parquet(data_dir / "history.parquet")
    matches.to_parquet(data_dir / "matches.parquet")
    print(f"{hist['personId'].nunique()} players with history, {len(matches)} match rows")
