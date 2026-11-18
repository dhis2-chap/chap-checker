#!/usr/bin/env bash
# Re-capture every TUI screenshot under docs/assets/ from a config (default ./chap-checker.toml):
#
#   dashboard.svg / dashboard-dhis2.svg                    the overview, phosphor and dhis2 themes
#   dashboard-instance.svg / dashboard-instance-dhis2.svg  the first instance's page
#   dashboard-check.svg / dashboard-check-dhis2.svg        the second instance's first check, in the side panel
#   tui-palette.svg / tui-palette-dhis2.svg                the command palette (Ctrl+K)
#   tui-token-modal.svg / tui-token-modal-dhis2.svg        the token prompt, against a daemon with [auth]
#
# The config's [ui] theme is overridden per flavour, in a scratch copy. Alerts never fire: the TUI
# runs without --alerts, and so does the daemon behind the token prompt.
#
#   scripts/capture_docs_tui.sh [CONFIG]
set -euo pipefail
config=${1:-chap-checker.toml}
out=docs/assets
size=160x45
# Scratch copies live in the build directory (removed by `make clean`), overwritten each run.
tmp=target/docs-capture
mkdir -p "$tmp"
trap 'kill "${daemon:-}" 2>/dev/null || true' EXIT
cargo build --quiet --bin chap-checker
bin=./target/debug/chap-checker

for flavour in phosphor dhis2; do
  suffix=""
  if [ "$flavour" = dhis2 ]; then suffix="-dhis2"; fi
  cfg="$tmp/$flavour.toml"
  # Drop any [ui] and [auth] tables, then add this flavour's theme.
  awk '/^\[/{skip=($0=="[ui]"||$0=="[auth]")} !skip' "$config" > "$cfg"
  printf '\n[ui]\ntheme = "%s"\n' "$flavour" >> "$cfg"
  chmod 600 "$cfg"
  $bin tui -c "$cfg" --frame $size --frame-out "$tmp/dashboard$suffix"
  $bin tui -c "$cfg" --frame $size --frame-out "$tmp/dashboard-instance$suffix" --frame-key enter
  cp "$tmp/dashboard$suffix.svg" "$out/dashboard$suffix.svg"
  $bin tui -c "$cfg" --frame $size --frame-out "$tmp/dashboard-check$suffix" \
    --frame-key down --frame-key enter --frame-key enter
  cp "$tmp/dashboard-instance$suffix.svg" "$out/dashboard-instance$suffix.svg"
  cp "$tmp/dashboard-check$suffix.svg" "$out/dashboard-check$suffix.svg"
  $bin tui -c "$cfg" --frame $size --frame-out "$tmp/tui-palette$suffix" --frame-key ctrl-k
  cp "$tmp/tui-palette$suffix.svg" "$out/tui-palette$suffix.svg"

  # The token prompt: a daemon that asks for a token, and a TUI that has none.
  printf '\n[auth]\ntoken = "docs-capture-token-0123456789"\n' >> "$cfg"
  $bin serve -c "$cfg" --port 8799 --no-ui >/dev/null 2>&1 &
  daemon=$!
  sleep 2
  $bin tui --connect http://127.0.0.1:8799 --frame $size --frame-out "$tmp/tui-token-modal$suffix"
  cp "$tmp/tui-token-modal$suffix.svg" "$out/tui-token-modal$suffix.svg"
  kill "$daemon"
  wait "$daemon" 2>/dev/null || true
  unset daemon
done
echo "wrote $out/{dashboard,dashboard-instance,dashboard-check,tui-palette,tui-token-modal}{,-dhis2}.svg"
