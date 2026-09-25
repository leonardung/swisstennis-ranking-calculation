# swisstennis-ranking

Scrape Swiss Tennis players, their ranking history and all their single matches, recompute the
ranking with the official algorithm (DCL 2025, Art. 5 — see `reference/2025_klassierungsrichtlinien_f.pdf`)
and compare it with the published values.

## Setup

```bash
uv sync
cp .env.example .env   # fill in your mytennis.ch login (needs Google Chrome for the headless login)
```

## Usage

```bash
uv run swisstennis-ranking scrape --since 2018-04-01   # resumable: cached per player in data/raw/
uv run swisstennis-ranking periods                     # published lists found in the history
uv run swisstennis-ranking evaluate --date 2025-10-01  # recompute a list and compare
uv run swisstennis-ranking calibrate --date 2025-10-01 # check the w0 table against w0 inverted from active players
uv run pytest
```

## Algorithm

`ranking.py` holds the formula, `evaluate.py` selects the period's matches and compares.

- Results of the two periods before the list count (1 Apr – 30 Sep, 1 Oct – 31 Mar).
- W = ½[ln(e^w0 + Σe^wᵢ) − ln(e^−w0 + Σe^−wⱼ)], R = ⅙[… + …], C = W + R.
- 5 passes; opponents are valued with their W from the previous pass (pass 1: their w0).
- Per 6 matches one loss (max 4) against the weakest opponent is ignored.
- Result codes: S/W win, N/Z loss (W/Z = retirement); 0/1 walkovers without play don't count.
- RankingHistory row dated 1 Apr / 1 Oct = list using results of the year before that date;
  its `games` equals our match count for 99.8% of players. The current list comes from the licence table.
- w0 is "derived from" W5 but the rule is unpublished. It is a monotone per-gender function of W5
  (ratio ~0.83-0.92, floor 0.75), revealed exactly by players without matches (W = w0), and
  learned per list from them. Players with matches start at least at 1.0 (as do new players).
- Categories: our C against the published per-gender C thresholds (foreigners are listed
  outside the quotas, Art. 9.2, so quota ranking doesn't reproduce the list).

Results (players with a previous value): C exact to 0.001 for ~60-75%, same category ~97-98.5%.
Known gaps: new players (value assigned by Swiss Tennis, Art. 6.2), N1-R1 (international
results are not in the scraped matches, Art. 4.2), April lists are noticeably worse than October lists.

`reference/` keeps the original notebooks for comparison.
