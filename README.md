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

Updating an existing download: `scrape --recent-days 45` fetches again the players with a result
dated in the last 45 days, or everyone once a new official list is out (`data/raw/season`).

## Web UI

Search any player and see the official list next to today's calculated list (the monthly list at
today's date, predict mode), every match of the window with the points it brought (the player's
C and W with minus without that match), discarded losses and results leaving after the next
official list, a win/loss simulator against any opponent (full recalculation of the list) and
match statistics (sets, games, tiebreaks, deciding sets, opponents' level, head to head…), in
English or French.

```bash
PORT=8080 docker compose up -d --build   # http://localhost:8080 (default port 8000)
```

The container reads `./data` (run a first `scrape` beforehand, or the container does it on first
start, which takes hours) and updates it every night at `SCRAPE_TIME` (default `03:00` Zurich
time, empty to disable) with the credentials from `.env`; today's list is recomputed afterwards.

Cloudflare Tunnel: add `COMPOSE_PROFILES=tunnel` and `TUNNEL_TOKEN=<token>` to `.env` and point
the tunnel's public hostname to `http://app:8000` (or your `PORT`). The UI has no login of its
own: protect the hostname with Cloudflare Access if it should not be public.

Analytics (self-hosted [Umami](https://umami.is), optional):

1. In `.env`: add `analytics` to `COMPOSE_PROFILES` (`tunnel,analytics`) and set
   `UMAMI_APP_SECRET` (`openssl rand -hex 32`); `docker compose up -d`.
2. Open the dashboard on `http://<host>:3000` (`UMAMI_PORT`), log in with `admin` / `umami`,
   change the password, and add a website: its id is in the website's settings.
3. The tracker must be public: add a tunnel public hostname (e.g. `stats.example.com`) pointing to
   `http://umami:3000`, which also gives the dashboard from anywhere.
4. In `.env`: `UMAMI_SCRIPT_URL=https://stats.example.com/radar.js` and `UMAMI_WEBSITE_ID=<id>`;
   `docker compose up -d app`.

It records page views (home, `/player/<id>/<tab>` titled with the player's name) and the events
`simulate` (player, opponent) and `language`, with no cookies.

Without Docker: `uv run swisstennis-ranking serve --port 8000` (API at `/api`, UI from
`web/dist`: `cd web && npm ci && npm run build`; `npm run dev` for development, proxying `/api`
to port 8000).

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
  Swiss players (W = w0); predict uses the means over the licensed players on the new list
  (≈ 0.001 off). Past lists in the history also carry players unlicensed then, so a backtest
  takes those licensed today or with results in the window (the means drift by up to 0.02 over
  all of them; the proxy weakens for lists several years old).
- **Foreigners** (`lzh_kontingent` = 0, Art. 9.2) from R1 up are not computed: they keep W5, their
  previous rank/category, and C of the Swiss player at that rank. Foreign results (source 610)
  store a category mean as opponent value; it is replaced by this list's mean.
- **No-shows** (Art. 5.8): > 3 Swiss tournaments with a walkover loss (code 0) in the window
  → C − 0.3 (W unchanged).
- **Assigned values** (Art. 6): new players and players "classified by evaluation" (mostly
  returning players, ~200 per April list) are published with one fixed (W, C) pair per gender
  and category. They take no quota slot. Reproduce reads them from the published list. Predict
  places new players without results in R9 (~94% are) with the R9 pair of the same list a year
  earlier (the pair is seasonal and stable: C within 0.001 since 2024), outside the quota; who
  is classified by evaluation (mostly promotions of 1–3 categories) shows no pattern in prior
  data, so they are computed.
- **Categories** (Art. 3): per gender, Swiss players ranked by (C, W) fill the quotas
  (M 10/30/70/…/30770, F 10/24/45/…/11824 cumulative); foreigners and assigned players get the
  rank their value would have without taking a slot. The pool seems to be the players licensed on the list
  date, only known for the current list, so reproduce reads the bounds off the published
  categories (best-separating cut) and predict uses the quotas on the licensed players.

Results, reproduce (lists 2021-10 … 2026-04): C exact to 0.001 for 95–97% of players, same
category 99.9%, mean |ΔC| ≈ 0.002. The rest comes from values we cannot know: opponent values
of foreign results (snapshots), assigned values of foreign N players, and who Swiss Tennis
reclassifies by evaluation.

Results, predict (2021-10, 2023-04, 2024-04, 2025-04, 2025-10, 2026-04): C exact
68.8/65.7/84.7/94.6/94.4/96.1%, same category 97.9/95.8/95.8/96.1/97.6/99.0%. The rest: players
classified by evaluation and new players outside R9 (≈ 500 per April list), and, on older lists,
the unknown licence holders of the time. Monthly
lists of one R1/R2 player (Nov 2025 – Sep 2026): all categories and match counts right, W and C
within 0.005–0.03, rank within 9.

`reference/` keeps the original notebooks, the rules and Swiss Tennis' explanations.
