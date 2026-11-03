"""The registered users of tennis-radar.ch: totals, then one line per account.

    python3 scripts/users.py            # emails hidden
    python3 scripts/users.py --emails   # with emails

Runs over SSH in the production app container (read-only: it only reads data/accounts.sqlite and
data/players.parquet). HOST and APP_DIR below say where.
"""

import subprocess
import sys

HOST = "tennis-radar"
APP_DIR = "~/tennis-radar"

# runs inside the container (pandas is there); SHOW_EMAILS is set before it
REPORT = r'''
import sqlite3, datetime as dt
import pandas as pd

db = sqlite3.connect("file:data/accounts.sqlite?mode=ro", uri=True)
players = pd.read_parquet("data/players.parquet").drop_duplicates("personId").set_index("personId")

def who(pid):
    if pid is None:
        return "-"
    if pid not in players.index:
        return f"#{pid}"
    p = players.loc[pid]
    return f"{p.firstname} {p.lastname} ({p.classification}, {p.ageCategoryRedundant})"

def when(t):
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"

users = db.execute("""
    select id, email, verified, google_sub is not null, password is not null, player_id, created, last_seen,
           (select count(*) from favourites f where f.user_id = u.id)
    from users u order by id
""").fetchall()
now = dt.datetime.now().timestamp()
verified = [u for u in users if u[2]]
print(f"Accounts: {len(users)}  (confirmed {len(verified)}, unconfirmed {len(users) - len(verified)})")
print(f"  with a player (me): {sum(u[5] is not None for u in users)}   with favourites: {sum(u[8] > 0 for u in users)}"
      f"   Google: {sum(u[3] for u in users)}   password: {sum(u[4] for u in users)}")
print(f"  seen in the last 7 days: {sum(bool(u[7]) and now - u[7] < 7 * 86400 for u in users)}")
print()
rows = [("#", "created", "last seen", "sign-in", "me", "fav") + (("email",) if SHOW_EMAILS else ())]
for uid, email, ok, google, pw, pid, created, seen, favs in users:
    how = "+".join(x for x, on in (("google", google), ("password", pw)) if on) or "-"
    rows.append((str(uid), when(created), when(seen), how if ok else "UNCONFIRMED", who(pid), str(favs))
                + ((email,) if SHOW_EMAILS else ()))
widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
for i, r in enumerate(rows):
    print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
    if i == 0:
        print("  ".join("-" * w for w in widths))
'''


def main() -> None:
    code = f"SHOW_EMAILS = {'--emails' in sys.argv[1:]}\n{REPORT}"
    cmd = f"cd {APP_DIR} && docker compose exec -T app python -"
    sys.exit(subprocess.run(["ssh", HOST, cmd], input=code, text=True).returncode)


if __name__ == "__main__":
    main()
