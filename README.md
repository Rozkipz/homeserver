<center>Just some charts and configs and stuff.

<center>Hopefully it can be useful if you stumble across it and need it.

---

## Architecture (quick reference)

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

### Storage & Immich

- Data drives are independent filesystems mounted at `/mnt/drive1` … `/mnt/driveN` (not pooled/RAID).
- **Immich photos** live in `/mnt/drive1/images` (bind-mounted to `/usr/src/app/upload`, Immich's default media path).
- The `immich-backup` service mirrors `/mnt/drive1/images` → `/mnt/drive3/images` every 6h (`rsync -aH --delete`, source read-only) for drive-failure redundancy. Immich's daily DB dump (written into `images/backups/`) rides along, so photos and the DB backup exist on both drives.
- The immich-server image declares `VOLUME /data`; leave it as an anonymous volume. Changing an image-declared volume's mount needs a full `docker compose rm -sf immich-server` then `up -d` (a plain recreate copies the old mount forward).
