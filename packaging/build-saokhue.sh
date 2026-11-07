#!/usr/bin/env bash
# Build the Sao Khuê desktop app: the stock pipeline plus this fork's dashboard edition.
#
# The fork's dashboard additions live in editions/saokhue/ and are composed into
# the SPA through the core's extension seams (website/docs/extension-seams.md), so
# no core frontend file carries fork code. This wrapper:
#
#   1. links editions/saokhue/node_modules to website/node_modules (bare imports),
#   2. builds the dashboard WITH the edition (KIROCREW_EDITION_DIR +
#      KIROCREW_ALLOW_EDITION=1) after typechecking it against the core sources,
#   3. runs packaging/build-desktop.sh with SKIP_FRONTEND=1 for everything else,
#   4. moves a repo-root .venv aside for the build and puts it back afterwards
#      (find-bin.js otherwise resolves the dev venv instead of the bundled launcher).
#
# Usage (every build-desktop.sh variable passes through):
#   bash packaging/build-saokhue.sh
#   WIN_DIR_ONLY=1 bash packaging/build-saokhue.sh      # Windows: only dist/win-unpacked
#   SKIP_FRONTEND=1 bash packaging/build-saokhue.sh     # reuse an edition-built website/dist
#   SKIP_NPM_CI=1 bash packaging/build-saokhue.sh       # keep website/node_modules as is
#   EDITION_TESTS=1 bash packaging/build-saokhue.sh     # also run the edition's vitest suite
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDITION="$ROOT/editions/saokhue"
WEBSITE="$ROOT/website"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=1 ;;
  *) IS_WINDOWS=0 ;;
esac

link_edition_node_modules() {
  local link="$EDITION/node_modules"
  [ -e "$link" ] && return 0
  if [ "$IS_WINDOWS" = "1" ]; then
    # A directory junction needs no admin rights, unlike a symlink.
    cmd //c "mklink /J $(cygpath -w "$link") $(cygpath -w "$WEBSITE/node_modules")" >/dev/null
  else
    ln -s "$WEBSITE/node_modules" "$link"
  fi
}

VENV_ASIDE=""
restore_venv() {
  if [ -n "$VENV_ASIDE" ] && [ -d "$VENV_ASIDE" ]; then
    mv "$VENV_ASIDE" "$ROOT/.venv"
    log "Restored .venv"
  fi
}
trap restore_venv EXIT

if [ -d "$ROOT/.venv" ]; then
  VENV_ASIDE="$ROOT/.venv.saokhue-build-aside"
  log "Moving .venv aside for the build (restored on exit)"
  mv "$ROOT/.venv" "$VENV_ASIDE"
fi

if [ "${SKIP_FRONTEND:-0}" != "1" ]; then
  log "Building the dashboard with the Sao Khuê edition…"
  (
    cd "$WEBSITE"
    if [ "${SKIP_NPM_CI:-0}" != "1" ] || [ ! -d node_modules ]; then
      npm ci --no-audit --no-fund
    fi
    link_edition_node_modules
    npx tsc -p "$EDITION/tsconfig.json"
    if [ "${EDITION_TESTS:-0}" = "1" ]; then
      npx vitest run --config "$EDITION/vitest.config.mts"
    fi
    KIROCREW_EDITION_DIR="$EDITION" KIROCREW_ALLOW_EDITION=1 npm run build
  )
else
  log "SKIP_FRONTEND=1 — reusing website/dist (build it with this script so it carries the edition)"
fi

SKIP_FRONTEND=1 bash "$ROOT/packaging/build-desktop.sh"
