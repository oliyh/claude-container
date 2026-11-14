ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

# Set to 0 when BASE_IMAGE is a project's own devcontainer image: it already
# brings its own language toolchain, so the Java/Clojure/Leiningen/clj-kondo
# layers below would just be redundant (or clash with what it installed).
ARG LANG_TOOLS=1

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    unzip \
    wget \
    ca-certificates \
    gnupg \
    rlwrap \
    bsdutils \
    procps \
    build-essential \
    tmux \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Java 21 (Temurin). JAVA_HOME is dropped as a profile script rather than an
# ENV so that when LANG_TOOLS=0 we don't overwrite whatever JAVA_HOME a
# project's own devcontainer base already set via its own ENV instruction;
# entrypoint.sh sources this file if present.
RUN if [ "$LANG_TOOLS" = "1" ]; then \
      wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor -o /etc/apt/trusted.gpg.d/adoptium.gpg && \
      echo "deb https://packages.adoptium.net/artifactory/deb noble main" > /etc/apt/sources.list.d/adoptium.list && \
      apt-get update && \
      apt-get install -y temurin-21-jdk && \
      rm -rf /var/lib/apt/lists/* && \
      echo 'export JAVA_HOME=/usr/lib/jvm/temurin-21-amd64' > /etc/profile.d/claude-java-home.sh ; \
    fi

# Clojure CLI
RUN if [ "$LANG_TOOLS" = "1" ]; then \
      curl -L https://github.com/clojure/brew-install/releases/latest/download/linux-install.sh | bash ; \
    fi

# Leiningen (script only — jar downloads on first use into the project's home)
RUN if [ "$LANG_TOOLS" = "1" ]; then \
      curl -fsSL https://raw.githubusercontent.com/technomancy/leiningen/stable/bin/lein -o /usr/local/bin/lein && \
      chmod +x /usr/local/bin/lein ; \
    fi

# Docker CLI — only used when a session is started with Docker access, in which
# case DOCKER_HOST points at the hub's nested daemon (never the host's). Reads
# the distro from os-release rather than hardcoding "ubuntu", since a project's
# devcontainer base image may be Debian (Docker's repo layout differs by distro).
RUN . /etc/os-release \
    && curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# clj-kondo
RUN if [ "$LANG_TOOLS" = "1" ]; then \
      curl -sLO https://raw.githubusercontent.com/clj-kondo/clj-kondo/master/script/install-clj-kondo && \
      chmod +x install-clj-kondo && \
      ./install-clj-kondo --dir /usr/local/bin && \
      rm install-clj-kondo ; \
    fi

# Node.js 20 LTS — skipped if the base image already has one on PATH, so this
# doesn't fight a devcontainer base that brought its own.
RUN command -v node >/dev/null 2>&1 \
    || (curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
        && apt-get install -y nodejs \
        && rm -rf /var/lib/apt/lists/*)

# Playwright + Chromium for browser automation. Browsers go to a system path
# because /home/dev is a bind mount at runtime and would hide anything baked
# into it. --with-deps pulls in the OS libraries Chromium needs.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g playwright \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for running Claude (--dangerously-skip-permissions refuses root).
# UID 1000 must match the hub's user so files in the shared /data volume are
# writable from both sides. Some base images already seat a user on UID 1000
# (Ubuntu's `ubuntu`, devcontainer images' `vscode`, ...) — evict whoever that
# is so `dev` can take it.
RUN existing="$(getent passwd 1000 | cut -d: -f1 || true)"; \
    if [ -n "$existing" ] && [ "$existing" != "dev" ]; then \
      userdel -r "$existing" 2>/dev/null || userdel "$existing" || true; \
    fi; \
    id -u dev >/dev/null 2>&1 || useradd -m -u 1000 -s /bin/bash dev

# Claude Code can't self-update here (global npm prefix is root-owned); updates
# arrive by rebuilding the image instead.
ENV DISABLE_AUTOUPDATER=1
RUN echo 'set -g focus-events on' > /etc/tmux.conf

# Last on purpose: bump CACHEBUST (the hub's "Rebuild" does) to pick up a new
# Claude Code release without invalidating the heavy layers above.
ARG CACHEBUST=0
RUN echo "claude-code cachebust: ${CACHEBUST}" \
    && npm install -g @anthropic-ai/claude-code

COPY entrypoint.sh run-claude.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/run-claude.sh

USER dev
WORKDIR /home/dev
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
