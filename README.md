# Sickbay

A self-hosted kidney stone history tracker for a patient and a caregiver.

People with recurring kidney stones are asked the same questions at every new urologist, ER visit and follow-up: how
many stones, how often, how big, what were they made of, what was done, were there infections, how long in hospital.
Sickbay is one private place that holds the whole history: one record per stone episode (an event), filled in during
and after it, with the patient portal's documents attached. It prints a one-page summary for a doctor and shows the
pattern over the years, above all how long it usually is between stones.

**It records history. It gives no medical advice**: no recommendation, risk score or predicted date, anywhere. Medians
and counts of what happened are shown; nothing is projected forward.

Two users sign in through Cloudflare Access; every other request is refused. The design is in
`docs/superpowers/specs/2026-11-14-sickbay-design.md`.

## Development

Python 3.12 or later.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
DEV_EMAIL=alex@example.com python3 app.py
```

The app listens on `http://127.0.0.1:8774` and keeps its database and files in `./data` (git-ignored). `DEV_EMAIL`
admits requests from this machine as that user, and only while no Cloudflare header is present; never set it on a
server. The other settings are listed at the top of `settings.py` and in `.env.example`.

Before every commit:

```sh
python -m pytest -q && ruff check && python .github/scripts/privacy_scan.py
```

## Deploying

Sickbay runs in its own Alpine LXC, listens on `127.0.0.1:8774` only, and is reachable solely through a Cloudflare
Tunnel. It holds health records: nothing may listen on the LAN.

Build and install:

```sh
cp deploy/deploy.env.example deploy/deploy.env   # git-ignored; vim deploy/deploy.env
sh installer/build.sh                            # writes dist/setup-sickbay.sh
# copy dist/setup-sickbay.sh into the container, then in it:
sh setup-sickbay.sh
```

`deploy/deploy.env` holds household defaults (`ALLOWED_EMAILS`, `USERS`, `CF_TEAM_DOMAIN`, `CF_AUD`, `APP_NAME`); they
are used on the first install only. The installer is safe to re-run: it installs `py3-flask py3-gunicorn py3-jwt
py3-cryptography`, `sqlite` and `tar` with `apk`, replaces `/opt/sickbay`, and keeps `/var/lib/sickbay` (the database
and `files/`) and `/etc/conf.d/sickbay`. It adds settings that are missing from the config and never overwrites
existing ones, and it never writes `DEV_EMAIL`. Edit settings with `vim /etc/conf.d/sickbay`, then:

```sh
rc-service sickbay restart
tail -n 40 /var/log/sickbay.log
```

Gunicorn runs as the unprivileged `sickbay` user under OpenRC (`rc-service sickbay status`). The database is migrated
at every start, so an upgrade needs no manual step. `sh setup-sickbay.sh --uninstall` removes the app and service and
keeps data, config and backups.

### Cloudflare Access

1. In Zero Trust, create an Access application for `logbook.example.com` only. Do not reuse another application.
2. Give it one Allow policy whose Include rule is only the two users' emails (Emails selector): no email domain, no
   "everyone".
3. Set the application's session duration (for example 1 to 24 hours; shorter means more sign-ins).
4. Copy the application's AUD tag into `CF_AUD` and your team domain into `CF_TEAM_DOMAIN` in `/etc/conf.d/sickbay`,
   and put the same two emails in `ALLOWED_EMAILS`. The app verifies the Access token itself; Access alone is not
   trusted.

### Tunnel

Run `cloudflared` in the same container and point the public hostname `logbook.example.com` at
`http://127.0.0.1:8774`. Do not run the tunnel elsewhere: that would need the app to listen beyond loopback, which the
installer never does.

Check from another machine on the LAN that `http://<container-ip>:8774` is refused at the socket, and that a third
email is refused by Access.

## Backups and restore

`ops/sickbay-backup` is installed as `/etc/periodic/daily/sickbay-backup` and runs nightly as root. It takes a SQLite
`.backup` of the database into a temporary folder, requires `PRAGMA integrity_check` to say `ok`, then packs that copy
and the `files/` folder into `/var/backups/sickbay/sickbay-YYYY-MM-DD.tar.gz` (mode 600, folder mode 700). Archives
older than 14 days are removed. If anything fails it logs `FAILED` to `/var/log/sickbay-backup.log`, leaves no partial
archive and exits non-zero; earlier archives stay. The installer takes a first backup.

An archive is the whole record: health data and the attached documents. Keep it readable by root only, and treat any
copy of it like the live database.

To restore, in the container:

```sh
rc-service sickbay stop
mkdir /tmp/restore && tar -xzf /var/backups/sickbay/sickbay-YYYY-MM-DD.tar.gz -C /tmp/restore
rm -f /var/lib/sickbay/sickbay.db-wal /var/lib/sickbay/sickbay.db-shm
rm -rf /var/lib/sickbay/files
mv /tmp/restore/sickbay.db /tmp/restore/files /var/lib/sickbay/
chown -R sickbay:sickbay /var/lib/sickbay
rc-service sickbay start
rm -rf /tmp/restore
```

To check an archive without touching the live data, unpack it into a scratch folder and open it with
`sqlite3 /tmp/restore/sickbay.db 'PRAGMA integrity_check;'`.

`/var/backups/sickbay` is the one folder an off-site copy needs. Backups stay on the container until you set that up.
