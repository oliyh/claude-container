#!/usr/bin/env bash
# Harvest the freshest Claude credentials from any container volume and
# republish them as the seed file that new containers clone on first start.
#
# Why this exists: a live container continuously refreshes its own
# ~/.claude/.credentials.json inside its Docker volume, so running sessions stay
# logged in. The host-side seed (/data/claude-credentials.json) is static,
# though — so a container launched a week later would seed from a stale refresh
# token and hit /login. This script keeps the seed as fresh as the most recently
# active session. Run it on a timer (see systemd/ units).
set -euo pipefail

SEED=/data/claude-credentials.json
VOLUMES_GLOB='/var/lib/docker/volumes/*/_data/.claude/.credentials.json'

newest=""
newest_mtime=0
for f in $VOLUMES_GLOB; do
  [[ -f "$f" ]] || continue
  # Must look like real OAuth creds, not an empty or placeholder file.
  grep -q claudeAiOauth "$f" || continue
  m=$(stat -c %Y "$f")
  if (( m > newest_mtime )); then
    newest_mtime=$m
    newest=$f
  fi
done

if [[ -z "$newest" ]]; then
  echo "harvest: no candidate credentials found under $VOLUMES_GLOB" >&2
  exit 0
fi

seed_mtime=0
[[ -f "$SEED" ]] && seed_mtime=$(stat -c %Y "$SEED")

# Never replace a newer seed with an older harvest (e.g. just after a manual
# refresh). Newest mtime == most recently refreshed == freshest refresh token.
if (( newest_mtime <= seed_mtime )); then
  echo "harvest: seed already current (no candidate newer than $SEED)"
  exit 0
fi

# Atomic replace so a half-written seed is never observed by a starting container.
tmp=$(mktemp /data/.claude-credentials.XXXXXX)
cp "$newest" "$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$SEED"
echo "harvest: updated seed from $newest (mtime $newest_mtime)"
