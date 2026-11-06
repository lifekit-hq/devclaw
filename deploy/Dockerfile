# syntax=docker/dockerfile:1
# devclaw-mcp server image — built FROM the checked-out devclaw source.
#
# Build context is the REPO ROOT (so this Dockerfile can COPY the working tree
# and the console source). Build from the repo root; the console stage reads
# @lifekit-hq/* from GitHub Packages through a BuildKit secret (a read:packages
# token), which is mounted for that one RUN and never lands in a layer:
#   NODE_AUTH_TOKEN=<token> docker build -f deploy/Dockerfile \
#       --secret id=npm_token,env=NODE_AUTH_TOKEN \
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

# ---- console bundle (its own stage; only the built dist reaches the image) ---
# The Angular PWA in frontend/ is served by the Python server from
# devclaw/server/console_dist (angular.json's outputPath). The lockfile layer
# comes first so a source-only change reuses the installed node_modules.
FROM node:22-trixie AS console
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN --mount=type=secret,id=npm_token,required=true \
    NODE_AUTH_TOKEN="$(cat /run/secrets/npm_token)" npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# node:22-trixie (Debian 13) ships Python 3.13; the devclaw host runtime needs >=3.12.
FROM node:22-trixie

# ---- system deps -----------------------------------------------------------
# gh: GitHub CLI for the host's GitHub reads + git credential helper.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      git curl ca-certificates gnupg \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] \
https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# ---- chromium system libs (root) for the browser e2e step -------------------
# A session runs as `node` and cannot apt-install, so the shared libraries the
# Playwright Chromium links against (libXfixes, libXrandr, libgbm, ...) and the
# fonts its screenshots need come from the image. `install-deps` is Playwright's
# own list for this Debian release, the same step CI runs as `--with-deps`; the
# browser itself is fetched per project, as `node`, further down and by the
# project's own `npx playwright install chromium`. (#987)
RUN npx -y playwright@latest install-deps chromium \
 && rm -rf /var/lib/apt/lists/* /root/.npm

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

# ---- claude CLI (npm, global) ---------------------------------------------
# Every session is `claude -p` in a treehouse worktree on this container.
RUN npm install -g \
      @anthropic-ai/claude-code \
      @playwright/mcp@latest

# ---- treehouse (one leased worktree per goal) ------------------------------
# Pinned release, checksum-verified. The engine drives its v2 lease commands
# (`get --lease`, `status --json`, `return --if-lease-holder`).
ARG TREEHOUSE_VERSION=v2.3.0
ARG TREEHOUSE_SHA256_AMD64=94fd2b2c20c35aac1ddc2941317890ad82c9916f5ccecbac4a50cda783eed10f
ARG TREEHOUSE_SHA256_ARM64=408589ba72b58d5e942071ed863a83fd96566cfd1e514945daa59defde528bbb
RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) sum="$TREEHOUSE_SHA256_AMD64" ;; arm64) sum="$TREEHOUSE_SHA256_ARM64" ;; *) echo "no treehouse build for $arch" >&2; exit 1 ;; esac \
 && tarball="treehouse-${TREEHOUSE_VERSION}-linux-${arch}.tar.gz" \
 && curl -fsSL -o "/tmp/$tarball" "https://github.com/kunchenguid/treehouse/releases/download/${TREEHOUSE_VERSION}/$tarball" \
 && echo "$sum  /tmp/$tarball" | sha256sum -c - \
 && tar -xzf "/tmp/$tarball" -C /usr/local/bin treehouse \
 && rm "/tmp/$tarball" \
 && treehouse --version

# ---- no-mistakes (the session ships its branch through it, #953) ----------
# Pinned release, checksum-verified. The engine runs `no-mistakes init` and
# reads `axi status`; the session drives `axi run` / `respond` / `answer`.
# Its home (NM_HOME, set below) lives on the `devclaw-state` volume so the
# gates and run history survive a redeploy; deploy/entrypoint.sh clears the
# previous container's stale daemon pid/lock/socket at start.
ARG NO_MISTAKES_VERSION=v1.99.0
ARG NO_MISTAKES_SHA256_AMD64=e891cc9ccc37bbbdd43897690788dc7a86567a3dcadfe8dd96eb62ddc71b416d
ARG NO_MISTAKES_SHA256_ARM64=66d0699e4d4babdd4d63191b17a26a3c6306f5f6652ac26915be720644092f4e
RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) sum="$NO_MISTAKES_SHA256_AMD64" ;; arm64) sum="$NO_MISTAKES_SHA256_ARM64" ;; *) echo "no no-mistakes build for $arch" >&2; exit 1 ;; esac \
 && tarball="no-mistakes-${NO_MISTAKES_VERSION}-linux-${arch}.tar.gz" \
 && curl -fsSL -o "/tmp/$tarball" "https://github.com/kunchenguid/no-mistakes/releases/download/${NO_MISTAKES_VERSION}/$tarball" \
 && echo "$sum  /tmp/$tarball" | sha256sum -c - \
 && tar -xzf "/tmp/$tarball" -C /usr/local/bin no-mistakes \
 && rm "/tmp/$tarball" \
 && no-mistakes --version

# ---- .NET SDK (finance-sentry and lifekit-dashboard verify with `dotnet`) ---
# Pinned release, SHA-512 verified against the release metadata
# (builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json).
# finance-sentry's `.devclaw/verify` runs `dotnet restore/build/test`; its
# global.json asks for 10.0.100 with rollForward latestFeature. The runtime's
# native deps (ICU, OpenSSL, libstdc++) already ship in the base image.
ARG DOTNET_SDK_VERSION=10.0.403
ARG DOTNET_SDK_SHA512_AMD64=b7b3d8461cf98b07d2552ce9199702fa3ad010ac2671a5f8cd5136f7534e50f6855ba7bbb02b11494563f1d04156276a31cda1103b900e5eecd0d9473dc7b9c5
ARG DOTNET_SDK_SHA512_ARM64=0966aa2b70f4677b8319be9df447a78a0dcd6164836425b55a68fd714f4023feb87b4cac21b9a996b79472ad54487cd0adcd2a9dfd570e2b2a1d26423a2baa9b
RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) rid=x64; sum="$DOTNET_SDK_SHA512_AMD64" ;; arm64) rid=arm64; sum="$DOTNET_SDK_SHA512_ARM64" ;; *) echo "no .NET SDK build for $arch" >&2; exit 1 ;; esac \
 && tarball="dotnet-sdk-${DOTNET_SDK_VERSION}-linux-${rid}.tar.gz" \
 && curl -fsSL -o "/tmp/$tarball" "https://builds.dotnet.microsoft.com/dotnet/Sdk/${DOTNET_SDK_VERSION}/$tarball" \
 && echo "$sum  /tmp/$tarball" | sha512sum -c - \
 && mkdir -p /usr/share/dotnet \
 && tar -xzf "/tmp/$tarball" -C /usr/share/dotnet \
 && rm "/tmp/$tarball" \
 && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
 && DOTNET_NOLOGO=1 dotnet --version
ENV DOTNET_ROOT=/usr/share/dotnet \
    DOTNET_NOLOGO=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=1

# ---- Playwright Chromium (runtime user, BEFORE the source COPY) ------------
# Pre-fetch the ~150MB browser so the browser e2e step doesn't download it
# on first use, and so it stays cached across code-only rebuilds (it sits
# before the cache-busted source layers below). Its system libs are the root
# layer above.
USER node
RUN npx -y playwright@latest install chromium
USER root

# ---- devclaw source (COPY from the checked-out tree — no git clone) --------
# Everything above is source-independent and stays cached across code changes.
# The COPY below is the only layer a code change invalidates.
WORKDIR /app
COPY . /app

# ---- console bundle (built in the console stage, not committed) ------------
# Copied in BEFORE the pip install so hatchling's force-include (pyproject
# `tool.hatch.build.targets.wheel.artifacts`) bakes it into the wheel.
COPY --from=console /build/devclaw/server/console_dist /app/devclaw/server/console_dist

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
    TREEHOUSE_ROOT=/var/lib/devclaw/workspaces/.treehouse \
    DEVCLAW_SKILL_LIBRARY=/app/skill-library \
    NM_HOME=/var/lib/devclaw/no-mistakes

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
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["devclaw-mcp"]
