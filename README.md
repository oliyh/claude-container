# claude-container

On-demand Claude Code remote sessions, deployable via [Coolify](https://coolify.io).

Start a container with `REPO=martian`, it clones your repo and launches a Claude Code remote session visible in the Claude mobile app.

## Usage

Set these in Coolify when starting a service:

| Variable | Example | Notes |
|---|---|---|
| `REPO` | `martian` | Short name → `github.com/oliyh/$REPO`. Also accepts `owner/repo` or a full https URL. |
| `SESSION_NAME` | `martian` | Name shown in the mobile app (defaults to repo name) |

Shared credentials (`GITHUB_TOKEN`, `CLAUDE_CREDENTIALS`) are loaded from a file on the host — see setup below.

## Host setup (first time)

SSH into the Coolify host and create a shared credentials file:

```bash
cat > /data/claude-shared.env << 'EOF'
GITHUB_TOKEN=ghp_your_token_here
CLAUDE_CREDENTIALS={"claudeAiOauth":{"accessToken":"sk-ant-...","refreshToken":"...",...}}
EOF
chmod 600 /data/claude-shared.env
```

**Getting `CLAUDE_CREDENTIALS`:** on a machine where you're already logged in to Claude Code, run:

```bash
cat ~/.claude/.credentials.json
```

Paste the entire JSON blob as the value. Docker env files take values literally so no escaping is needed. The refresh token is long-lived — you only need to update this file if you explicitly log out and back in.

**Getting `GITHUB_TOKEN`:** create a Personal Access Token at github.com/settings/tokens with `repo` scope (read + write).

All containers on this host share the same file, so you only do this once per host.

## What's in the image

- Java 21 (Temurin), Clojure CLI, Leiningen
- Node.js 20 LTS
- Claude Code CLI
