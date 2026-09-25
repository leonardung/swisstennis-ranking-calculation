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

`ranking.py` holds the formula, `evaluate.py` selects each list's matches, derives the inputs and
compares with the published list.

- Results of the two periods before the list count (1 Apr – 30 Sep, 1 Oct – 31 Mar). Codes S/W
  win, N/Z loss (W/Z = retirement); walkovers 0/1 don't count.
- W = ½[ln(e^w0 + Σe^wᵢ) − ln(e^−w0 + Σe^−wⱼ)], R = ⅙[… + …], C = W + R; 5 passes, opponents
  valued with their W of the previous pass; per 6 matches one loss (max 4) vs the weakest
  opponent is ignored.
- **w0 from W5** ("interpolation linéaire", `reference/Interpolieren.xlsx`): piecewise-linear
  between the previous list's category means of W, mapped R8=1 … R1=8, N4=9 … N1=12; below R8
  0.75. Fitted per list and gender from inactive Swiss players (W = w0). Active players start ≥ 1.0.
- **Foreigners** (`lzh_kontingent` = 0, Art. 9.2) from R1 up are not computed: they keep W5, their
  previous rank/category, and C of the Swiss player at that rank. Foreign results (source 610)
  store a category mean as opponent value; it is replaced by this list's mean.
- **No-shows** (Art. 5.8): > 3 Swiss tournaments with a walkover loss (code 0) in the window
  → C − 0.3 (W unchanged).
- **Assigned values** (Art. 6): new players and players "classified by evaluation" (mostly
  returning players, ~200 per April list) are published with one fixed (W, C) pair per gender
  and category. These are read from the published list (external input, not predicted).
- Categories: our C against the best-separating published C cut per gender and category
  (foreigners are outside the quotas, so quota ranking doesn't reproduce the list).

Results (all lists 2020-10 … 2026-04): C exact to 0.001 for 94–97.5% of players, same category
99.8–99.9%, mean |ΔC| ≈ 0.002. The rest comes from values we cannot know: opponent values of
foreign results (snapshots), assigned values of foreign N players, and who Swiss Tennis
reclassifies by evaluation.

`reference/` keeps the original notebooks, the rules and Swiss Tennis' explanations.
