# claude-container

On-demand Claude Code remote sessions, deployable via [Coolify](https://coolify.io).

Start a container with `REPO=martian`, it clones your repo and launches a Claude Code remote session visible in the Claude mobile app.

## Usage

Set these in Coolify when starting a service:

| Variable | Example | Notes |
|---|---|---|
| `REPO` | `martian` | Short name → `github.com/oliyh/$REPO`. Also accepts `owner/repo` or a full https URL. |
| `GITHUB_TOKEN` | `ghp_...` | Only needed for private repos |
| `SESSION_NAME` | `martian` | Name shown in the mobile app (defaults to repo name) |

The `dev-home` volume persists your OAuth token and dependency caches across sessions.

## First-time auth

1. Start the container in Coolify
2. Connect to the session from the Claude mobile app
3. Type `/login` — follow the OAuth URL that appears
4. Done. The token is saved to the volume; subsequent sessions start authenticated.

**Alternative:** run `claude setup-token` locally once and add `CLAUDE_CODE_OAUTH_TOKEN` as a Coolify secret for fully automatic auth.

## What's in the image

- Java 21 (Temurin), Clojure CLI, Leiningen
- Node.js 20 LTS
- Claude Code CLI
