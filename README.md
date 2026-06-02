# claude-container

On-demand Claude Code remote sessions, deployable via [Coolify](https://coolify.io).

Start a container with `REPO=martian`, it clones your repo and launches a Claude Code remote session visible in the Claude mobile app.

## Usage

Set these in Coolify when starting a service:

| Variable | Example | Notes |
|---|---|---|
| `REPO` | `martian` | Short name → `github.com/oliyh/$REPO`. Also accepts `owner/repo` or a full https URL. |
| `SESSION_NAME` | `martian` | Name shown in the mobile app (defaults to repo name) |

Credentials live on the host: a `GITHUB_TOKEN` in `/data/claude-shared.env`, and the Claude seed in `/data/claude-credentials.json` — see setup below.

## How credentials stay fresh

Claude Code logs in with an OAuth credential that has a short-lived access token and a refresh token. A **running** container refreshes its own copy in place (inside its `dev-home` volume), so live sessions stay logged in indefinitely.

The problem is **new** containers. Each repo gets its own container and its own empty volume, so on first start it seeds from the host file `/data/claude-credentials.json`. If that seed is a static snapshot from weeks ago, its refresh token has aged out and the new container hits `/login`.

The fix is a host-side harvester ([`harvest-credentials.sh`](harvest-credentials.sh)): on a timer it scans every container volume for the most recently refreshed `.credentials.json` and copies it over the seed. As long as *some* container has been alive recently, the seed is always fresh, so a brand-new container next week seeds from minutes-old credentials. No action needed from your phone.

> Note: Anthropic Remote Control sessions must use subscription OAuth credentials. The long-lived `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` is explicitly scoped to inference only and **cannot** establish a Remote Control session, so it isn't an option here — hence this harvest approach.

## Host setup (first time)

SSH into the Coolify host.

**1. GitHub token** — create a fine-grained PAT at github.com/settings/tokens with **Contents** set to **Read and write**, then:

```bash
cat > /data/claude-shared.env << 'EOF'
GITHUB_TOKEN=ghp_your_token_here
EOF
chmod 600 /data/claude-shared.env
```

**2. Claude seed** — on a machine where you're already logged in to Claude Code, copy `~/.claude/.credentials.json` to the host as the seed:

```bash
# on the host
install -m 600 /dev/stdin /data/claude-credentials.json   # paste the JSON, then Ctrl-D
```

The seed only needs to be a *valid, recently-refreshed* credential to bootstrap the first container; after that the harvester keeps it current automatically.

**3. Install the harvester** (from this repo, on the host):

```bash
install -m 755 harvest-credentials.sh /usr/local/bin/harvest-credentials.sh
install -m 644 systemd/claude-harvest.service /etc/systemd/system/
install -m 644 systemd/claude-harvest.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now claude-harvest.timer
```

Check it: `systemctl list-timers claude-harvest.timer` and `journalctl -u claude-harvest.service`. Run it once on demand with `systemctl start claude-harvest.service`.

All containers on this host share these files, so you only do this once per host.

## Break-glass: refreshing the seed manually

If every container has been down long enough that the seed aged out (and the harvester had nothing fresh to copy), re-seed from your phone over SSH:

```bash
# on the host, in a throwaway dir
claude            # prints a login URL — open it in your phone browser,
                  # authorize, paste the code back at the prompt
install -m 600 ~/.claude/.credentials.json /data/claude-credentials.json
```

The next container you launch seeds from this, and the harvester takes over again from there.

## What's in the image

- Java 21 (Temurin), Clojure CLI, Leiningen
- Node.js 20 LTS
- Claude Code CLI
