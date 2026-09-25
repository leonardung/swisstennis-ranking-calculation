"""Scrape Swiss Tennis rankings and matches, recompute the ranking and compare."""

import argparse
import os
from pathlib import Path

import pandas as pd


def _load(data_dir: Path):
    from . import evaluate

    if not (data_dir / "history.parquet").exists():
        raise SystemExit(f"No scraped data in {data_dir}/; run `swisstennis-ranking scrape` first")
    history = pd.read_parquet(data_dir / "history.parquet")
    matches = pd.read_parquet(data_dir / "matches.parquet")
    players = pd.read_parquet(data_dir / "players.parquet")
    return evaluate, history, matches, players


def _publication(evaluate, history: pd.DataFrame, date: str | None, predict: bool) -> pd.Timestamp:
    pubs = evaluate.publications(history)
    if date is None:
        return pubs[-1]
    ts = pd.Timestamp(date)
    if ts not in pubs and not predict:
        raise SystemExit(f"{date} is not a publication date; run `periods` (other dates need --predict)")
    return ts


def main() -> None:
    parser = argparse.ArgumentParser(prog="swisstennis-ranking")
    parser.add_argument("--data", type=Path, default=Path("data"), help="data directory (default: data)")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="download players, ranking history and matches")
    s.add_argument("--since", default="2018-04-01", help="oldest history/match date to fetch")
    s.add_argument("--workers", type=int, default=8)
    s.add_argument(
        "--recent-days",
        type=int,
        help="update an existing download: fetch again the players with a result in the last N days "
        "(everyone when a new official list came out)",
    )

    sub.add_parser("periods", help="list publication dates found in the ranking history")

    e = sub.add_parser("evaluate", help="compute a list and compare it with the published one")
    e.add_argument("--date", help="list date (default: latest published)")
    e.add_argument(
        "--predict",
        action="store_true",
        help="use only what is known before the list is published (allows any date, e.g. a monthly list)",
    )

    w = sub.add_parser("serve", help="run the web UI")
    w.add_argument("--host", default="0.0.0.0")
    w.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    w.add_argument("--static", type=Path, default=Path(os.environ.get("STATIC_DIR", "web/dist")))
    w.add_argument(
        "--scrape-time",
        default=os.environ.get("SCRAPE_TIME", "03:00"),
        help="daily update time HH:MM (local time; empty = never)",
    )

    args = parser.parse_args()

    if args.command == "scrape":
        from .scrape import scrape

        scrape(args.data, args.since, args.workers, args.recent_days)
        return

    if args.command == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(args.data, args.static, args.scrape_time or None), host=args.host, port=args.port)
        return

    evaluate, history, matches, players = _load(args.data)

    if args.command == "periods":
        for pub in evaluate.publications(history):
            n = history.loc[history["date"].dt.normalize() == pub, "personId"].nunique()
            print(f"{pub.date()}  results {(pub - pd.DateOffset(years=1)).date()} .. {pub.date()}  {n} players")
        return

    period = evaluate.build_period(
        history, matches, players, _publication(evaluate, history, args.date, args.predict)
    )
    mode = "prediction" if args.predict else "reproduction"
    print(f"list {period.publication.date()} ({mode}), results {period.start.date()} .. {period.end.date()}")
    result = evaluate.evaluate(period, predict=args.predict)
    out = args.data / f"{mode}_{period.publication.date()}.parquet"
    result.to_parquet(out)
    if period.published:
        print(evaluate.report(result))
    else:
        listed = result[result["on_list"]]
        print(f"{len(listed)} players; per category:")
        print(listed.groupby(["gender", "class"]).size().unstack(0).reindex(evaluate.CATEGORIES).to_string())
    print(f"per-player results: {out}")
