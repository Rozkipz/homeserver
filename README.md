<center>Just some charts and configs and stuff.

<center>Hopefully it can be useful if you stumble across it and need it.

---

## Architecture (quick reference)

Everything here is Docker Compose. (An older Helm chart tree lived in this repo until
2026-09-12; nothing ran on Kubernetes, so it was removed — see git history if you want it back.)

Two hosts on a Tailscale tailnet:

- **Media host** — runs the main `compose.yml` stack (Immich, Plex, the *arr apps, etc.) on the home network.
- **Edge host** — runs `server-compose.yaml`: a Caddy reverse proxy + Tailscale that exposes selected services publicly and proxies them back to the media host over the tailnet. Its Caddy config is `Caddyfile` in this repo.

### Deploying

Edit configs in this repo, then copy the file to the host and apply:

```sh
scp compose.yml <media-host>:~/homeserver/compose.yml
ssh <media-host> 'cd ~/homeserver && docker compose up -d'
```

Don't use `--remove-orphans` — it would remove the Tailscale helper containers that aren't defined in `compose.yml`.

On the edge host the live files are **not** the repo checkout: compose runs from `~/compose.yaml` and Caddy reads `~/services/caddy/Caddyfile`. Deploy with:

```sh
scp server-compose.yaml <edge-host>:~/compose.yaml
scp Caddyfile <edge-host>:~/services/caddy/Caddyfile
ssh <edge-host> 'docker compose up -d && docker exec caddy caddy reload --config /etc/caddy/Caddyfile'
```

Services that live on the media host but are exposed publicly (Immich, Jellyseerr, AudioBookRequest, Audiobookshelf, Headplane) need both a `socat` hop in `tailnet-relay` and a `reverse_proxy http://tailscale:<port>` block in the Caddyfile. Tailnet-only services (e.g. Tautulli on 8181) get neither.

### Dashboard (Homepage)

