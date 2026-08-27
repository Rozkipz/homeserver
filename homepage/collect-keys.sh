#!/bin/sh
# Populate HOMEPAGE_VAR_* in ~/homeserver/.env from the apps' own config files on the media host.
# Run ON THE HOST (monster) after adding/rotating a service key, then `docker compose up -d homepage`.
# Idempotent: existing values are replaced in place. Prints variable names only, never values.
set -eu
ENV="$HOME/homeserver/.env"; S="$HOME/services"
set_var() {
  if [ -z "$2" ]; then echo "skip $1 (not found)"; return; fi
  if grep -q "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else printf '%s=%s\n' "$1" "$2" >> "$ENV"; fi
  echo "set  $1"
}
xml_key() { sed -n 's:.*<ApiKey>\([^<]*\)</ApiKey>.*:\1:p' "$1" 2>/dev/null | head -1; }
set_var HOMEPAGE_VAR_SONARR_KEY     "$(xml_key "$S/sonarr/config/config.xml")"
set_var HOMEPAGE_VAR_RADARR_KEY     "$(xml_key "$S/radarr/config/config.xml")"
set_var HOMEPAGE_VAR_PROWLARR_KEY   "$(xml_key "$S/prowlarr/config/config.xml")"
set_var HOMEPAGE_VAR_BAZARR_KEY     "$(sed -n 's/^ *apikey: *//p' "$S/bazarr/config/config/config.yaml" 2>/dev/null | tr -d "'\"" | head -1)"
set_var HOMEPAGE_VAR_TAUTULLI_KEY   "$(sed -n 's/^api_key = //p' "$S/tautulli/config/config.ini" 2>/dev/null | head -1)"
set_var HOMEPAGE_VAR_JELLYSEERR_KEY "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["main"]["apiKey"])' "$S/jellyseerr/config/settings.json" 2>/dev/null || true)"
set_var HOMEPAGE_VAR_PLEX_TOKEN     "$(sed -n 's/.*PlexOnlineToken="\([^"]*\)".*/\1/p' "$S/plex/config/Library/Application Support/Plex Media Server/Preferences.xml" 2>/dev/null | head -1)"
# Audiobookshelf: reuse the key the audiobookshelf-scan sidecar already has in .env.
set_var HOMEPAGE_VAR_ABS_KEY        "$(sed -n 's/^ABS_API_KEY=//p' "$ENV" | head -1)"
