# claude-container

A single Coolify app that gives you a web page for running Claude Code sessions, one container per project, reachable from the Claude mobile app.

```
 Google sign-in                       nested Docker daemon (dind)
┌────────────┐   ┌──────────────┐    ┌────────────────────────────┐
│ auth-proxy │──▶│     hub      │───▶│ cc-martian   cc-leadout …  │
│ (oauth2)   │   │ web UI + API │mTLS│ claude --remote-control    │
└────────────┘   └──────┬───────┘    └──────────────┬─────────────┘
                        └────── /data volume ───────┘
                   claude/  login shared by every session
                   projects/<name>  one checkout per project
                   home/<name>      that project's $HOME (caches)
```

- **Login** is done once from the page (`claude auth login` driven for you: open the link, paste the code). Every session mounts the same config directory, so they all share that login and there is nothing to copy around or refresh.
- **Sessions** are plain containers on a *nested* daemon, not on your host. Each gets only its own project directory, its own `$HOME`, and the Claude config. Claude runs in tmux inside, so the page can show its screen and send keystrokes if it ever asks something.
- **Docker access** is opt-in per session (checkbox) and is root-equivalent on the server. Without it a session cannot use the nested daemon.
- The host Docker socket is never mounted anywhere (see *Security notes* for what that does and doesn't buy you).

## Set up on Coolify

You need a domain pointing at your Coolify server (e.g. `claude.example.com`). Four steps, about ten minutes plus the first image build.

### 1. Create a Google sign-in (once)

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials) (create a project if asked).
2. If prompted, set up the **OAuth consent screen**: user type *External*, any app name, and add your own Google account under **Test users**. Leave it in *Testing*; you don't need to publish it.
3. **Create credentials → OAuth client ID → Web application.**
   Under **Authorised redirect URIs** add `https://claude.example.com/oauth2/callback` (your domain).
4. Copy the **Client ID** and **Client secret**.

### 2. Create the app in Coolify

1. **New resource → Docker Compose** (from a Git repository), pointing at this repo, branch `main`.
2. Open **Environment Variables** and set:

   | Variable | Value |
   |---|---|
   | `ALLOWED_EMAIL` | your Google account email — the only account let in |
   | `GOOGLE_CLIENT_ID` | from step 1 |
   | `GOOGLE_CLIENT_SECRET` | from step 1 |
   | `COOKIE_SECRET` | 32 random characters — run `openssl rand -hex 16` |
   | `PUBLIC_URL` | `https://claude.example.com` (no trailing slash) |
   | `GITHUB_TOKEN` | *optional* — [fine-grained token](https://github.com/settings/personal-access-tokens/new) limited to the repos you want to use, with **Contents: read and write**. Needed for private repos and for pushing. |
   | `GITHUB_OWNER` | *optional* — your GitHub username; a bare repo name like `martian` means `GITHUB_OWNER/martian` (default `oliyh`) |

   Other optional variables are listed in [`.env.example`](.env.example).
3. Under the **`auth-proxy`** service, set the domain to `https://claude.example.com:4180` — the `:4180` tells Coolify which port to route to. Give the other two services (`hub`, `dind`) **no** domain.

### 3. Deploy

Press **Deploy**. The first start builds the session image inside the app (Java, Clojure, Node, Playwright, Claude Code), which takes several minutes; progress shows on the page. Later deploys reuse the cache.

### 4. Log in and use it

1. Open `https://claude.example.com` and sign in with Google.
2. Under **Claude login**, press **Log in**, open the link, sign in to Claude, and paste the code back. You only do this once; every session shares the login.
3. **Add a project** (repo name, `owner/repo` or a github.com URL, or *Create empty*), then **Start**.
4. On your phone, open the Claude app → **Code**. The project shows up with a green dot while it's running.

If sign-in loops or shows a redirect error, the redirect URI in Google must match `PUBLIC_URL` + `/oauth2/callback` exactly.

## Using it

| | |
|---|---|
| Add a project | Pick/type `repo`, `owner/repo` or a github.com URL → **Clone**, or **Create empty** for a fresh idea. |
| Start / Restart | Recreates the container (state lives in `/data`, so nothing is lost). |
| Screen | Live view of Claude's terminal, with a text box and Enter/Esc/arrows/Ctrl-C. |
| Rebuild image | Pulls the latest Claude Code (and base packages). Restart sessions afterwards to use it. |
| Log out / in | Re-do the login cycle any time. |

Sessions restart automatically with the daemon. Claude is launched as `claude --dangerously-skip-permissions --remote-control <project>` so unattended runs never stall on a permission prompt — the blast radius is the session container (its project dir, its home, the shared Claude config, and the GitHub token if set).

## Security notes

**Perimeter.** Only `ALLOWED_EMAIL` gets past Google sign-in. The hub additionally rejects requests that lack that email or don't come from the auth-proxy's IP, and it doesn't even listen on the interface facing the nested daemon, so session containers can't connect to it. State-changing calls need `application/json` and a same-origin `Origin`; the UI never uses `innerHTML` and sets a strict CSP.

**What a session can do.** Treat every session as untrusted: it runs with `--dangerously-skip-permissions` on whatever a repo or a prompt tells it. A session:
- can read and write its own project and home, and can read **the shared Claude login** (all sessions share it by design — a compromised session can steal your subscription login);
- can read other projects' conversation history in the shared config dir, and write `.claude.json` (including MCP server config) that other sessions will load;
- cannot change `settings.json`, `CLAUDE.md`, `commands/`, `agents/`, `skills/` or `hooks/`: those are served read-only from a hub-owned overlay;
- has all capabilities dropped, and optional `SESSION_MEMORY` / `SESSION_CPUS` limits;
- has outbound network access, including to your host and LAN: the nested daemon's network is not isolated. Add egress rules on the host if that matters.
- gets `GITHUB_TOKEN` in its environment if you set one. Use a fine-grained PAT limited to the repos you want sessions to touch.

**Docker access is root-equivalent.** `dind` is `privileged`, which exposes the host's devices to anything that can drive its daemon. A session started with the *Docker access* box ticked gets client certs for that daemon, so treat it as having root on the server. Leave it off unless a project needs to run containers. Without the box, a session can reach the daemon's port but is rejected at the TLS handshake.

**Cloning** is limited to `ALLOWED_OWNERS` (default `GITHUB_OWNER`), because a cloned repo's own hooks and MCP config run unattended in a session.

**Not done.** Image builds download unpinned installers and the latest Claude Code (the Rebuild button is meant to). Anyone who controls your claude.ai account can drive these sessions. The stronger fixes are rootless/Sysbox instead of privileged dind, a per-project Claude config dir, and a per-project GitHub token.

## Layout

- `hub/` — Flask app + static UI (`app.py`, `static/`), runs the login flow and talks to the nested daemon.
- `session/` — image each project container runs (Java 21, Clojure, Leiningen, Node 20, Playwright, gh, Claude Code).
- `docker-compose.yaml` — `auth-proxy`, `hub`, `dind`.
