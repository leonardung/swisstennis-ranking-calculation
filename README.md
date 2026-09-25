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
uv run swisstennis-ranking scrape --since 2019-01-01   # resumable: cached per player in data/raw/
uv run swisstennis-ranking periods                     # published lists found in the history
uv run swisstennis-ranking calibrate --date 2025-04-..  # recover w0 from published values, fit w0 = a*W5 + b
uv run swisstennis-ranking evaluate --date 2025-04-.. --w0-slope A --w0-intercept B
uv run pytest
```

## Algorithm

`ranking.py` holds the formula, `evaluate.py` selects the period's matches and compares.

- Results of the two periods before the list count (1 Apr – 30 Sep, 1 Oct – 31 Mar).
- W = ½[ln(e^w0 + Σe^wᵢ) − ln(e^−w0 + Σe^−wⱼ)], R = ⅙[… + …], C = W + R.
- 5 passes; opponents are valued with their W from the previous pass (pass 1: their w0).
- Per 6 matches one loss (max 4) against the weakest opponent is ignored.
- w0 is "derived from" the previous period's W5; the exact rule is not published, so it is
  fitted by `calibrate`. For players without matches W = w0, which gives the mapping exactly.

`reference/` keeps the original notebooks for comparison.
