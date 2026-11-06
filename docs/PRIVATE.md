# Running Swave for friends (private instance)

Swave runs on this PC in production mode (`docker-compose.private.yml`: API, web, Cloudflare
Tunnel) and is reached at your domain through Cloudflare. Nobody gets in without passing
**Cloudflare Access** (a one-time PIN emailed to an address on your allowlist), and the API
checks Cloudflare's signed token on every request too. Each friend's events, Spotify login and
"delete my data" are their own (keyed by their email).

All commands run from the repo folder (`C:\Dev\Swave`), with Docker Desktop running.

## Start / stop

| | Command |
|---|---|
| Start (or restart after a reboot) | `make private-up` |
| Stop (friends see a Cloudflare error page) | `make private-down` |
| Is it working? | `make private-logs` (Ctrl-C to leave), or open your domain |

The PC must be on, awake and running Docker for friends to use Swave. The containers restart on
their own after Docker restarts. Development (`make dev`) can run at the same time.

## Add or remove a friend

1. **Cloudflare** (lets them in): Zero Trust → Access → Applications → Swave → Policies →
   "Friends" → add or remove their email under *Include → Emails* → Save. Takes effect at
   their next login; to cut someone off at once, also revoke their session
   (Zero Trust → My Team → Users → the user → Revoke sessions).
2. **Spotify** (only if they should save playlists to Spotify, max 5 people including you):
   developer.spotify.com → Dashboard → Swave app → User Management → add their name and the
   email of their Spotify account. Everyone else uses *Copy for Spotify*, the CSV or the song
   links instead.
3. When someone leaves, ask them to press **Delete my data** first (footer → *What Swave
   stores*), or run `DELETE FROM events WHERE user_id = '<email>'` (and the same for
   `preview_attempts`, `spotify_auth`) yourself.

## Deploy an update

```
git pull                 # or: commit your changes on this PC
make test                # everything green first
make private-update      # rebuilds and restarts the API and web (≈ 1–2 min; the DB stays up)
```

`scorers.json` (the scorer weights) is mounted into the API: after editing it, restart the API
with `make private-update`.

## Backups

Set `SWAVE_BACKUP_DIR` in `.env.private` to a folder that leaves this PC, e.g.
`C:\Users\<you>\OneDrive\SwaveBackups` (OneDrive syncs it to the cloud, so a dead PC doesn't
take the backups with it). Then `make backup` backs up now, and
`scripts\install-backup-task.ps1` (run once) schedules it nightly at 03:30, keeping 7 days
(log: `%LOCALAPPDATA%\Swave\backup.log`).

Backed up: events, preview attempts and the lookup caches. **Spotify logins are left out** by
default (they are refresh tokens, and the folder syncs to a cloud drive): after a restore each
person reconnects Spotify once. `SWAVE_BACKUP_TOKENS=1` includes them. Not backed up: the music
index (~20 GB), which is rebuilt with the `make` pipeline.

Restore (stop the API first): `make private-down`, then
`powershell -File scripts\restore.ps1 -File <folder>\swave-YYYYMMDD-HHMM.dump`,
then `make private-up`.

## Settings

`.env.private` (never committed; template: `private.env.example`) holds the database password,
Spotify client id and redirect URI, the tunnel token and the Access team domain + AUD tag.
