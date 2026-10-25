"""Claude hub: a small web UI to manage the shared Claude login and per-project
Claude Code sessions, each running in its own container on a nested Docker daemon.

Trust model: this process is only reachable through oauth2-proxy (Google login).
It additionally refuses any request that does not come from that proxy's IP or
does not carry the allowed user's email, so containers it launches cannot talk
to it directly.
"""
import json
import os
import pty
import re
import select
import shutil
import socket
import struct
import subprocess
import fcntl
import termios
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from waitress import serve

DATA = Path("/data")
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/data/claude"))
PROJECTS = DATA / "projects"
HOMES = DATA / "home"
DOCKER_CERTS = DATA / "hub" / "docker-certs"
# Login runs against a private config dir that sessions never see; only the
# resulting credentials are copied into the shared one (see LoginFlow).
LOGIN_DIR = DATA / "hub" / "claude-login"
# Hub-owned files laid over the shared config dir, read-only, inside sessions.
OVERLAY = DATA / "hub" / "overlay"
OVERLAY_DIRS = ("commands", "agents", "skills", "hooks")
IMAGE = "claude-hub-session:latest"
SESSION_CONTEXT = "/app/session"

ALLOWED_EMAIL = os.environ.get("ALLOWED_EMAIL", "").strip().lower()
TRUSTED_PROXY_HOST = os.environ.get("TRUSTED_PROXY_HOST", "auth-proxy-front")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "hub-front")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "oliyh")
# Cloning runs a project's own hooks/MCP config under skip-permissions with the
# GitHub token to hand, so only clone from owners you trust.
ALLOWED_OWNERS = {o.strip().lower() for o in
                  (os.environ.get("ALLOWED_OWNERS") or GITHUB_OWNER).split(",") if o.strip()}
SESSION_MEMORY = os.environ.get("SESSION_MEMORY", "")
SESSION_CPUS = os.environ.get("SESSION_CPUS", "")

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
GITHUB_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?")
CODE_RE = re.compile(r"[A-Za-z0-9_\-#.~+/=%]{1,512}")
LOGIN_URL_RE = re.compile(
    r"(https://(?:claude\.com|claude\.ai|platform\.claude\.com|console\.anthropic\.com)/\S+)\s"
)
ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]"
)
KEYS = {"Enter", "Escape", "Up", "Down", "Left", "Right", "Tab", "BTab", "Space", "BSpace", "C-c", "C-d"}
LOGIN_TIMEOUT = 600

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- helpers

def run(args, timeout=60, env=None):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def docker(*args, timeout=60):
    return run(["docker", *args], timeout=timeout)


def strip_ansi(s):
    return ANSI_RE.sub("", s).replace("\r", "")


def atomic_write_json(path, data):
    tmp = path.with_name(path.name + ".hub-tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify(error=str(e)), e.status


# ---------------------------------------------------------------- access control

def proxy_ips():
    try:
        return {i[4][0] for i in socket.getaddrinfo(TRUSTED_PROXY_HOST, None)}
    except socket.gaierror:
        return set()


@app.before_request
def guard():
    if request.path == "/healthz":
        return None
    if request.remote_addr not in proxy_ips():
        abort(403)
    email = request.headers.get("X-Forwarded-Email", "").strip().lower()
    if not ALLOWED_EMAIL or email != ALLOWED_EMAIL:
        abort(403)
    if request.method not in ("GET", "HEAD"):
        # JSON content type forces a CORS preflight, so a cross-site form
        # cannot drive the API with the user's cookie.
        if request.mimetype != "application/json":
            abort(415)
        origin = request.headers.get("Origin")
        if origin:
            host = request.headers.get("X-Forwarded-Host") or request.host
            if origin.split("://", 1)[-1] != host:
                abort(403)
    return None


@app.after_request
def security_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- claude config

def ensure_claude_config():
    """Seed the shared Claude config so sessions start unattended: onboarding
    done, the workspace trusted, no bypass-permissions prompt, remote control on.
    Settings and the other config-as-code files are served to sessions from
    OVERLAY (mounted read-only) so one session can't plant hooks for the rest."""
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    OVERLAY.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OVERLAY / "settings.json",
                      {"skipDangerousModePermissionPrompt": True, "remoteControlAtStartup": True})
    (OVERLAY / "CLAUDE.md").touch()
    for d in OVERLAY_DIRS:
        (OVERLAY / d).mkdir(exist_ok=True)

    state_path = CLAUDE_DIR / ".claude.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except ValueError:
            return  # don't clobber a file we can't parse; it may be mid-write
    changed = not state.get("hasCompletedOnboarding")
    state["hasCompletedOnboarding"] = True
    # Trust is inherited by subdirectories, so one entry covers every project.
    proj = state.setdefault("projects", {}).setdefault("/home/dev/workspace", {})
    if not proj.get("hasTrustDialogAccepted"):
        proj["hasTrustDialogAccepted"] = True
        changed = True
    if changed:
        atomic_write_json(state_path, state)


