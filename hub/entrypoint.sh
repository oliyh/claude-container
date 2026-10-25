#!/usr/bin/env bash
set -euo pipefail

# /data is a named volume shared with the dind sidecar, which may have created
# it first as root. Fix ownership of the dirs we manage (not recursively —
# projects can be large), then drop privileges.
mkdir -p /data/claude /data/projects /data/home /data/hub/docker-certs /data/hub/claude-login /data/hub/overlay
chown 1000:1000 /data /data/claude /data/projects /data/home /data/hub /data/hub/docker-certs \
  /data/hub/claude-login /data/hub/overlay
chmod 700 /data/claude /data/hub/docker-certs /data/hub/claude-login
# Sessions mount the overlay read-only, so it must be traversable (not writable).
chmod 755 /data/hub /data/hub/overlay

# Client certificates for the nested Docker daemon (mTLS). dind generates them
# into a volume that only root can read; hand a copy to the app user. The same
# copy is mounted read-only into sessions that are started with Docker access.
for f in ca.pem cert.pem key.pem; do
  install -m 600 -o 1000 -g 1000 "/dind-certs/client/$f" "/data/hub/docker-certs/$f"
done

exec setpriv --reuid=1000 --regid=1000 --init-groups -- python3 /app/app.py
