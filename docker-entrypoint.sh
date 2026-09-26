#!/bin/sh
# Run as the owner of the mounted /config folder, so settings saved from the dashboard stay
# editable on the host. PUID/PGID override the detected owner.
set -e
if [ "$(id -u)" != "0" ]; then
  exec cheap-flights "$@"
fi
mkdir -p /config /data
uid="${PUID:-$(stat -c %u /config)}"
gid="${PGID:-$(stat -c %g /config)}"
if [ "$uid" = "0" ]; then
  # A root-owned or freshly created /config: use the unprivileged app user instead.
  uid=10001 gid=10001
  chown "$uid:$gid" /config
fi
chown -R "$uid:$gid" /data
exec setpriv --reuid="$uid" --regid="$gid" --clear-groups cheap-flights "$@"
