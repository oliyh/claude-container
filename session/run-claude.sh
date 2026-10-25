#!/usr/bin/env bash
# Runs inside the tmux session started by entrypoint.sh.
cd "$TARGET" || exit 1
exec claude --dangerously-skip-permissions --remote-control "$PROJECT"
