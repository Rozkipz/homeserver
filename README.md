<center>Just some charts and configs and stuff.

<center>Hopefully it can be useful if you stumble across it and need it.

---

## Architecture (quick reference)

Two hosts on a Tailscale tailnet:

- **Media host** — runs the main `compose.yml` stack (Immich, Plex, the *arr apps, etc.) on the home network.
- **Edge host** — runs `server-compose.yaml`: a Caddy reverse proxy + Tailscale that exposes selected services publicly and proxies them back to the media host over the tailnet.

### Deploying

Edit configs in this repo, then copy the file to the host and apply:

```sh
scp compose.yml <media-host>:~/homeserver/compose.yml
ssh <media-host> 'cd ~/homeserver && docker compose up -d'
```

Don't use `--remove-orphans` — it would remove the Tailscale helper containers that aren't defined in `compose.yml`.

### Storage & Immich

- Data drives are independent filesystems mounted at `/mnt/drive1` … `/mnt/driveN` (not pooled/RAID).
- **Immich photos** live in `/mnt/drive1/images` (bind-mounted to `/usr/src/app/upload`, Immich's default media path).
- The `immich-backup` service mirrors `/mnt/drive1/images` → `/mnt/drive3/images` every 6h (`rsync -aH --delete`, source read-only) for drive-failure redundancy. Immich's daily DB dump (written into `images/backups/`) rides along, so photos and the DB backup exist on both drives.
- The immich-server image declares `VOLUME /data`; leave it as an anonymous volume. Changing an image-declared volume's mount needs a full `docker compose rm -sf immich-server` then `up -d` (a plain recreate copies the old mount forward).
