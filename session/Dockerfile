FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV JAVA_HOME=/usr/lib/jvm/temurin-21-amd64

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

# Java 21 (Temurin)
RUN wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor -o /etc/apt/trusted.gpg.d/adoptium.gpg \
    && echo "deb https://packages.adoptium.net/artifactory/deb noble main" > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    && apt-get install -y temurin-21-jdk \
    && rm -rf /var/lib/apt/lists/*

# Clojure CLI
RUN curl -L https://github.com/clojure/brew-install/releases/latest/download/linux-install.sh | bash

# Leiningen (script only — jar downloads on first use into the project's home)
RUN curl -fsSL https://raw.githubusercontent.com/technomancy/leiningen/stable/bin/lein -o /usr/local/bin/lein \
    && chmod +x /usr/local/bin/lein

# Docker CLI — only used when a session is started with Docker access, in which
# case DOCKER_HOST points at the hub's nested daemon (never the host's).
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list \
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
RUN curl -sLO https://raw.githubusercontent.com/clj-kondo/clj-kondo/master/script/install-clj-kondo \
    && chmod +x install-clj-kondo \
    && ./install-clj-kondo --dir /usr/local/bin \
    && rm install-clj-kondo

# Node.js 20 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Playwright + Chromium for browser automation. Browsers go to a system path
# because /home/dev is a bind mount at runtime and would hide anything baked
# into it. --with-deps pulls in the OS libraries Chromium needs.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g playwright \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for running Claude (--dangerously-skip-permissions refuses root).
# UID 1000 must match the hub's user so files in the shared /data volume are
# writable from both sides; Ubuntu 24.04 ships an `ubuntu` user on 1000, drop it.
RUN userdel -r ubuntu \
    && useradd -m -u 1000 -s /bin/bash dev

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
