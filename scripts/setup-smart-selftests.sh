#!/bin/sh
# Schedule SMART self-tests for every disk on the media host. Run ON THE HOST as root:
#   scp scripts/setup-smart-selftests.sh <host>:/tmp/ && ssh -t <host> 'sudo sh /tmp/setup-smart-selftests.sh'
#
# Why this exists: SMART *attributes* did not catch the bad sector on drive9 (sdi). Attribute 198
# Offline_Uncorrectable read 0 because automatic offline collection had never been enabled, and the
# drive had had three self-tests in 6.6 years, the last one 22,000 hours before the rot was found.
# Nothing re-reads archived media, so a full surface scan is the only thing that finds cold-data rot.
#
# Schedule: one extended (full-surface) test per disk per month, one disk per night at 01:00 on days
# 1-13, so no two disks ever test at once. Short test weekly, Sundays 05:00 (~2 min, all disks).
# Scrutiny's collector reads the self-test log every 6h, so results surface on the dashboard.
#
# Idempotent: backs up the existing config and rewrites it. Safe to re-run.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
command -v smartctl >/dev/null || { echo "smartmontools not installed" >&2; exit 1; }

CONF=/etc/smartd.conf
[ -f "$CONF" ] && cp -a "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"

# -a            all the standard checks (health, errors, self-test log, 197 pending, 198 uncorrectable)
# -o on         enable automatic offline data collection -- this is what populates attribute 198,
#               and its absence is why drive9's rot was invisible
# -S on         enable attribute autosave across power cycles
# -d sat        the device type Scrutiny's collector already proved works on these drives
# -M exec ...   Debian's alert runner (/etc/smartmontools/run.d/); see 20syslog installed below
#
# No -n standby: a sleeping disk would have its scheduled test skipped for the whole month.
# Spinning one up once a month at 01:00 is the cheaper trade.
cat > "$CONF" <<'CONF_EOF'
# Managed by scripts/setup-smart-selftests.sh -- edit there, not here.
#
# -s (S/../../7/05|L/../DD/./01)
#      S/../../7/05  short test, Sundays 05:00
#      L/../DD/./01  extended test, day DD of each month, 01:00
DEFAULT -a -o on -S on -d sat -m root -M exec /usr/share/smartmontools/smartd-runner

# dev  mount          size   long test
/dev/disk/by-id/ata-WDC_WD101EFBX-68B0AN0_VHGWTW8M          -s (S/../../7/05|L/../01/./01)  # sda  drive11  10TB  1st
/dev/disk/by-id/ata-WDC_WD101EFBX-68B0AN0_VHGWMWVM          -s (S/../../7/05|L/../02/./01)  # sdb  drive2   10TB  2nd
/dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S758NX0W405322R -s (S/../../7/05|L/../03/./01)  # sdc  drive1    4TB  3rd
/dev/disk/by-id/ata-KINGSTON_SA400S37960G_50026B7785466320  -s (S/../../7/05|L/../04/./01)  # sdd  md0 root  1TB  4th
/dev/disk/by-id/ata-KINGSTON_SA400S37960G_50026B7785466265  -s (S/../../7/05|L/../05/./01)  # sde  md0 root  1TB  5th
/dev/disk/by-id/ata-WDC_WD60EFZX-68B3FN0_WD-C82D2EMK        -s (S/../../7/05|L/../06/./01)  # sdf  drive4    6TB  6th
/dev/disk/by-id/ata-WDC_WD60EFZX-68B3FN0_WD-C82E6JWK        -s (S/../../7/05|L/../07/./01)  # sdg  drive5    6TB  7th
/dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S6BCNF0W306879Z -s (S/../../7/05|L/../08/./01)  # sdh  drive3    4TB  8th
/dev/disk/by-id/ata-WDC_WD40EFRX-68WT0N0_WD-WCC4E3LTHNCN    -s (S/../../7/05|L/../09/./01)  # sdi  drive9    4TB  9th  <-- known bad sector
/dev/disk/by-id/ata-WDC_WD60EFZX-68B3FN0_WD-C82EMUYK        -s (S/../../7/05|L/../10/./01)  # sdj  drive7    6TB  10th
/dev/disk/by-id/ata-WDC_WD100EFGX-68CPLN0_WD-BC0MU9BJ       -s (S/../../7/05|L/../11/./01)  # sdk  drive6   10TB  11th
/dev/disk/by-id/ata-Samsung_SSD_870_EVO_4TB_S6BCNF0W306853Z -s (S/../../7/05|L/../12/./01)  # sdl  drive10   4TB  12th
/dev/disk/by-id/ata-WDC_WD100EFGX-68CPLN0_WD-BC0N6XNJ       -s (S/../../7/05|L/../13/./01)  # sdm  drive8   10TB  13th
CONF_EOF

# There is no MTA on this host, so smartd's default 10mail alert silently does nothing.
# Log to syslog AND push to Signal, so a drive alert reaches a phone rather than dying in a
# logfile nobody reads -- which is exactly how drive9 rotted unnoticed.
mkdir -p /etc/smartmontools/run.d
cat > /etc/smartmontools/run.d/20syslog <<'ALERT_EOF'
#!/bin/sh
# Installed by scripts/setup-smart-selftests.sh. smartd sets SMARTD_DEVICE / SMARTD_MESSAGE and
# passes the alert body on stdin. Read the syslog copy with: journalctl -t smartd-alert
logger -t smartd-alert -p daemon.crit "${SMARTD_DEVICE:-unknown}: ${SMARTD_MESSAGE:-SMART alert}"

# Push to Signal via the signal-cli-rest-api container, published on 127.0.0.1:8090.
# smartd runs on the host (outside Docker), so it uses the published port, not Docker DNS.
# Credentials live in the compose .env; this script runs as root so it can read them.
ENVFILE=/home/rowan/homeserver/.env
[ -r "$ENVFILE" ] || exit 0
NUM=$(sed -n 's/^SIGNAL_NUMBER=//p' "$ENVFILE" | head -1)
GID=$(sed -n 's/^SIGNAL_GROUP_ID=//p' "$ENVFILE" | head -1)
[ -n "$NUM" ] && [ -n "$GID" ] || exit 0

MSG="monster/smartd: ${SMARTD_DEVICE:-unknown} - ${SMARTD_MESSAGE:-SMART alert}"
python3 -c 'import json,sys; print(json.dumps({"message":sys.argv[1],"number":sys.argv[2],"recipients":[sys.argv[3]]}))' \
    "$MSG" "$NUM" "$GID" \
  | curl -s --max-time 60 -X POST -H "Content-Type: application/json" -d @- \
      http://127.0.0.1:8090/v2/send >/dev/null 2>&1

# Never fail: a non-zero exit here would make smartd log the alert handler as broken.
exit 0
ALERT_EOF
chmod +x /etc/smartmontools/run.d/20syslog

echo "--- validating config (one check cycle, no changes) ---"
if smartd -q onecheck -c "$CONF"; then
    echo "config OK"
else
    echo "CONFIG INVALID - restoring backup" >&2
    cp -a "$(ls -t $CONF.bak.* | head -1)" "$CONF"
    exit 1
fi

systemctl restart smartmontools
systemctl is-active smartmontools

echo
echo "--- scheduled tests smartd now knows about ---"
journalctl -u smartmontools --since '1 min ago' --no-pager | grep -iE "next .*test|Device: " | tail -30
