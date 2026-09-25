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
uv run swisstennis-ranking evaluate --date 2025-10-01  # reproduce a published list and compare
uv run swisstennis-ranking evaluate --date 2025-10-01 --predict  # same, using only prior knowledge
uv run swisstennis-ranking evaluate --date 2026-10-01 --predict  # a future list
uv run swisstennis-ranking evaluate --date 2026-09-01 --predict  # a monthly list
uv run pytest
```

## Algorithm

`ranking.py` holds the formula, `evaluate.py` selects each list's matches, derives the inputs and
compares with the published list. Two modes:

- **reproduce** (default): may read facts off the published list that no rule predicts — the w0
  interpolation points, the values of new/evaluated players, the category bounds.
- **predict** (`--predict`): only what is known before the list; works for any date.

Rules:

- The official lists (1 April, 1 October) count the results of the two periods before them
  (1 Apr – 30 Sep, 1 Oct – 31 Mar). A **monthly list** is the next official list computed early:
  same w0 (previous official list) and window start, results up to its date. Codes S/W win, N/Z
  loss (W/Z = retirement); walkovers 0/1 don't count.
- W = ½[ln(e^w0 + Σe^wᵢ) − ln(e^−w0 + Σe^−wⱼ)], R = ⅙[… + …], C = W + R; 5 passes, opponents
  valued with their W of the previous pass; per 6 matches one loss (max 4) vs the weakest
  opponent is ignored.
- **w0 from W5** ("interpolation linéaire", `reference/Interpolieren.xlsx`): piecewise-linear
  between the previous list's category means of W, mapped R8=1 … R1=8, N4=9 … N1=12; below R8
  0.75. Active players start ≥ 1.0. Reproduce fits the points per list and gender from inactive
  Swiss players (W = w0); predict uses the means (≈ 0.001 off).
- **Foreigners** (`lzh_kontingent` = 0, Art. 9.2) from R1 up are not computed: they keep W5, their
  previous rank/category, and C of the Swiss player at that rank. Foreign results (source 610)
  store a category mean as opponent value; it is replaced by this list's mean.
- **No-shows** (Art. 5.8): > 3 Swiss tournaments with a walkover loss (code 0) in the window
  → C − 0.3 (W unchanged).
- **Assigned values** (Art. 6): new players and players "classified by evaluation" (mostly
  returning players, ~200 per April list) are published with one fixed (W, C) pair per gender
  and category. They take no quota slot. Reproduce reads them from the published list; predict
  computes everyone.
- **Categories** (Art. 3): per gender, Swiss players ranked by (C, W) fill the quotas
  (M 10/30/70/…/30770, F 10/24/45/…/11824 cumulative); foreigners and assigned players get the
  rank their value would have without taking a slot. The pool seems to be the players licensed on the list
  date, only known for the current list, so reproduce reads the bounds off the published
  categories (best-separating cut) and predict uses the quotas on the licensed players.

Results, reproduce (lists 2021-10 … 2026-04): C exact to 0.001 for 95–97% of players, same
category 99.9%, mean |ΔC| ≈ 0.002. The rest comes from values we cannot know: opponent values
of foreign results (snapshots), assigned values of foreign N players, and who Swiss Tennis
reclassifies by evaluation.

Results, predict: same category 97–98% on October lists, 90–92% on April lists (players returning
for the summer are reclassified by evaluation, which no rule predicts); C exact 55–92%. Monthly
lists of one R1/R2 player (Nov 2025 – Sep 2026): all categories and match counts right, W and C
within 0.005–0.03, rank within 9.

`reference/` keeps the original notebooks, the rules and Swiss Tennis' explanations.
