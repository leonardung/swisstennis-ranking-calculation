"""Download players, ranking history and single results into data/.

Layout:
    data/raw/<personId>.json   one cached API response per player (makes scraping resumable)
    data/raw/season            the current official list when the cache was last completed
    data/players.parquet       currently licensed players (both genders, kontingent 0: foreigners)
    data/clubs.parquet         their clubs with coordinates (personId, clubId, club, city, lat, lon, canton)
    data/cantons.json          the canton at each club's coordinates (Nominatim, cached)
    data/history.parquet       published ranking values per player and publication date
    data/matches.parquet       single results, one row per (player, match) from the player's view
"""

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from tqdm import tqdm

from .evaluate import publications
from .winprob import free_memory
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
BUILD_BATCH = 2000  # raw files per batch when building the tables
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


def player_clubs(players: pd.DataFrame) -> pd.DataFrame:
    """The clubs of the players (fetch_players' "clubs" column), one row per player and club:
    personId, clubId, club, city, lat, lon."""
    rows = [
        {"personId": pid, "clubId": c["id"], "club": c.get("name"), "city": c.get("city"),
         "lat": c.get("latitude"), "lon": c.get("longitude")}
        for pid, clubs in zip(players["personId"], players["clubs"])
        for c in (m.get("club") for m in (clubs if isinstance(clubs, list) else []))
        if c and c.get("id") is not None
    ]  # fmt: skip
    df = pd.DataFrame(rows, columns=["personId", "clubId", "club", "city", "lat", "lon"])
    return df.astype({"personId": "int64", "clubId": "int64", "lat": "float64", "lon": "float64"})


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


def build_tables(raw_dir: Path, matches_out: Path) -> tuple[pd.DataFrame, int]:
    """Turn the raw cache into the history table, and write the matches table to `matches_out`
    on the way; returns the history and the number of match rows.

    Built in batches of files: millions of results as Python dicts do not fit in memory. Each
    batch of matches goes to its own file at once and the files are then copied one after the
    other into `matches_out`, so the ~1.2 GB matches table is never in memory as a whole (the
    nightly update runs next to the web server on a small machine).
    """
    paths = sorted(raw_dir.glob("*.json"))
    hist_parts = []
    with tempfile.TemporaryDirectory(dir=matches_out.parent) as tmp:
        parts = []
        for start in tqdm(range(0, len(paths), BUILD_BATCH), desc="building tables", unit_scale=BUILD_BATCH):
            history, matches = [], []
            for path in paths[start : start + BUILD_BATCH]:
                person_id = int(path.stem)
                raw = json.loads(path.read_text())
                history += raw["history"]
                matches += [{"personId": person_id, **r} for r in raw["results"]]
            h, m = pd.DataFrame(history), pd.DataFrame(matches)
            h["date"] = pd.to_datetime(h["date"], format="mixed")
            if len(m):
                m["date"] = pd.to_datetime(m["date"], format="mixed")
                m["adversaryPersonId"] = m["adversaryPersonId"].astype("Int64")
                parts.append(Path(tmp) / f"{start}.parquet")
                # the pandas types (Int64) go with each part, in its metadata
                pq.write_table(pa.Table.from_pandas(m, preserve_index=False), parts[-1])
            hist_parts.append(h)
            del history, matches, h, m
        n_matches = _join_parquet(parts, matches_out)

    hist = pd.concat(hist_parts, ignore_index=True)
    hist = hist.rename(columns={"competitionValue": "W", "classificationValue": "C"})
    return hist, n_matches


def _join_parquet(parts: list[Path], out: Path) -> int:
    """Write the tables of `parts` one after the other into `out`, one part in memory at a time;
    returns the number of rows. Their columns are united (a column missing from a part, or empty
    in it, becomes nulls of the type it has elsewhere), with the pandas metadata of the first part."""
    schemas = [pq.read_schema(p) for p in parts]
    schema = pa.unify_schemas(schemas, promote_options="permissive").with_metadata(schemas[0].metadata)
    n = 0
    with pq.ParquetWriter(out, schema) as writer:
        for p in parts:
            t = pq.read_table(p)
            columns = [t[f.name].cast(f.type) if f.name in t.column_names else pa.nulls(len(t), f.type) for f in schema]
            writer.write_table(pa.Table.from_arrays(columns, schema=schema))
            n += len(t)
    return n


def opponent_ids(raw_dir: Path) -> set[int]:
    """The opponents in the results of the raw cache, read one file at a time (no tables)."""
    ids = set()
    for path in raw_dir.glob("*.json"):
        ids.update(int(r["adversaryPersonId"]) for r in json.loads(path.read_text())["results"] if r.get("adversaryPersonId") is not None)
    return ids


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
    columns = ["personId", "firstname", "lastname", "date", "classification", "rank", "W", "C", "kontingent"]
    return pd.concat([hist, current[columns]])


def add_list_flags(client: Client, hist: pd.DataFrame) -> pd.DataFrame:
    """Add the quota flag (kontingent: 1 inside, 0 foreigner) of every full list to the history.

    One paged bulk query per list; the current list keeps the flag of the licence table, rows of
    individual corrections on other dates stay NaN.
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
    out = hist.rename(columns={"kontingent": "_current"}).assign(_day=day)
    out = out.merge(flags, on=["personId", "_day"], how="left")
    if "_current" in out:
        out["kontingent"] = out["kontingent"].fillna(out["_current"])
    return out.drop(columns=["_day", "_current"], errors="ignore")


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
    from .tournaments import club_cantons  # tournaments imports this module

    _write(club_cantons(player_clubs(players), data_dir / "cantons.json"), data_dir, "clubs")
    players = players.drop(columns="clubs")
    _write(players, data_dir, "players")
    print(f"{len(players)} licensed players ({players['gender'].value_counts().to_dict()})")

    _fetch_all(client, set(players["personId"]), since, raw_dir, workers, "players")

    # Opponents who are no longer licensed still carry a ranking value; fetch them once.
    # Their own opponents are not followed further: those matches fall back to the
    # adversary value stored with the result.
    cached = {int(p.stem) for p in raw_dir.glob("*.json")}
    missing = opponent_ids(raw_dir) - cached
    _fetch_all(client, missing, since, raw_dir, workers, "unlicensed opponents")

    write_tables(client, raw_dir, data_dir, players)
    (raw_dir / "season").write_text(current_season(client).isoformat())
    (raw_dir / "refresh_since").unlink(missing_ok=True)


def write_tables(client: Client, raw_dir: Path, data_dir: Path, players: pd.DataFrame) -> None:
    """Build history and matches from the raw cache and write them; both are renamed into place
    at the end, so a failure on the way leaves the previous pair."""
    matches_tmp = data_dir / "matches.tmp"
    hist, n_matches = build_tables(raw_dir, matches_tmp)
    free_memory()  # the parsed results: give their memory back before the history steps
    hist = _with_current_list(client, hist, players)
    hist = add_list_flags(client, hist)
    _write(hist, data_dir, "history")
    matches_tmp.replace(data_dir / "matches.parquet")
    print(f"{hist['personId'].nunique()} players with history, {n_matches} match rows")
