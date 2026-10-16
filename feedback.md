# Feedback and feature ideas (2026-10-15)

## Sources

The site's feedback form appends to `data/feedback.jsonl`. Two deployments run on the server
(`labeler-app`), each with its own file:

- `~/tennis-radar/data/feedback.jsonl`: 3 messages (2026-10-10 → 10-11)
- `~/swisstennis-ranking-calculation/data/feedback.jsonl`: 4 messages (2026-09-27 → 10-07)

A friend also sent 4 ideas by chat (2026-10-09/10).

Names and emails are left out of this file on purpose. They are in the `.jsonl` files on the
server.

## All site feedback (7)

| # | Date | Page | Message | Request |
|---|---|---|---|---|
| 1 | 09-27 | `/player/…` | "Vraiment sympa, bravo" | – |
| 2 | 09-28 | `/player/…` | "Salut Leonard, super site tu régales 🙌" | – |
| 3 | 10-01 | `/player/…/stats` | (a coach) "Outil extraordinaire pour un entraîneur ! C'est remarquable, les statistiques sont claires et permettent de mettre en évidence tous les temps forts et les temps faibles des joueurs !" | – |
| 4 | 10-07 | `/player/…` | "C'est trop bien" | – |
| 5 | 10-10 | `/player/…/stats` | "Extrêmement bien fait ! Bravo" | – |
| 6 | 10-10 | `/` | (a club member, TC St-Maurice) "Serait-ce possible de pouvoir faire une recherche par club et avoir une liste avec les classements actuels et futurs des joueurs du club ? Ça peut être utile en vue des IC." | **A: club page** |
| 7 | 10-11 | `/tournaments` | "Super outil, même bien meilleur que swiss tennis !" plus two ideas: save a list of favourites to follow friends' progress and be told about their results; easily filter out the tournaments the user can't enter (e.g. women's or kids' tournaments for an adult man) | **B: favourites**, **C: "open to me" filter** |

