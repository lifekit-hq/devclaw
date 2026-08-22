# devclaw-mcp server image — built FROM the checked-out devclaw source.
#
# Build context is the REPO ROOT (so this Dockerfile can COPY the working tree
# and the console source). Build from the repo root:
#   docker build -f deploy/Dockerfile \
#       --build-arg DEVCLAW_GIT_SHA="$(git rev-parse HEAD)" \
#       --build-arg DEVCLAW_BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
#       -t ghcr.io/lifekit-hq/devclaw-mcp:<sha> .
#
# This replaces lifekit-stack's compose/devclaw-mcp/Dockerfile, which cloned
# devclaw from GitHub at build time against the moving `main` ref — forcing
# `--no-cache` every deploy and an md5-verify-against-GitHub crutch to catch
# the clone shipping stale code (spec 005). Building from the checked-out
# source makes the build a normal cache-respecting COPY: the image contains
# exactly this commit, verifiable via the DEVCLAW_GIT_SHA baked on /health.
#
# Auth: inherits Claude Code OAuth from the bind-mounted ~/.claude session at
# runtime (compose). No ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, ever
# (Constitution I — OAuth-only).

# node:22-trixie (Debian 13) ships Python 3.13; the devclaw host runtime needs >=3.12.
FROM node:22-trixie

# ---- system deps -----------------------------------------------------------
# docker-ce-cli: the MCP server spawns per-task devclaw-sandbox containers via
#   the host docker socket bind-mounted by compose.
# gh: GitHub CLI for the agent's tool access + git credential helper.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      git curl ca-certificates gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian trixie stable" \
      > /etc/apt/sources.list.d/docker.list \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli gh \
 && rm -rf /var/lib/apt/lists/*

# ---- chromium system libs for the done-gate E2E phase ----------------------
# Same lib list as devclaw-sandbox and openclaw-gateway so we don't drift.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libxcomposite1 libxdamage1 libatspi2.0-0 libdrm2 libxkbcommon0 \
        libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*

# ---- tailscale CLI ---------------------------------------------------------
# deploy.py shells out to `tailscale serve`. ONLY the CLI is used — tailscaled
# runs on the HOST and is reached over its control socket (bind-mounted by
# compose). The package pulls tailscaled too, but it is never started here.
RUN curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
      -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
 && curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
      -o /etc/apt/sources.list.d/tailscale.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends tailscale \
 && rm -rf /var/lib/apt/lists/*

# ---- claude CLI + ACP bridge (npm, global) --------------------------------
RUN npm install -g \
      @anthropic-ai/claude-code \
      @agentclientprotocol/claude-agent-acp \
      @playwright/mcp@latest

# ---- Playwright Chromium (runtime user, BEFORE the source COPY) ------------
# Pre-fetch the ~150MB browser so the done-gate E2E phase doesn't download it
# on first use, and so it stays cached across code-only rebuilds (it sits
# before the cache-busted source layers below).
USER node
RUN npx -y playwright@latest install chromium
USER root

# ---- devclaw source (COPY from the checked-out tree — no git clone) --------
# Everything above is source-independent and stays cached across code changes.
# The COPY below is the only layer a code change invalidates.
WORKDIR /app
COPY . /app

# ---- console bundle (built here, not committed — it's .gitignored) ---------
# The Vite+React SPA is served by the Python server from
# devclaw/server/console/dist. Build it BEFORE the pip install so hatchling's
# force-include (pyproject `tool.hatch.build.targets.wheel.artifacts`) bakes the
# freshly-built dist into the wheel.
RUN npm --prefix devclaw/server/console ci \
 && npm --prefix devclaw/server/console run build

# Install the devclaw Python package (FastMCP server + deps).
# --break-system-packages: Debian trixie is PEP 668 externally-managed; no venv
# needed — pip installs into system site-packages.
RUN pip install --break-system-packages --no-cache-dir .

# ---- runtime config --------------------------------------------------------
RUN mkdir -p /var/lib/devclaw

ENV DEVCLAW_TRANSPORT=http \
    DEVCLAW_PORT=8000 \
    DEVCLAW_DB=/var/lib/devclaw/devclaw.db \
    CLAUDE_CODE_EXECUTABLE=/usr/local/bin/claude \
    CLAUDE_CONFIG_DIR=/home/node/.claude \
    DEVCLAW_SKILL_LIBRARY=/app/skill-library

# ---- build identity (LAST layer — bust only this on each commit) -----------
# Read per request by _health_freshness() and surfaced on /health + /node.json
# (#494). Placed at the very end so changing the SHA invalidates only this one
# ~0-byte ENV layer, never the deps/source/console/pip layers above — a
# code-only rebuild that already ran stays a near-instant cache hit (SC-002).
ARG DEVCLAW_GIT_SHA=unknown
ARG DEVCLAW_BUILT_AT=unknown
ENV DEVCLAW_GIT_SHA=${DEVCLAW_GIT_SHA} \
    DEVCLAW_BUILT_AT=${DEVCLAW_BUILT_AT}

EXPOSE 8000
USER node
CMD ["devclaw-mcp"]
