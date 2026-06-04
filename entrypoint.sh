#!/usr/bin/env bash
set -euo pipefail

# REPO can be:  "martian"  →  github.com/oliyh/martian
#               "oliyh/martian"  →  same
#               "othername/repo"  →  github.com/othername/repo
#               full https URL  →  used as-is
if [[ -f /run/claude-shared.env ]]; then
  set -a
  # shellcheck source=/dev/null
  source /run/claude-shared.env
  set +a
fi

if [[ -z "${REPO:-}" ]]; then
  echo "ERROR: \$REPO must be set (e.g. REPO=martian or REPO=oliyh/martian)" >&2
  exit 1
fi

case "$REPO" in
  https://*)
    CLONE_URL="$REPO"
    REPO_NAME="$(basename "$REPO" .git)"
    ;;
  */*)
    CLONE_URL="https://github.com/${REPO}.git"
    REPO_NAME="$(basename "$REPO")"
    ;;
  *)
    CLONE_URL="https://github.com/oliyh/${REPO}.git"
    REPO_NAME="$REPO"
    ;;
esac

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  printf 'https://%s:x-oauth-basic@github.com\n' "$GITHUB_TOKEN" > /home/dev/.git-credentials
  chmod 600 /home/dev/.git-credentials
  git config --global credential.helper store
fi

TARGET="/home/dev/workspace/${REPO_NAME}"

mkdir -p /home/dev/workspace

# Seed Claude config on first run to skip onboarding wizard
if [[ ! -f /home/dev/.claude.json ]]; then
  printf '{"hasCompletedOnboarding":true,"numStartups":1,"projects":{"/home/dev/workspace":{"allowedTools":[],"mcpContextUris":[],"mcpServers":{},"enabledMcpjsonServers":[],"disabledMcpjsonServers":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0,"hasClaudeMdExternalIncludesApproved":false,"hasClaudeMdExternalIncludesWarningShown":false}}}\n' \
    > /home/dev/.claude.json
fi
# Machine-managed settings: written every start so changes here reach
# existing deployments (the /home/dev volume persists across restarts).
# bypassPermissions = never prompt — safe in this isolated container and
# required so remote-control sessions can run tests/builds unattended
# without hanging on a permission request that's awkward to answer from mobile.
mkdir -p /home/dev/.claude
printf '{"skipDangerousModePermissionPrompt":true,"remoteControlAtStartup":true}\n' \
  > /home/dev/.claude/settings.json

# Seed OAuth credentials so the session starts logged in without an interactive
# /login. Two copies drift over time: the host seed (kept fresh by
# harvest-credentials.sh from whichever container refreshed most recently) and
# the in-volume copy (refreshed in place by a running Claude). On every start we
# keep whichever is fresher, measured by the access-token expiry inside the file
# rather than mtime — the harvester rewrites the seed's mtime on every copy, so
# mtime tracks harvest time, not token age. This recovers a redeployed container
# whose volume copy went stale (host seed wins) without clobbering a still-warm
# container's newer in-place refresh (volume copy wins). Falls back to the
# CLAUDE_CREDENTIALS env var for older deployments with no seed file.
CRED=/home/dev/.claude/.credentials.json
SEED=/run/claude-credentials.json
mkdir -p /home/dev/.claude

# Access-token expiry (epoch ms) from a creds file, or 0 if missing/unreadable.
cred_expiry() {
  local f=$1 e
  [[ -r "$f" ]] || { echo 0; return; }
  e=$(grep -o '"expiresAt":[0-9]*' "$f" 2>/dev/null | head -1 | grep -o '[0-9]*')
  echo "${e:-0}"
}

if [[ -r "$SEED" ]] && grep -q claudeAiOauth "$SEED" 2>/dev/null; then
  if [[ ! -f "$CRED" ]] || (( $(cred_expiry "$SEED") > $(cred_expiry "$CRED") )); then
    cp "$SEED" "$CRED"
    chmod 600 "$CRED"
    echo "entrypoint: seeded credentials from host seed (fresher than volume copy)"
  else
    echo "entrypoint: kept in-volume credentials (as fresh as or fresher than seed)"
  fi
elif [[ ! -f "$CRED" && -n "${CLAUDE_CREDENTIALS:-}" ]]; then
  printf '%s' "$CLAUDE_CREDENTIALS" > "$CRED"
  chmod 600 "$CRED"
  echo "entrypoint: seeded credentials from CLAUDE_CREDENTIALS env"
fi

# Configure git identity if provided
if [[ -n "${GIT_USER_NAME:-}" ]]; then
  git config --global user.name "$GIT_USER_NAME"
fi
if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_USER_EMAIL"
fi

git config --global --add safe.directory "$TARGET"

# Clone or update the repo
if [[ -d "$TARGET/.git" ]]; then
  echo "Repo already cloned at $TARGET, pulling latest..."
  git -C "$TARGET" pull
else
  echo "Cloning $CLONE_URL into $TARGET..."
  git clone "$CLONE_URL" "$TARGET"
fi

SESSION_NAME="${SESSION_NAME:-${REPO_NAME}}"
echo "Starting Claude Code remote-control session: ${SESSION_NAME}"

cd "$TARGET"
exec claude --dangerously-skip-permissions --remote-control "$SESSION_NAME"