# ---------------------------------------------------------------- auth status

def auth_status(force=False):
    """Read the shared credentials file directly. Deliberately does not run
    `claude` against the shared config dir, which sessions can write to."""
    try:
        oauth = json.loads((CLAUDE_DIR / ".credentials.json").read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        oauth = {}
    logged_in = bool(oauth.get("refreshToken") or oauth.get("accessToken"))
    return {
        "loggedIn": logged_in,
        "authMethod": "claude.ai" if logged_in else "none",
        "subscriptionType": oauth.get("subscriptionType") if logged_in else None,
    }


# ---------------------------------------------------------------- login flow

class LoginFlow:
    """Drives `claude auth login` in a pty: surfaces the sign-in URL, accepts the
    pasted code, and reports the outcome."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.fd = None
        self.state = "idle"  # idle | waiting | done | failed
        self.url = None
        self.output = ""

    def snapshot(self):
        with self.lock:
            # Never return the raw terminal output: it echoes the pasted code.
            error = next((ln[:200] for ln in reversed(self.output.splitlines())
                          if ln.startswith("Login failed")), None)
            return {"state": self.state, "url": self.url, "error": error}

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise ApiError("login already in progress")
            master, slave = pty.openpty()
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 4000, 0, 0))
            LOGIN_DIR.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, BROWSER="/bin/true", TERM="xterm-256color",
                       CLAUDE_CONFIG_DIR=str(LOGIN_DIR))
            # Never leak these into the login process: they change auth behaviour.
            for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"):
                env.pop(k, None)
            self.proc = subprocess.Popen(
                ["claude", "auth", "login", "--claudeai"],
                stdin=slave, stdout=slave, stderr=slave,
                env=env, start_new_session=True, close_fds=True,
            )
            os.close(slave)
            self.fd = master
            self.state, self.url, self.output = "waiting", None, ""
            threading.Thread(target=self._read, args=(self.proc, master), daemon=True).start()

    def _read(self, proc, fd):
        raw, started = "", time.time()
        while True:
            ready, _, _ = select.select([fd], [], [], 1)
            if ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                raw += data.decode("utf-8", "replace")
                text = strip_ansi(raw)
                with self.lock:
                    self.output = text
                    if not self.url:
                        m = LOGIN_URL_RE.search(text)
                        if m:
                            self.url = m.group(1)
            elif proc.poll() is not None:
                break
            if time.time() - started > LOGIN_TIMEOUT:
                self._kill(proc)
                break
        proc.wait()
        ok = self._install_credentials()
        with self.lock:
            os.close(fd)  # under the lock so submit() can't write to a closed fd
            self.fd = None
            if self.proc is proc:
                self.state = "done" if ok else "failed"

    @staticmethod
    def _install_credentials():
        """Move fresh credentials from the private login dir into the shared one."""
        src = LOGIN_DIR / ".credentials.json"
        try:
            data = json.loads(src.read_text())
        except (OSError, ValueError):
            return False
        if not (data.get("claudeAiOauth") or {}).get("accessToken"):
            return False
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(CLAUDE_DIR / ".credentials.json", data)
        src.unlink(missing_ok=True)
        ensure_claude_config()
        return True

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, 15)
        except ProcessLookupError:
            pass

    def submit(self, code):
        if not CODE_RE.fullmatch(code):
            raise ApiError("that does not look like a login code")
        with self.lock:
            if not (self.proc and self.proc.poll() is None and self.fd is not None):
                raise ApiError("no login in progress")
            os.write(self.fd, (code + "\r").encode())

    def cancel(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self._kill(self.proc)
            # Detach so the reader thread doesn't overwrite "idle" on exit.
            self.proc = None
            self.state, self.url, self.output = "idle", None, ""


login = LoginFlow()


# ---------------------------------------------------------------- session image

class ImageBuild:
    def __init__(self):
        self.lock = threading.Lock()
        self.building = False
        self.log = []
        self.error = None

    def exists(self):
        try:
            return docker("image", "inspect", IMAGE, timeout=20).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def snapshot(self):
        with self.lock:
            return {
                "ready": self.exists() if not self.building else False,
                "building": self.building,
                "error": self.error,
                "log": self.log[-60:],
            }

    def build(self):
        with self.lock:
            if self.building:
                raise ApiError("build already running")
            self.building, self.error, self.log = True, None, []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            p = subprocess.Popen(
                ["docker", "build", "--progress=plain", "-t", IMAGE,
                 "--build-arg", f"CACHEBUST={int(time.time())}", SESSION_CONTEXT],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in p.stdout:
                with self.lock:
                    self.log.append(line.rstrip()[:300])
                    del self.log[:-300]
            if p.wait() != 0:
                self.error = "build failed — see log"
        except Exception as e:  # noqa: BLE001 — surfaced in the UI
            self.error = str(e)
        finally:
            with self.lock:
                self.building = False


image = ImageBuild()


# ---------------------------------------------------------------- projects

def parse_repo(text):
    text = text.strip()
    m = GITHUB_URL_RE.fullmatch(text)
    if m:
        owner, repo = m.groups()
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        owner, repo = text.split("/")
    elif re.fullmatch(r"[\w.-]+", text):
        owner, repo = GITHUB_OWNER, text
    else:
        raise ApiError("expected a repo name, owner/repo or a github.com URL")
    if not NAME_RE.fullmatch(repo):
        raise ApiError("unsupported repository name")
    if owner.lower() not in ALLOWED_OWNERS:
        raise ApiError(f"owner '{owner}' is not allowed (set ALLOWED_OWNERS to permit it)", 403)
    return owner, repo


def valid_name(name):
    if not NAME_RE.fullmatch(name or ""):
        raise ApiError("invalid project name")
    return name


jobs = {}  # project name -> {"state": "cloning"|"error", "message": str}


def clone_job(owner, repo):
    jobs[repo] = {"state": "cloning", "message": ""}
    helper = '!f() { echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        p = run(
            ["git", "-c", f"credential.https://github.com.helper={helper}",
             "clone", "--", f"https://github.com/{owner}/{repo}.git", str(PROJECTS / repo)],
            timeout=900, env=env,
        )
        if p.returncode != 0:
            jobs[repo] = {"state": "error", "message": p.stderr.strip()[-400:]}
        else:
            jobs.pop(repo, None)
    except subprocess.TimeoutExpired:
        jobs[repo] = {"state": "error", "message": "clone timed out"}


def unsynced_work(name):
    """Human-readable list of things in a project that exist nowhere but this
    disk (so deleting it would lose them). Empty means everything is in git."""
    root = PROJECTS / name
    if not (root / ".git").exists():
        has_files = any(root.iterdir()) if root.is_dir() else False
        return ["not a git repository — its files are not backed up anywhere"] if has_files else []

    def git(path, *args):
        return run(["git", "-C", str(path), *args], timeout=30)

    warnings = []
    remotes = git(root, "remote")
    if remotes.returncode != 0:
        return [f"could not inspect the repository: {remotes.stderr.strip()[:200]}"]
    if not remotes.stdout.strip():
        warnings.append("no git remote is configured — nothing in this project is backed up")
    else:
        ahead = git(root, "log", "HEAD", "--branches", "--not", "--remotes", "--oneline")
        n = len(ahead.stdout.splitlines())
        if n:
            warnings.append(f"{n} commit{'s' if n != 1 else ''} not pushed to any remote")

    # The main checkout plus any worktrees Claude created for sessions.
    trees = [root]
    wt = git(root, "worktree", "list", "--porcelain")
    for line in wt.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
            if path != root and path.exists():
                trees.append(path)
    for tree in trees:
        status = git(tree, "status", "--porcelain")
        n = len(status.stdout.splitlines())
        if n:
            where = "" if tree == root else f" in worktree {tree.name}"
            warnings.append(f"{n} uncommitted change{'s' if n != 1 else ''}{where}")
    if git(root, "stash", "list").stdout.strip():
        warnings.append("stashed changes")
    return warnings


def container_name(project):
    return f"cc-{project}"


def list_sessions():
    """project -> {state, status, docker}; raises on daemon trouble."""
    p = docker("ps", "-a", "--filter", "label=claude-hub", "--format", "{{json .}}", timeout=20)
    if p.returncode != 0:
        raise ApiError(f"docker daemon: {p.stderr.strip()[:200]}", 503)
    out = {}
    for line in p.stdout.splitlines():
        row = json.loads(line)
        labels = dict(kv.split("=", 1) for kv in row.get("Labels", "").split(",") if "=" in kv)
        project = labels.get("claude-hub.project")
        if project and NAME_RE.fullmatch(project):
            out[project] = {
                "state": row.get("State"),
                "status": row.get("Status"),
                "docker": labels.get("claude-hub.docker") == "1",
            }
    return out


def start_session(name, docker_access):
    valid_name(name)
    if not (PROJECTS / name / ".git").exists():
        raise ApiError("project is not cloned")
    if not auth_status().get("loggedIn"):
        raise ApiError("log in to Claude first")
    if not image.snapshot()["ready"]:
        raise ApiError("session image is not built yet")
    ensure_claude_config()
    # Pre-create the project's mount point inside the home mount. Otherwise
    # Docker creates it as root, and the hub could never clean the home dir up.
    (HOMES / name / "workspace" / name).mkdir(parents=True, exist_ok=True)

    cname = container_name(name)
    docker("rm", "-f", cname, timeout=60)
    args = [
        "run", "-d", "--name", cname, "--restart", "unless-stopped",
        "--label", "claude-hub=1",
        "--label", f"claude-hub.project={name}",
        "--label", f"claude-hub.docker={'1' if docker_access else '0'}",
        "--shm-size", "1g", "--pids-limit", "4096",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
        "-e", "CLAUDE_CONFIG_DIR=/claude-config",
        "-e", "GIT_USER_NAME", "-e", "GIT_USER_EMAIL",
        "-v", f"{CLAUDE_DIR}:/claude-config",
        # Config-as-code the hub controls; read-only so a session can't plant
        # hooks/instructions that every other session would then run.
        "-v", f"{OVERLAY / 'settings.json'}:/claude-config/settings.json:ro",
        "-v", f"{OVERLAY / 'CLAUDE.md'}:/claude-config/CLAUDE.md:ro",
        *[a for d in OVERLAY_DIRS for a in ("-v", f"{OVERLAY / d}:/claude-config/{d}:ro")],
        "-v", f"{PROJECTS / name}:/home/dev/workspace/{name}",
        "-v", f"{HOMES / name}:/home/dev",
    ]
    if SESSION_MEMORY:
        args += ["--memory", SESSION_MEMORY]
    if SESSION_CPUS:
        args += ["--cpus", SESSION_CPUS]
    if GITHUB_TOKEN:
        args += ["-e", "GITHUB_TOKEN"]
    if docker_access:
        # mTLS client certs for the nested daemon. Without this a session has no
        # way to drive Docker, even though the daemon port is reachable.
        args += [
            "--add-host", "host.docker.internal:host-gateway",
            "-v", f"{DOCKER_CERTS}:/docker-certs:ro",
            "-e", "DOCKER_HOST=tcp://host.docker.internal:2376",
            "-e", "DOCKER_TLS_VERIFY=1",
            "-e", "DOCKER_CERT_PATH=/docker-certs",
        ]
    args += [IMAGE, name]
    p = docker(*args, timeout=120)
    if p.returncode != 0:
        raise ApiError(p.stderr.strip()[-400:] or "docker run failed", 500)


def session_exec(name, *cmd):
    valid_name(name)
    return docker("exec", container_name(name), *cmd, timeout=20)


# ---------------------------------------------------------------- GitHub repo picker

_gh_cache = {"at": 0, "repos": []}


def github_repos():
    if not GITHUB_TOKEN:
        return []
    if time.time() - _gh_cache["at"] < 300:
        return _gh_cache["repos"]
    repos = []
    for page in (1, 2, 3):
        req = urllib.request.Request(
            "https://api.github.com/user/repos?per_page=100&sort=pushed&page=%d" % page,
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                batch = json.load(r)
        except (urllib.error.URLError, ValueError):
            break
        repos += [x["full_name"] for x in batch]
        if len(batch) < 100:
            break
    _gh_cache.update(at=time.time(), repos=repos)
    return repos


# ---------------------------------------------------------------- routes

@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    return send_from_directory("/app/static", "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory("/app/static", name)


@app.get("/api/state")
def state():
    sessions, docker_error = {}, None
    try:
        sessions = list_sessions()
    except ApiError as e:
        docker_error = str(e)
    names = {p.name for p in PROJECTS.iterdir() if p.is_dir() and NAME_RE.fullmatch(p.name)}
    names |= set(jobs) | set(sessions)
    projects = [
        {
            "name": n,
            "cloned": (PROJECTS / n / ".git").exists(),
            "session": sessions.get(n),
            "job": jobs.get(n),
        }
        for n in sorted(names)
    ]
    return jsonify(
        user=ALLOWED_EMAIL,
        auth=auth_status(),
        login=login.snapshot(),
        image=image.snapshot(),
        projects=projects,
        docker_error=docker_error,
        github=bool(GITHUB_TOKEN),
    )


@app.post("/api/login/start")
def login_start():
    login.start()
    return jsonify(ok=True)


@app.post("/api/login/code")
def login_code():
    login.submit(str((request.get_json(silent=True) or {}).get("code", "")).strip())
    return jsonify(ok=True)


@app.post("/api/login/cancel")
def login_cancel():
    login.cancel()
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    for f in (CLAUDE_DIR / ".credentials.json", LOGIN_DIR / ".credentials.json"):
        f.unlink(missing_ok=True)
    return jsonify(ok=True)


@app.post("/api/image/build")
def image_build():
    image.build()
    return jsonify(ok=True)


@app.get("/api/github/repos")
def gh_repos():
    return jsonify(repos=github_repos())


@app.post("/api/projects")
def add_project():
    body = request.get_json(silent=True) or {}
    if body.get("new"):
        name = valid_name(str(body["new"]).strip())
        if (PROJECTS / name).exists():
            raise ApiError("a project with that name already exists", 409)
        (PROJECTS / name).mkdir()
        p = run(["git", "init", "--", str(PROJECTS / name)], timeout=30)
        if p.returncode != 0:
            raise ApiError(p.stderr.strip()[-300:], 500)
        return jsonify(ok=True, name=name)
    owner, repo = parse_repo(str(body.get("repo", "")))
    if (PROJECTS / repo).exists() or jobs.get(repo, {}).get("state") == "cloning":
        raise ApiError("a project with that name already exists", 409)
    threading.Thread(target=clone_job, args=(owner, repo), daemon=True).start()
    return jsonify(ok=True, name=repo)


@app.post("/api/projects/<name>/dismiss")
def dismiss_job(name):
    if jobs.get(valid_name(name), {}).get("state") == "error":
        jobs.pop(name)
    return jsonify(ok=True)


@app.get("/api/projects/<name>/unsynced")
def project_unsynced(name):
    valid_name(name)
    if not (PROJECTS / name).is_dir():
        raise ApiError("no such project", 404)
    return jsonify(warnings=unsynced_work(name))


@app.post("/api/projects/<name>/delete")
def project_delete(name):
    valid_name(name)
    root = PROJECTS / name
    if root.is_symlink() or not root.is_dir():
        raise ApiError("no such project", 404)
    if jobs.get(name, {}).get("state") == "cloning":
        raise ApiError("still cloning", 409)
    # Re-check on the server: the client's view may be stale, and the API must
    # not depend on the UI having shown a warning.
    warnings = unsynced_work(name)
    if warnings and not (request.get_json(silent=True) or {}).get("force"):
        return jsonify(error="project has work that is not in git", warnings=warnings), 409
    docker("rm", "-f", container_name(name), timeout=60)
    jobs.pop(name, None)
    try:
        shutil.rmtree(root)
    except OSError as e:
        raise ApiError(f"could not remove the project files: {e}", 500)
    try:
        shutil.rmtree(HOMES / name)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise ApiError(f"project removed, but its home directory could not be: {e}", 500)
    return jsonify(ok=True)


@app.post("/api/projects/<name>/start")
def project_start(name):
    body = request.get_json(silent=True) or {}
    start_session(name, bool(body.get("docker")))
    return jsonify(ok=True)


@app.post("/api/projects/<name>/stop")
def project_stop(name):
    docker("rm", "-f", container_name(valid_name(name)), timeout=60)
    return jsonify(ok=True)


@app.get("/api/projects/<name>/screen")
def project_screen(name):
    p = session_exec(name, "tmux", "capture-pane", "-p", "-t", "claude", "-S", "-80")
    if p.returncode != 0:
        raise ApiError("session is not running", 409)
    return jsonify(screen=p.stdout.rstrip())


@app.post("/api/projects/<name>/keys")
def project_keys(name):
    body = request.get_json(silent=True) or {}
    text, key = body.get("text"), body.get("key")
    if key is not None:
        if key not in KEYS:
            raise ApiError("unsupported key")
        p = session_exec(name, "tmux", "send-keys", "-t", "claude", key)
    elif isinstance(text, str) and 0 < len(text) <= 2000:
        p = session_exec(name, "tmux", "send-keys", "-t", "claude", "-l", "--", text)
    else:
        raise ApiError("nothing to send")
    if p.returncode != 0:
        raise ApiError("session is not running", 409)
    return jsonify(ok=True)


# ---------------------------------------------------------------- main

def listen_addresses():
    """Listen only on the private network shared with the auth-proxy (and on
    loopback, for the healthcheck). Sessions reach the compose networks through
    dind, which is not on that network, so they cannot even connect to the hub;
    the source-IP check in guard() is the second layer. Fails closed: if the
    address cannot be resolved, exit and let the restart policy retry."""
    for _ in range(30):
        try:
            front = socket.gethostbyname(LISTEN_HOST)
            break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit(f"could not resolve {LISTEN_HOST}; refusing to listen elsewhere")
    print(f"listening on {front} ({LISTEN_HOST}) and 127.0.0.1", flush=True)
    return [front, "127.0.0.1"]


def main():
    if not ALLOWED_EMAIL:
        raise SystemExit("ALLOWED_EMAIL must be set")
    PROJECTS.mkdir(parents=True, exist_ok=True)
    HOMES.mkdir(parents=True, exist_ok=True)
    ensure_claude_config()
    if not image.exists():
        image.build()
    serve(app, listen=" ".join(f"{ip}:8080" for ip in listen_addresses()), threads=8)


if __name__ == "__main__":
    main()
