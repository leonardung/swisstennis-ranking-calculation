#!/bin/sh
# Umami visitors (one row per session) with the players they looked at, first player first,
# exported from the production Umami database to scripts/out/umami-users-players-<date>.csv (gitignored).
#   sh scripts/umami_users.sh [ssh-host]     (default: tennis-radar)
set -eu
host=${1:-tennis-radar}
out=$(dirname "$0")/out/umami-users-players-$(date +%F).csv
mkdir -p "$(dirname "$0")/out"
ssh "$host" 'cd ~/tennis-radar && docker compose exec -T umami-db psql -v ON_ERROR_STOP=1 -U umami -d umami' > "$out" <<'EOF'
COPY (
with ev as (
  select e.session_id, e.website_id, e.created_at, e.hostname,
         substring(e.url_path from '^/player/(\d+)') pid, e.page_title
  from website_event e
  where e.hostname in ('tennis-radar.leounghome.loan','tennis-radar.ch')
),
names as (
  select pid, max(regexp_replace(page_title, ' · Tennis Radar$', '')) nm
  from ev where pid is not null and page_title like '% · Tennis Radar' group by pid
),
pv as (
  select session_id, pid, min(created_at) first_at, count(*) n
  from ev where pid is not null group by 1,2
),
pl as (
  select pv.session_id,
         count(*) n_players,
         string_agg(coalesce(nm, '#'||pv.pid) || ' (' || pv.pid || ')' || case when n>1 then ' x'||n else '' end, '; ' order by first_at) players,
         (array_agg(coalesce(nm, '#'||pv.pid) order by first_at))[1] first_player,
         (array_agg(pv.pid order by first_at))[1] first_pid,
         min(first_at) first_player_at
  from pv left join names using(pid) group by 1
),
s as (
  select session_id, min(hostname) site, min(created_at) first_seen, max(created_at) last_seen, count(*) events
  from ev group by 1
)
select case when s.site='tennis-radar.ch' then 'tennis-radar.ch' else 'old (leounghome)' end site,
       left(s.session_id::text, 8) user_id,
       to_char(s.first_seen at time zone 'Europe/Zurich','YYYY-MM-DD HH24:MI') first_seen,
       to_char(s.last_seen at time zone 'Europe/Zurich','YYYY-MM-DD HH24:MI') last_seen,
       s.events, se.country, se.city, se.device, se.os, se.browser,
       pl.first_player, pl.first_pid, to_char(pl.first_player_at at time zone 'Europe/Zurich','YYYY-MM-DD HH24:MI') first_player_at,
       coalesce(pl.n_players,0) n_players, pl.players
from s join session se using(session_id) left join pl using(session_id)
order by s.first_seen
) TO STDOUT WITH CSV HEADER;
EOF
echo "$out: $(($(wc -l < "$out") - 1)) visitors"
