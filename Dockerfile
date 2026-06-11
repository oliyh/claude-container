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
    && rm -rf /var/lib/apt/lists/*

# Java 21 (Temurin)
RUN wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public | gpg --dearmor -o /etc/apt/trusted.gpg.d/adoptium.gpg \
    && echo "deb https://packages.adoptium.net/artifactory/deb noble main" > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    && apt-get install -y temurin-21-jdk \
    && rm -rf /var/lib/apt/lists/*

# Clojure CLI
RUN curl -L https://github.com/clojure/brew-install/releases/latest/download/linux-install.sh | bash

# Leiningen (script only — jar downloads on first use into the volume)
RUN curl -fsSL https://raw.githubusercontent.com/technomancy/leiningen/stable/bin/lein -o /usr/local/bin/lein \
    && chmod +x /usr/local/bin/lein

# Docker CLI — talks to the isolated leadout-dind daemon via DOCKER_HOST
# (see docker-compose.yaml); the host socket is intentionally not mounted.
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

# Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Playwright + Chromium for browser automation. Browsers are installed to a
# system path rather than the default ~/.cache, because the dev-home volume
# shadows /home/dev at runtime and would hide anything baked in there. The env
# var is set globally so the dev user (and any project-local Playwright) finds
# them. --with-deps pulls in the OS libraries Chromium needs (fonts, GTK/X libs,
# etc.) via apt, so we clean the apt lists afterwards.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN npm install -g playwright \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for running Claude (--dangerously-skip-permissions blocks root)
RUN useradd -m -s /bin/bash dev

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER dev
WORKDIR /home/dev/workspace
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD pgrep -f claude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