Takeaways: the tone is very positive. The stats tab gets praised twice (#3, #5) and coaches are an
audience. Interclubs (#6) and "my circle" (#7) are the two use cases nobody covers today.

## Requests from the site

### A. Club page: current and future classifications of a club's players (#6)

**Already there:** `clubs.parquet` maps each player to their club(s) (it's used for the distance
in the suggested tournaments), and every player has the official class and today's calculated
class.

**Proposal:** a club page (`/club/<id>`), reached from the search box (clubs as well as players)
and from the club name on a player's page. It shows the club's players, men and women apart,
sorted by class: official class → calculated class (with the arrow up / down / same), C and W,
and links to each player. Useful for picking interclub teams.

- Effort: medium (club search, one endpoint, one page).
- Decided: current players only (a current licence at the club).

### B. Favourites to follow friends (#7)

**Already there:** nothing (no favourites, no accounts).

**Proposal:** part of the user accounts (see below). Signed-in users get a ☆ on each player page,
and the home page gets a "My players" card: each player's class today vs the official one and
their results since the last visit. Later, notifications by email after the daily scrape.

- Effort: small on top of accounts; notifications medium.

### C. Hide the tournaments I can't enter (#7)

**Already there:** the Tournaments page filters by match type and by age, but the user has to
know which ones to tick. A player's "suggested" events (player page) already check eligibility,
but only for actives' singles of the player's gender.

**Proposal:** an "Open to [player]" filter on the Tournaments page. It keeps the events matching
the player's gender, an age category the player is allowed in (`tournaments/ages.json` has each
category's limits), and a category range that includes the player's level. This generalises the
suggested-events check beyond actives. It's an account feature: for a signed-in user it's on by
default for "me", with a toggle to see everything. Anonymous visitors keep today's manual filters.

- Effort: small to medium (mostly back end; the age rules need care for juniors and seniors).

## Ideas from the friend

### 1. Tournament map ("voir où sont les clubs près de chez soi")

**Already there:** 98.8 % of 5,513 tournaments are geocoded, clubs have coordinates, and the
Tournaments page has a "Near" filter with a distance sort.

**Proposal:** a *List / Map* switch on the Tournaments page. The map shows the same filtered
results with one marker per place, a popup with the tournaments and links, and centres on the
"Near" place. Leaflet with **swisstopo** tiles (decided; the light base map, free, with
attribution).

- Effort: small to medium, almost all front end.

### 2. Leaderboards filtered by level (N1–R3 / R4–R9) as well as men / women

**Already there:** *Upsets of the week* and *Biggest movers* already filter by gender and group
(`highlights.GROUPS`). The leaderboards filter by gender only (`boards = {g: …}`).

**Proposal:** key the boards by gender and group (the current class) and reuse the group switch.

- Effort: small.

### 3. "Tiebreaks won by R5 players, nobody cares; the longest tiebreak is more interesting"

**Data limit:** for 7-6 sets we only have the games (0 of 120k draw scores have tiebreak
points), so only the match tiebreak can be measured, and *Longest super tiebreak* already
exists.

**Proposal (decided):** put it first in its row and **demote** the tiebreak ratio (kept, lower down).

- Effort: trivial (`BOARD_ROWS` order).

### 4. Junior section ("les plus gros exploits … c'est avec les juniors")

**Data:** `players.ageCategoryRedundant`: 18&U 3,992 · 16&U 3,997 · 14&U 4,343 · 12&U 2,342 ·
10&U 865 (about 15.5k junior licences out of 88.7k). Juniors' classifications lag behind their
level, so their upsets are the most telling.

**Proposal (decided: juniors only, no seniors):** an age switch on the home highlights,
*All / U18 / U16 / U14 / U12 / U10*, each category on its own (not cumulative: U16 means the
players in 16&U today, not everyone under 16). It applies to every card (upsets of the week,
movers, leaderboards) and uses the player's current age category.

- The category is today's. The leaderboards now cover the last 12 months for everyone (changed
  2026-10-15, was 5 years), which fits juniors too, so they need no window of their own.
- Precomputing: 6 ages × 2 genders × 2 levels = 24 variants per board. That's fine at TOP = 20,
  but the U10/U12 boards can skip the level split (almost all R7–R9).
- Effort: medium (a filter on every board plus an age switch to lay out on phones; a select fits
  better than a segmented control with 6 options).

## User accounts (proposal, 2026-10-15)

Goal: each visitor has a profile, "me" (a player picked from the list, changeable in settings),
favourites, and default filters (Tournaments only shows what "me" can enter, the leaderboards
and "My players" on the home page). This covers B and C, and it's the basis for notifications.

**Login with the Swiss Tennis account:** not possible as far as we know. mytennis.ch publishes no
OAuth/SSO for third parties, and asking users for their mytennis password is a no-go (we would
hold their credentials, and it likely breaks the terms of use). Worth one email to Swiss Tennis
later if the site grows.

**Claiming a player without verification is fine:** all the data is public, and an account only
holds preferences. Two accounts may pick the same player; nothing private leaks.

**No login, no profile:** "me", favourites and default filters exist only for signed-in users and
are stored on the server. Nothing is kept in the browser. Anonymous visitors get the site as it
is today; the account features show a "Sign in to …" prompt instead.

**Login (decided: both):** email and password (the email verified once by a link; "forgot my
password" emails a reset link) and "Sign in with Google". One account per email: Google and the
password with the same verified email are the same account.
After signing up, the user picks their player ("me") and a display name, both changeable in
settings. The account also enables notifications later (B).

**Filters default to "me":** once a signed-in user has picked their player, every filter starts
on that player's categories: the home highlights' gender, level group (N1–R3 / R4–R9) and junior
age category, and the Tournaments page's gender, age and level range. The user can still change
them; the change only lasts for the page (no saved filter state beyond "me").

**Only on tennis-radar.ch:** the old address keeps working read-only, without accounts. No login
button, and the account API answers 404 there. The new deployment (`~/tennis-radar`) turns
accounts on with a setting in its `.env`. When one app serves both hostnames, a request whose host
is `MOVED_FROM` is treated as accounts off. The moved banner on the old address can mention that
accounts are on the new one. So the two deployments don't need to share data.

**What accounts cost:**
- Personal data under Switzerland's data protection law (nFADP): a privacy page, account
  deletion, backups of the user database (SQLite like `perf.sqlite` is enough at this scale).
- An email provider (e.g. Resend or Brevo free tier) with SPF/DKIM on tennis-radar.ch.
- Sessions in an HttpOnly cookie scoped to tennis-radar.ch, CSRF protection, rate limits on
  login (`RateLimit` exists).

## Suggested order

1. **3 + 2**: quick highlights fixes. *Done and deployed 2026-10-15.*
2. **A**: club page. A concrete use case (interclubs, every spring) and good for word of mouth
   within clubs; no account needed. *Done 2026-10-15: `/club/<id>`, clubs in the header and home
   search, the player's clubs linked on their page, a men / women switch and an interclub age
   filter (45+ includes the older seniors, 16&U the younger juniors).*
3. **Accounts** (tennis-radar.ch only): login, "me", settings. *Built 2026-10-15: email and password
   (the address verified by an emailed link, Resend; "forgot my password" emails a reset link;
   scrypt hashes) and Google, one account per email, sessions of a year renewed on use, `/account`
   (me, followed players, sign out, delete), `/privacy`; data/accounts.sqlite. No display name
   (the player's name serves).*
4. **C + B**: on top of accounts, the "open to me" tournament filter and favourites with the
   "My players" card. *Built 2026-10-15: "Open to me" chip on the Tournaments page (on by
   default, `openTo=<player>`: gender, licence age category, class for singles); ☆ on player
   pages; "My players" on the home page with the results since the previous visit; the home
   filters start from me.*
5. **4**: juniors filter. *Done 2026-10-15: an age select (All, U18 … U10) next to the home
   filters, applied to every card; `/api/highlights?age=U14`, each age precomputed.*
6. **1**: map. *Done 2026-10-15: a List / Map switch on the Tournaments page (swisstopo grey
   national map, Leaflet loaded only with the map, one marker per place with a popup); the map
   shows every matching tournament, not only the page.*
7. **Notifications**, only if favourites get used.

## Decisions (2026-10-15)

- A: current players of the club only.
- Accounts: both login methods, email + password (verified by a link) and Google.
- 3: demote the tiebreak ratio (don't remove it).
- 4: juniors only (no seniors), with U18, U16, U14, U12 and U10 as separate choices.
- 1: swisstopo tiles.
- Leaderboards: the last 12 months for everyone (was 5 years; replaces the 2-year junior window).

