#!/usr/bin/env bash
# Runs inside the tmux session started by entrypoint.sh.
cd "$TARGET" || exit 1
if [[ -n "${RESUME_SESSION_ID:-}" ]]; then
  exec claude --dangerously-skip-permissions --remote-control --resume "$RESUME_SESSION_ID"
else
  exec claude --dangerously-skip-permissions --remote-control "$PROJECT"
fi