`homepage/` holds the [Homepage](https://gethomepage.dev) config — tailnet-only on port 80 of the media host (http://100.64.0.3/). Tiles are static entries in `homepage/services.yaml` (with `container:` for a live docker status dot, and `widget:` for app stats); plain links, including external sites, go in `homepage/bookmarks.yaml`. No secrets in the repo: widgets reference `{{HOMEPAGE_VAR_*}}`, which `homepage/collect-keys.sh` fills into the host's `.env` from each app's own config file. Deploy config changes with:

```sh
scp homepage/*.yaml <media-host>:~/services/homepage/config/
```

Homepage hot-reloads its YAML, so no restart is needed for config edits. After adding a service that needs a key: `scp homepage/collect-keys.sh <media-host>:~/homeserver/homepage/ && ssh <media-host> 'sh ~/homeserver/homepage/collect-keys.sh && cd ~/homeserver && docker compose up -d homepage'`.

### Host health & updates

- **Scrutiny** (`http://100.64.0.3:8080`, tailnet-only) watches SMART on all 13 disks — the 11
  data drives plus the two Kingston SSDs in the `md0` root mirror. It exists because the host's
  `smartd` is configured to mail `root` and there is no MTA installed, so drive failures were
  previously silent. The omnibus image bundles the web UI, InfluxDB and the collector; the
  collector needs `cap_add: SYS_RAWIO` plus the explicit `devices:` list in `compose.yml`.
  Scrutiny keys history off each drive's WWN, so it survives `/dev/sdX` reshuffling across
  reboots — the `devices:` list just has to keep naming every disk. **Add a disk, add it there.**

  Scrutiny does not notify anywhere yet. Until a sink (ntfy/Discord/email) is configured in
  `~/services/scrutiny/config/scrutiny.yaml`, it is a dashboard you have to actually open.

- **Watchtower** pulls and recreates images nightly at 04:00 (after the 03:00 Immich DB dump,
  before the 04:30 person-sync cron), with `WATCHTOWER_CLEANUP=true` so superseded layers don't
  pile up. It uses the `nicholas-fedor` fork — upstream `containrrr/watchtower` has had no
  release since Nov 2023.

  It needs the real read-write Docker socket, so it cannot go through the read-only `dockerproxy`
  sidecar Homepage uses (that one sets `POST=0` deliberately).

  **The Immich stack is opted out** via `com.centurylinklabs.watchtower.enable: "false"` on all
  four containers: server and DB have to move in lockstep and releases carry breaking schema
  migrations. Update it by hand after reading the release notes. Remove those labels to include
  it. Note the labels only take effect once a container is *recreated*, not merely restarted.

  Watchtower's metrics API feeds the Homepage tile. It shares one secret with Homepage:
  `WATCHTOWER_HTTP_API_TOKEN` in `.env` (`openssl rand -hex 16`), read by both services.

- **SMART self-tests**: `scripts/setup-smart-selftests.sh`, run as root on the host. Schedules one
  extended (full-surface) test per disk per month — one disk per night at 01:00 on days 1-13, so no
  two ever run at once — plus a short test Sundays at 05:00. Scrutiny's collector reads the
  self-test log every 6h, so results land on the dashboard.

  This exists because SMART *attributes alone did not catch* the bad sector on drive9. Attribute 198
  `Offline_Uncorrectable` read 0 because automatic offline collection had never been enabled, and the
  drive had had three self-tests in 6.6 years — the last one 22,000 hours earlier. Nothing re-reads
  archived media, so only a full surface scan finds cold-data rot. The config sets `-o on` to fix the
  first half of that and schedules the scans to fix the second.

  Devices are named by `/dev/disk/by-id/ata-*` so the per-disk schedule survives `/dev/sdX`
  reshuffling. **Add a disk, add it there** (and to Scrutiny's `devices:` list in `compose.yml`).

  The script also installs `/etc/smartmontools/run.d/20syslog`, because there is no MTA on this host
  and smartd's default `10mail` alert therefore does nothing. Read alerts with
  `journalctl -t smartd-alert`.

- **Signal alerting**: `signal-cli-rest-api` (service `signal-api`, tailnet-only on port 8090) is a
  bridge, not a bot — monster is a **linked device on your own Signal account**, so it sends as you.
  Alerts go to the "home server" group. Number and group ID live in `.env` as `SIGNAL_NUMBER` /
  `SIGNAL_GROUP_ID`, with `SIGNAL_SHOUTRRR_URL` composed from them and shared by both consumers:

  - **Scrutiny** — `SCRUTINY_NOTIFY_URLS`. Test it with `curl -XPOST localhost:8080/api/health/notify`.
  - **Watchtower** — `WATCHTOWER_NOTIFICATION_URL`, with a template that emits nothing when there is
    nothing to report, so you don't get a nightly "0 updated" message and learn to tune the channel out.
  - **smartd** — `/etc/smartmontools/run.d/20syslog` curls `127.0.0.1:8090/v2/send` directly. It runs
    on the host, outside Docker, so it uses the published port rather than Docker DNS.

  `disabletls=yes` on the shoutrrr URL is **required**: its signal service defaults to HTTPS and
  `signal-api` speaks plain HTTP on `internal`.

  Relinking, if the device is ever unlinked: do **not** use `/v1/qrcodelink` — it returns the QR and
  then exits, leaving your phone talking to a provisioning UUID nobody is listening on ("network
  error" in the app). Run `docker exec signal-api signal-cli link -n monster` as a persistent
  process, render its `sgnl://` URI with `qrencode`, and scan promptly — the provisioning socket
  expires in about a minute. Note `curl https://chat.signal.org` failing certificate validation is
  **normal**: Signal signs it with their own private CA, which signal-cli bundles and curl doesn't.

  The linked-device keys in `~/services/signal-api/config` are worth having in the backup set —
  losing them means relinking from the phone.

- **Automatic security updates**: `scripts/setup-unattended-upgrades.sh`, run as root on the host.
  Security-only, no automatic reboot (this box serves media and holds the sshfs mount to mill),
  so watch for `/var/run/reboot-required` and reboot deliberately. mill already had this.

### Storage & Immich

- Data drives are independent filesystems mounted at `/mnt/drive1` … `/mnt/driveN` (not pooled/RAID).
- **Immich photos** live in `/mnt/drive1/images` (bind-mounted to `/usr/src/app/upload`, Immich's default media path).
- The `immich-backup` service mirrors `/mnt/drive1/images` → `/mnt/drive3/images` every 6h (`rsync -aH --delete`, source read-only) for drive-failure redundancy. Immich's daily DB dump (written into `images/backups/`) rides along, so photos and the DB backup exist on both drives.
- The immich-server image declares `VOLUME /data`; leave it as an anonymous volume. Changing an image-declared volume's mount needs a full `docker compose rm -sf immich-server` then `up -d` (a plain recreate copies the old mount forward).
