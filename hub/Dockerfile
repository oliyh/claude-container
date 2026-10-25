FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git gnupg python3 python3-flask python3-waitress util-linux \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-buildx-plugin \
    && rm -rf /var/lib/apt/lists/*

# The hub drives `claude auth login/status/logout` for the shared login.
RUN npm install -g @anthropic-ai/claude-code

COPY hub/app.py /app/app.py
COPY hub/static /app/static
COPY hub/entrypoint.sh /usr/local/bin/hub-entrypoint.sh
# Build context for the session image, built on demand against the nested daemon.
COPY session /app/session
RUN chmod +x /usr/local/bin/hub-entrypoint.sh

ENV CLAUDE_CONFIG_DIR=/data/claude \
    HOME=/home/node \
    PYTHONUNBUFFERED=1

WORKDIR /app
# Starts as root only to fix ownership of the (possibly root-created) volume,
# then drops to the node user (uid 1000) for the app itself.
ENTRYPOINT ["/usr/local/bin/hub-entrypoint.sh"]
