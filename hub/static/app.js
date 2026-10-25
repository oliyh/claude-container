"use strict";

// Tiny DOM helper. Everything is built with textContent/attributes (never
// innerHTML) so values from the server can't inject markup.
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const $ = (id) => document.getElementById(id);
let state = null;
const openScreens = new Set();   // projects whose screen panel is open
const dockerChoice = {};         // project -> checkbox state, survives re-render
let busy = false;                // pause polling re-render while the user is typing

function banner(msg) {
  $("banner").hidden = !msg;
  $("banner").textContent = msg || "";
}

async function act(fn) {
  try { banner(""); await fn(); } catch (e) { banner(e.message); }
  refresh(true);
}

// ------------------------------------------------------------------ login

function renderLogin() {
  const { auth, login } = state;
  const sec = $("login");
  // Don't rebuild while the user is typing a code.
  if (login.state === "waiting" && sec.querySelector("input:focus")) return;
  sec.replaceChildren(h("h2", null, "Claude login"));

  if (login.state === "waiting") {
    sec.append(
      login.url
        ? h("p", null, "1. Open this link, sign in, and copy the code it shows:")
        : h("p", { class: "muted" }, "Starting login…"),
    );
    if (login.url) {
      sec.append(h("p", null, h("a", { href: login.url, target: "_blank", rel: "noopener noreferrer" }, "Sign in to Claude")));
      const input = h("input", { type: "text", class: "grow", placeholder: "Paste code here", autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
      sec.append(
        h("p", { class: "muted" }, "2. Paste the code:"),
        h("div", { class: "row" }, input,
          h("button", { class: "primary", onclick: () => act(() => api("/api/login/code", { code: input.value.trim() })) }, "Submit")),
      );
    }
    sec.append(h("div", { class: "row" }, h("button", { onclick: () => act(() => api("/api/login/cancel", {})) }, "Cancel")));
    return;
  }

  if (auth.loggedIn) {
    const who = [auth.email, auth.subscriptionType].filter(Boolean).join(" · ");
    sec.append(h("div", { class: "row spread" },
      h("span", null, h("span", { class: "pill ok" }, "logged in"), " ", h("span", { class: "muted" }, who)),
      h("button", { class: "danger", onclick: () => confirm("Log out? Running sessions will lose access.") && act(() => api("/api/logout", {})) }, "Log out"),
    ));
  } else {
    sec.append(h("div", { class: "row spread" },
      h("span", { class: "pill bad" }, "logged out"),
      h("button", { class: "primary", onclick: () => act(() => api("/api/login/start", {})) }, "Log in"),
    ));
    if (login.state === "failed") {
      sec.append(h("p", { class: "bad" }, `Login did not complete. ${login.error || "Try again."}`));
    }
  }
}

// ------------------------------------------------------------------ projects

function sessionPill(p) {
  const s = p.session;
  if (p.job && p.job.state === "cloning") return h("span", { class: "pill warn" }, "cloning…");
  if (!s) return h("span", { class: "pill muted" }, "stopped");
  if (s.state === "running") return h("span", { class: "pill ok" }, "running");
  return h("span", { class: "pill warn" }, s.state);
}

function renderProject(p) {
  const running = p.session && p.session.state === "running";
  const dockerBox = h("input", { type: "checkbox", onchange: (e) => { dockerChoice[p.name] = e.target.checked; } });
  dockerBox.checked = dockerChoice[p.name] ?? (p.session ? p.session.docker : false);

  const el = h("div", { class: "project" },
    h("div", { class: "row spread" }, h("span", { class: "name" }, p.name), sessionPill(p)),
  );

  if (p.job && p.job.state === "error") {
    el.append(h("pre", null, p.job.message || "clone failed"),
      h("div", { class: "row" }, h("button", { onclick: () => act(() => api(`/api/projects/${p.name}/dismiss`, {})) }, "Dismiss")));
    return el;
  }
  if (!p.cloned) return el;

  el.append(h("div", { class: "row" },
    h("button", { class: "primary", onclick: () => act(() => api(`/api/projects/${p.name}/start`, { docker: dockerBox.checked })) }, p.session ? "Restart" : "Start"),
    p.session ? h("button", { class: "danger", onclick: () => act(() => api(`/api/projects/${p.name}/stop`, {})) }, "Stop") : null,
    running ? h("button", { onclick: () => { openScreens.has(p.name) ? openScreens.delete(p.name) : openScreens.add(p.name); renderProjects(); pollScreens(); } }, openScreens.has(p.name) ? "Hide screen" : "Screen") : null,
    h("label", { title: "Lets the session start arbitrary containers on the nested daemon, which is effectively root on this server" }, dockerBox, "Docker access (root-equivalent)"),
    h("button", { class: "danger", onclick: () => act(() => deleteProject(p.name)) }, "Delete"),
  ));

  if (running && openScreens.has(p.name)) el.append(screenPanel(p.name));
  return el;
}

async function deleteProject(name) {
  const { warnings } = await api(`/api/projects/${name}/unsynced`);
  const msg = warnings.length
    ? `"${name}" has work that is NOT in git and will be lost:\n\n• ${warnings.join("\n• ")}\n\nDelete it anyway?`
    : `Delete "${name}"? Everything is synced to git, so nothing will be lost.`;
  if (!confirm(msg)) return;
  openScreens.delete(name);
  await api(`/api/projects/${name}/delete`, { force: warnings.length > 0 });
}

function screenPanel(name) {
  const input = h("input", { type: "text", class: "grow", placeholder: "Type to the session…", autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
  const send = (body) => act(() => api(`/api/projects/${name}/keys`, body).then(pollScreens));
  const key = (k, label) => h("button", { onclick: () => send({ key: k }) }, label || k);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value) { send({ text: input.value }).then(() => send({ key: "Enter" })); input.value = ""; }
  });
  return h("div", null,
    h("pre", { id: `screen-${name}` }, "loading…"),
    h("div", { class: "row" }, input,
      h("button", { onclick: () => { if (input.value) { send({ text: input.value }).then(() => send({ key: "Enter" })); input.value = ""; } } }, "Send")),
    h("div", { class: "row" }, key("Enter"), key("Escape", "Esc"), key("Up", "↑"), key("Down", "↓"), key("Tab"), key("C-c", "Ctrl-C")),
  );
}

async function pollScreens() {
  for (const name of openScreens) {
    const pre = document.getElementById(`screen-${name}`);
    if (!pre) continue;
    try { pre.textContent = (await api(`/api/projects/${name}/screen`)).screen || "(empty)"; }
    catch (e) { pre.textContent = e.message; }
    pre.scrollTop = pre.scrollHeight;
  }
}

function renderProjects() {
  const sec = $("projects");
  if (document.activeElement && sec.contains(document.activeElement) && document.activeElement.tagName === "INPUT"
      && document.activeElement.type === "text") return;
  sec.replaceChildren(h("h2", null, "Projects"));
  if (!state.projects.length) sec.append(h("p", { class: "muted" }, "No projects yet — add one below."));
  for (const p of state.projects) sec.append(renderProject(p));
}

// ------------------------------------------------------------------ add project

let ghRepos = null;
async function loadRepos() {
  if (ghRepos || !state.github) return;
  ghRepos = [];
  try { ghRepos = (await api("/api/github/repos")).repos; } catch { /* picker is optional */ }
  const dl = document.getElementById("repos");
  if (dl) dl.replaceChildren(...ghRepos.map((r) => h("option", { value: r })));
}

function renderAdd() {
  const sec = $("add");
  if (sec.childElementCount) return; // static form; never rebuild or it loses input
  const repo = h("input", { type: "text", class: "grow", list: "repos", placeholder: "repo, owner/repo or github.com URL", autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
  const fresh = h("input", { type: "text", class: "grow", placeholder: "new project name", autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
  sec.append(
    h("h2", null, "Add a project"),
    h("datalist", { id: "repos" }),
    h("div", { class: "row" }, repo, h("button", { class: "primary", onclick: () => act(async () => { await api("/api/projects", { repo: repo.value }); repo.value = ""; }) }, "Clone")),
    h("div", { class: "row" }, fresh, h("button", { onclick: () => act(async () => { await api("/api/projects", { new: fresh.value }); fresh.value = ""; }) }, "Create empty")),
  );
  loadRepos();
}

// ------------------------------------------------------------------ image

function renderImage() {
  const { image } = state;
  const sec = $("image");
  sec.replaceChildren(h("h2", null, "Session image"));
  const pill = image.building ? h("span", { class: "pill warn" }, "building…")
    : image.ready ? h("span", { class: "pill ok" }, "ready")
    : h("span", { class: "pill bad" }, "not built");
  sec.append(h("div", { class: "row spread" }, pill,
    h("button", { disabled: image.building, onclick: () => act(() => api("/api/image/build", {})) }, image.ready ? "Rebuild (update Claude Code)" : "Build")));
  if (image.error) sec.append(h("p", { class: "bad" }, image.error));
  if (image.building || image.error) sec.append(h("pre", null, image.log.join("\n")));
}

// ------------------------------------------------------------------ loop

function render() {
  $("who").textContent = state.user;
  banner(state.docker_error);
  renderLogin();
  renderProjects();
  renderAdd();
  renderImage();
  pollScreens(); // render() rebuilt the panels; refill them straight away
}

async function refresh(now) {
  if (busy && !now) return;
  busy = true;
  try { state = await api("/api/state"); render(); }
  catch (e) { banner(e.message); }
  finally { busy = false; }
}

refresh(true);
setInterval(refresh, 4000);
setInterval(pollScreens, 3000);
