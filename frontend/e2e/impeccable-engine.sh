#!/bin/sh
# Installs the impeccable detector engine quality.spec.ts runs (the version lifekit-common's
# design scan pins) and prints its path. The binary comes from the upstream release and runs only
# when it matches the sha256 pinned here; an existing cached copy is checked the same way.
#
#   sh e2e/impeccable-engine.sh   # CI runs it as its own step before the e2e step
set -eu

VERSION=0.1.11
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset=impeccable-linux-x64 sha=0221607e1f535af937ea267c347b1f90233b85dc2563cbd2eeefdf42e0e5c594 ;;
  Linux-aarch64) asset=impeccable-linux-arm64 sha=a3036c5c2da08ac6a14fa4886e96a8b1651beab332af75cbe328092528d6e40a ;;
  *) echo "impeccable-engine: no pinned engine for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

bin="${IMPECCABLE_HOME:-$HOME/.impeccable}/bin/$VERSION/impeccable"
verified() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$sha" ]; }

if ! verified "$bin"; then
  mkdir -p "$(dirname "$bin")"
  tmp="$bin.part.$$"
  curl -fsSL --retry 2 -o "$tmp" \
    "https://github.com/pbakaus/impeccable/releases/download/engine-v$VERSION/$asset"
  if ! verified "$tmp"; then
    rm -f "$tmp"
    echo "impeccable-engine: $asset does not match the pinned sha256" >&2
    exit 1
  fi
  chmod +x "$tmp"
  mv -f "$tmp" "$bin"
fi
echo "$bin"
