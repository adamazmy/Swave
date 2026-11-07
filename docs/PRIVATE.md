# Running Swave for friends (private instance)

Swave runs on this PC in production mode (`docker-compose.private.yml`: API, web, Cloudflare
Tunnel) and is reached at your domain through Cloudflare. Nobody gets in without passing
**Cloudflare Access** (a one-time PIN emailed to an address on your allowlist), and the API
checks Cloudflare's signed token on every request too. Each friend's events, Spotify login and
"delete my data" are their own (keyed by their email).

**Two databases, one Postgres container.** Development (`make dev`) uses `swave`; the private
instance uses its own database **`swave_private`**, through the role `swave_private`, which is
not a superuser and cannot connect to `swave`. It holds the friends' data, its own caches and a
**copy** of the music index, so dev experiments (rebuilding or reinstalling the index) never
reach friends until you run `make private-index-sync`.

All commands run from the repo folder (`C:\Dev\Swave`), with Docker Desktop running.

## Start / stop

| | Command |
|---|---|
| Start (or restart after a reboot) | `make private-up` |
| Stop (friends see a Cloudflare error page) | `make private-down` |
| Is it working? | `make private-logs` (Ctrl-C to leave), or open your domain |

The PC must be on, awake and running Docker for friends to use Swave. The containers restart on
their own after Docker restarts. Development (`make dev`) can run at the same time.

## Inviting a friend (checklist)

- [ ] **Let them in:** Cloudflare Zero Trust → Access controls → Policies → **Friends** → add
      their email under *Include → Emails* → Save.
- [ ] **Saving straight to Spotify** (optional): developer.spotify.com → Dashboard → Swave app →
      **User Management** → add their name and the email of *their Spotify account*. Max 5
      people including you; everyone else uses *Copy for Spotify*, the CSV or the song links.
- [ ] **Send them the link** (`https://swave.party`) and tell them: enter your email, then the
      login code Cloudflare emails you, and **check spam for the first login code**.

## Removing a friend

1. Ask them to press **Delete my data** first (footer → *What Swave stores*), or run
   `DELETE FROM events WHERE user_id = '<email>'` (and the same for `preview_attempts`,
   `spotify_auth`) yourself.
2. Cloudflare Zero Trust → Access controls → Policies → **Friends** → remove their email →
   Save. That stops their next login; to cut them off at once, also revoke their session
   (Zero Trust → My Team → Users → the user → Revoke sessions).
3. Spotify dashboard → User Management → remove them, if they were on it.

## Deploy an update

```
git pull                 # or: commit your changes on main
make test                # everything green first
make private-deploy      # backup → rebuild api + web → health check → prints the deployed commit
```

`make private-deploy` refuses unless you're on `main` with no uncommitted changes (so the commit
it prints is what runs; `make private-deploy FORCE=1` overrides). It backs up the private
database first and stops if that fails, keeps the running images for rollback, restarts only
the API and web (≈ 30 s of downtime; the database and tunnel stay up), then checks that the API
is healthy and reports the new commit at `/health`, that the web answers, and that the domain
still redirects to the Access login.

If the new code changed the database schema at startup, the deploy prints **`DB MIGRATION
RAN`** with the added or removed columns. Something wrong? `make private-rollback` puts the
previous images back (≈ 30 s), but **does not undo a migration**: migrations must stay
backward-compatible (add, don't rename or drop), so the previous code still runs on the new schema.

`scorers.json` (the scorer weights) is mounted into the API: commit the change, then
`make private-deploy`.

## Update the music index for friends

Index work (`make lb-install`, `make artist-index`, `make discogs-styles`…) only changes the dev
database. When you're happy with it there:

```
make private-index-sync  # copies the index into swave_private: 15–30 min of loading while the
                         # site keeps serving, then a seconds-long swap (downtime is printed)
```

It needs ~11 GB of free disk while the new copy sits beside the old one.

## Backups

Set `SWAVE_BACKUP_DIR` in `.env.private` to a folder that leaves this PC, e.g.
`C:\Users\<you>\OneDrive\SwaveBackups` (OneDrive syncs it to the cloud, so a dead PC doesn't
take the backups with it). Then `make backup` backs up now, and
`scripts\install-backup-task.ps1` (run once) schedules it nightly at 03:30, keeping 7 days
(log: `%LOCALAPPDATA%\Swave\backup.log`). If the PC is off or asleep at 03:30, the backup runs
as soon as it's back on (Docker must be running). Check it:
`Get-ScheduledTask "Swave nightly backup" | Get-ScheduledTaskInfo` (LastTaskResult 0 = success).

Backed up (from `swave_private`): events, preview attempts and the lookup caches. **Spotify logins are left out** by
default (they are refresh tokens, and the folder syncs to a cloud drive): after a restore each
person reconnects Spotify once. `SWAVE_BACKUP_TOKENS=1` includes them. Not backed up: the music
index, which is copied from dev again with `make private-index-sync`. Development data (the
`swave` database) isn't in these backups: `scripts\backup.ps1 -Database swave` for that.

Restore (stop the API first): `make private-down`, then
`powershell -File scripts\restore.ps1 -File <folder>\swave-YYYYMMDD-HHMM.dump` (into `swave_private`),
then `make private-up`.

## Settings

`.env.private` (never committed; template: `private.env.example`) holds the dev database
login (for admin scripts), the private role's password (`PRIVATE_DB_PASSWORD`, generated by
`scripts\private-db-setup.ps1`), the Spotify client id and redirect URI, the tunnel token, the
Access team domain + AUD tag and the backup folder.

The private database was created once with `scripts\private-db-setup.ps1` (`-Create`: role,
database, caches, index; `-MoveUsers`: friends' rows from dev, with the site stopped;
`-CleanupDev`: delete them from dev once verified).
