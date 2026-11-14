#!/usr/bin/env bash
# Session container entrypoint. Started by the hub as:
#   docker run ... claude-hub-session PROJECT
# with /data/projects/PROJECT mounted at /home/dev/workspace/PROJECT and the
# shared Claude login directory mounted at $CLAUDE_CONFIG_DIR.
set -euo pipefail

PROJECT="${1:?usage: entrypoint.sh PROJECT}"
TARGET="/home/dev/workspace/${PROJECT}"

if [[ ! -d "$TARGET" ]]; then
  echo "ERROR: $TARGET is not mounted" >&2
  exit 1
fi

# Git over HTTPS to github.com with the token from the environment. The helper
# is scoped to github.com so the token is never offered to any other host, and
# nothing is written to disk.
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  git config --global 'credential.https://github.com.helper' \
    '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
  export GH_TOKEN="$GITHUB_TOKEN"
fi
# Set by the image when it installed its own JDK (see session/Dockerfile) —
# absent when a project's devcontainer base brings its own JAVA_HOME instead.
[[ -f /etc/profile.d/claude-java-home.sh ]] && source /etc/profile.d/claude-java-home.sh

[[ -n "${GIT_USER_NAME:-}" ]] && git config --global user.name "$GIT_USER_NAME"
[[ -n "${GIT_USER_EMAIL:-}" ]] && git config --global user.email "$GIT_USER_EMAIL"
git config --global --add safe.directory "$TARGET"

# Best-effort fast-forward; a dirty tree or a repo with no upstream is fine.
git -C "$TARGET" pull --ff-only 2>&1 || echo "entrypoint: skipped pull"

# A project's own devcontainer.json postCreateCommand (e.g. starting a bundled
# database), passed in by the hub. Runs on every start, not just creation —
# fine as long as it's idempotent, same as the devcontainer CLI would do for
# postStartCommand.
if [[ -n "${DEVCONTAINER_POST_CREATE_COMMAND:-}" ]]; then
  echo "entrypoint: running devcontainer postCreateCommand"
  (cd "$TARGET" && eval "$DEVCONTAINER_POST_CREATE_COMMAND") || echo "entrypoint: postCreateCommand failed"
fi

echo "entrypoint: logged-in check"
claude auth status --text || echo "entrypoint: NOT logged in — log in from the hub UI"

# claude runs inside tmux so the hub can show the screen and send keystrokes
# (answering the odd prompt from a phone). The container lives exactly as long
# as the tmux session; if claude exits the container exits and Docker restarts it.
export PROJECT TARGET RESUME_SESSION_ID="${RESUME_SESSION_ID:-}"
tmux new-session -d -s claude -x 160 -y 50 /usr/local/bin/run-claude.sh

while tmux has-session -t claude 2>/dev/null; do
  sleep 5
done
echo "entrypoint: claude exited; restarting container in 10s"
sleep 10
exit 1
