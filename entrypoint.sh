#!/usr/bin/env bash
set -euo pipefail

# REPO can be:  "martian"  →  github.com/oliyh/martian
#               "oliyh/martian"  →  same
#               "othername/repo"  →  github.com/othername/repo
#               full https URL  →  used as-is
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
  CLONE_URL="${CLONE_URL/https:\/\//https:\/\/${GITHUB_TOKEN}@}"
fi

TARGET="/home/dev/workspace/${REPO_NAME}"

# Fix volume ownership before any user-level operations
mkdir -p /home/dev/workspace
chown -R dev:dev /home/dev

# Seed Claude config on first run to skip onboarding wizard
if [[ ! -f /home/dev/.claude.json ]]; then
  printf '{"hasCompletedOnboarding":true,"numStartups":1,"projects":{"/home/dev/workspace":{"allowedTools":[],"mcpContextUris":[],"mcpServers":{},"enabledMcpjsonServers":[],"disabledMcpjsonServers":[],"hasTrustDialogAccepted":true,"projectOnboardingSeenCount":0,"hasClaudeMdExternalIncludesApproved":false,"hasClaudeMdExternalIncludesWarningShown":false}}}\n' \
    > /home/dev/.claude.json
  chown dev:dev /home/dev/.claude.json
fi
if [[ ! -f /home/dev/.claude/settings.json ]]; then
  mkdir -p /home/dev/.claude
  printf '{"permissions":{"defaultMode":"acceptEdits"},"remoteControlAtStartup":true}\n' \
    > /home/dev/.claude/settings.json
  chown -R dev:dev /home/dev/.claude
fi

# Write OAuth credentials from env var if provided (avoids interactive /login)
if [[ -n "${CLAUDE_CREDENTIALS:-}" ]]; then
  mkdir -p /home/dev/.claude
  printf '%s' "$CLAUDE_CREDENTIALS" | sed 's/\\"/"/g' > /home/dev/.claude/.credentials.json
  chown -R dev:dev /home/dev/.claude
fi

# Configure git identity if provided
if [[ -n "${GIT_USER_NAME:-}" ]]; then
  gosu dev git config --global user.name "$GIT_USER_NAME"
fi
if [[ -n "${GIT_USER_EMAIL:-}" ]]; then
  gosu dev git config --global user.email "$GIT_USER_EMAIL"
fi

# Clone or update the repo as the dev user
if [[ -d "$TARGET/.git" ]]; then
  echo "Repo already cloned at $TARGET, pulling latest..."
  gosu dev git -C "$TARGET" pull
else
  echo "Cloning $CLONE_URL into $TARGET..."
  gosu dev git clone "$CLONE_URL" "$TARGET"
fi


SESSION_NAME="${SESSION_NAME:-${REPO_NAME}}"
echo "Starting Claude Code remote-control session: ${SESSION_NAME}"

# Use `script` to create a pseudo-TTY so claude's isatty() check passes
# even when Coolify starts the container detached with no real terminal.
WRAPPER=$(mktemp /tmp/claude-XXXXXX.sh)
cat > "$WRAPPER" <<ENDSCRIPT
#!/bin/bash
exec gosu dev claude --add-dir "$TARGET" --remote-control "$SESSION_NAME"
ENDSCRIPT
chmod +x "$WRAPPER"
exec script -q -e -c "$WRAPPER" /dev/null
