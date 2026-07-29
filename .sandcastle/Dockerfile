# devclaw v2 — per-task sandbox image.
#
# One container per task. The host TaskQueue does `docker run --rm` against
# this image, bind-mounts the task's workspace_dir at /workspace and the
# host's ~/.claude read-only at /home/agent/.claude, then exec's runner.py.
# Container vaporizes on exit.
#
# Image is rebuilt whenever openhands-runner/ or this Dockerfile changes.
# Build from the repo root:
#   docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .
#
# Composition (project-devcontainer path): BASE_IMAGE lets a project's OWN dev
# image be the base so the agent runs inside the same environment a human dev
# would (the project owns a .devcontainer/Dockerfile → devclaw builds it, then
# builds THIS harness FROM it). The default BASE_IMAGE is the historical bare
# base, so an unparameterized build stays on the same base (the harness layers
# below gain an explicit python3 for the composition case — see that RUN — but
# the default image's runtime behaviour is unchanged). The harness layer
# below is therefore base-agnostic within one constraint: BASE_IMAGE must be
# DEBIAN-based (apt) — every official language SDK image (dotnet/node/go/rust)
# is. A non-debian base fails the build LOUD, never silently. The project's dev
# image declares only its TOOLCHAIN (claude-free); this layer adds devclaw's
# harness on top, so the project image stays model-agnostic and human-usable.
ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE}

ARG AGENT_UID=1000
ARG AGENT_GID=1000
# Claude CLI version pinned to match what the host runs (2.x line).
ARG CLAUDE_CLI_VERSION=2.1.139
ARG CLAUDE_AGENT_ACP_VERSION=0.37.0
# mise provisions the project-declared toolchain at task start (ADR 0005).
# Pinned so the image is reproducible.
ARG MISE_VERSION=v2026.7.11

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Node 20 (for claude CLI + claude-agent-acp), git for whatever the agent does,
# ca-certificates so https works, plus a non-root agent user matching host UID.
# python3 is installed explicitly (not assumed from the base): when BASE_IMAGE
# is a project's dev image (e.g. dotnet/sdk) the base carries no python, and
# runner.py IS python — so the harness must bring its own interpreter. On the
# default python base this is a dormant extra; runner still resolves the base's
# python3 via PATH (/usr/local/bin ahead of /usr/bin).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git gnupg python3 python3-pip \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid ${AGENT_GID} agent \
 && useradd  --uid ${AGENT_UID} --gid ${AGENT_GID} --create-home --shell /bin/bash agent

# Headless-Chromium system libraries required by the Playwright MCP server the
# agent calls when it needs to drive a real browser (UI verification, E2E).
# Same list openclaw-gateway uses — without these the bundled Chromium fails to
# launch on debian-slim with cryptic "shared libraries" errors.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
      libxcomposite1 libxdamage1 libatspi2.0-0 libdrm2 libxkbcommon0 \
      libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
 && rm -rf /var/lib/apt/lists/*

# Toolchain provisioning (ADR 0005): the image ships NO language SDKs beyond
# python+node. runner.py detects the project's declared toolchain and
# `mise install`s it at task start, cached in a per-project volume mounted at
# MISE_DATA_DIR by sandcastle.py. The apt list below is the ONE place stack
# knowledge legitimately remains — system libs mise can't install (the
# ICU/OpenSSL/krb5 set .NET runtimes need). Keep it small.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libicu-dev libssl3 libgssapi-krb5-2 \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://mise.run \
      | MISE_VERSION=${MISE_VERSION} MISE_INSTALL_PATH=/usr/local/bin/mise sh \
 && mise --version \
 # Pre-create the cache-volume mountpoint owned by agent so docker's
 # copy-on-first-mount gives fresh named volumes agent ownership (a root-owned
 # volume would fail every `mise install`).
 && mkdir -p /home/agent/.local/share/mise \
 && chown -R agent:agent /home/agent/.local

# Install python deps system-wide so the agent user can import them without
# its own venv. openhands-sdk pulls in a long tail of deps; the image is fat
# but that's fine — it's built once per sandbox version.
COPY openhands-runner/requirements.txt /tmp/requirements.txt
# `python3 -m pip` (not bare `pip`): a project base may ship no `pip` shim, and
# --break-system-packages tolerates a debian base's externally-managed marker
# (a no-op on the non-managed default python image). Installs against whichever
# python3 PATH resolves — the base's on the default image, debian's on a
# project base.
RUN python3 -m pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Claude CLI + claude-agent-acp via npm. Pinned so image is reproducible.
# @playwright/mcp is the MCP server the agent reaches Playwright through
# (configured at /workspace/.mcp.json by runner.py per task).
RUN npm install -g \
      @anthropic-ai/claude-code@${CLAUDE_CLI_VERSION} \
      @agentclientprotocol/claude-agent-acp@${CLAUDE_AGENT_ACP_VERSION} \
      @playwright/mcp@latest \
 && npm cache clean --force

# Bake the sandbox-only MCP config. runner.py copies this to
# /workspace/.mcp.json at task start so claude auto-discovers Playwright MCP
# WITHOUT mounting the host's mcpServers (which would re-open the leak
# SANDBOX_CLAUDE_ALLOWLIST exists to prevent).
COPY --chown=agent:agent openhands-runner/sandbox-mcp.json /opt/devclaw/sandbox-mcp.json

COPY --chown=agent:agent openhands-runner/runner.py /opt/devclaw/runner.py
# Skill bundle + hooks baked into the image. Skills are markdown files
# runner.py concatenates per-task-kind and prepends to the goal; hooks are
# best-effort bash scripts runner.py invokes around the agent loop.
COPY --chown=agent:agent openhands-runner/skills/ /opt/devclaw/skills/
COPY --chown=agent:agent openhands-runner/hooks/  /opt/devclaw/hooks/
RUN chmod +x /opt/devclaw/hooks/*.sh

USER agent

# Pre-fetch the Playwright Chromium build into the agent user's cache so the
# Playwright MCP server doesn't pull ~150MB on first invocation (and so first
# call works without network). Lands in /home/agent/.cache/ms-playwright.
RUN npx -y playwright@latest install chromium

WORKDIR /workspace

ENV CLAUDE_CODE_EXECUTABLE=/usr/bin/claude \
    CLAUDE_CONFIG_DIR=/home/agent/.claude \
    HOME=/home/agent \
    PATH=/usr/local/bin:/usr/bin:/bin \
    # explicit (matches the XDG default) — sandcastle.py mounts the per-project
    # toolchain cache volume here; runner.py's provisioning pre-step fills it.
    MISE_DATA_DIR=/home/agent/.local/share/mise

ENTRYPOINT ["python3", "/opt/devclaw/runner.py"]
