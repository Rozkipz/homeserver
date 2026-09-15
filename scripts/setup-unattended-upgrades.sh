#!/bin/sh
# Enable automatic Debian security updates. Run ON THE HOST as root:
#   scp scripts/setup-unattended-upgrades.sh <host>:/tmp/ && ssh -t <host> 'sudo sh /tmp/setup-unattended-upgrades.sh'
#
# mill already had this; monster did not, which is why it exists. Idempotent — safe to re-run,
# and safe to run on a host that already has the package.
#
# Local settings go in a 52- drop-in rather than editing the package's own 50unattended-upgrades
# conffile, so a future apt upgrade of the package never prompts about a modified conffile.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq unattended-upgrades apt-listchanges

# Turn on the periodic jobs that apt-daily{,-upgrade}.timer trigger.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF

# Security-only. No automatic reboot: this host serves media and holds the sshfs mount to mill,
# so kernel reboots stay manual. Watch for /var/run/reboot-required.
cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'CONF'
Unattended-Upgrade::Origins-Pattern {
        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-updates";
};
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "false";
CONF

systemctl enable --now apt-daily.timer apt-daily-upgrade.timer

echo "--- config check (dry run) ---"
unattended-upgrade --dry-run --debug 2>&1 | sed -n '1,15p'
echo
echo "done. next runs:"
systemctl list-timers apt-daily.timer apt-daily-upgrade.timer --no-pager
