"""Scrape Swiss Tennis rankings and matches, recompute the ranking and compare."""

import argparse
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


def _publication(evaluate, history: pd.DataFrame, date: str | None) -> pd.Timestamp:
    pubs = evaluate.publications(history)
    if date is None:
        return pubs[-1]
    ts = pd.Timestamp(date)
    if ts not in pubs:
        raise SystemExit(f"{date} is not a publication date; run `periods` to list them")
    return ts


def main() -> None:
    parser = argparse.ArgumentParser(prog="swisstennis-ranking")
    parser.add_argument("--data", type=Path, default=Path("data"), help="data directory (default: data)")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scrape", help="download players, ranking history and matches")
    s.add_argument("--since", default="2018-04-01", help="oldest history/match date to fetch")
    s.add_argument("--workers", type=int, default=8)

    sub.add_parser("flags", help="add the quota flag (kontingent) of each list to history.parquet")

    sub.add_parser("periods", help="list publication dates found in the ranking history")

    e = sub.add_parser("evaluate", help="recompute a published list and compare")
    e.add_argument("--date", help="publication date (default: latest)")

    c = sub.add_parser("calibrate", help="recover w0 from published values and fit w0 = a*W5 + b")
    c.add_argument("--date", help="publication date (default: latest)")

    args = parser.parse_args()

    if args.command == "scrape":
        from .scrape import scrape

        scrape(args.data, args.since, args.workers)
        return

    if args.command == "flags":
        from .scrape import update_list_flags

        update_list_flags(args.data)
        return

    evaluate, history, matches, players = _load(args.data)

    if args.command == "periods":
        for pub in evaluate.publications(history):
            end = evaluate.window_end(pub)
            n = history.loc[history["date"].dt.normalize() == pub, "personId"].nunique()
            print(f"{pub.date()}  results {(end - pd.DateOffset(years=1)).date()} .. {end.date()}  {n} players")
        return

    period = evaluate.build_period(history, matches, players, _publication(evaluate, history, args.date))
    print(f"list {period.publication.date()}, results {period.start.date()} .. {period.end.date()}")

    if args.command == "evaluate":
        result = evaluate.evaluate(period)
        out = args.data / f"evaluation_{period.publication.date()}.parquet"
        result.to_parquet(out)
        print(evaluate.report(result))
        print(f"per-player results: {out}")

    elif args.command == "calibrate":
        cal = evaluate.calibrate(period)
        cal["w0_table"] = evaluate.start_values(cal, evaluate.w0_table(period.players))
        out = args.data / f"calibration_{period.publication.date()}.parquet"
        cal.to_parquet(out)
        active = cal[cal["n_matches"] > 0]
        err = (active["w0"] - active["w0_table"]).abs()
        print(f"players with matches: {len(active)}; |inverted w0 - table w0|: "
              f"median {err.median():.4f}, mean {err.mean():.4f}, <0.01: {(err < 0.01).mean():.1%}")
        bins = pd.cut(active["W5_prev"], [0, 1, 2, 4, 6, 8, 10, 12, 17])
        print(err.groupby(bins, observed=True).describe()[["count", "50%", "mean"]].round(4))
        print(f"per-player values: {out}")
