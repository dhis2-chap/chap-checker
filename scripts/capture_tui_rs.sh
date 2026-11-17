#!/usr/bin/env bash
# Capture the Rust TUI (crates/chap-checker) as SVG, TXT and PNG.
#
# Draws one frame after the first data arrives, through the hidden `tui --frame` flag, so no
# terminal is needed. Any further arguments go to `chap-checker tui` (a config, --connect URL,
# --kiosk, --frame-key enter, ...).
#
#   scripts/capture_tui_rs.sh OUT [COLSxROWS] [tui args...]
#   scripts/capture_tui_rs.sh /tmp/overview 160x45 -c chap-checker.toml
#   scripts/capture_tui_rs.sh /tmp/instance 160x45 -c chap-checker.toml --frame-key enter
#
# Needs rsvg-convert (librsvg) for the PNG; the SVG and TXT are written without it.
set -euo pipefail
out=${1:?usage: capture_tui_rs.sh OUT [COLSxROWS] [tui args...]}
shift
size=160x45
if [[ ${1:-} =~ ^[0-9]+x[0-9]+$ ]]; then
  size=$1
  shift
fi
cargo build --quiet --bin chap-checker
./target/debug/chap-checker tui --frame "$size" --frame-out "$out" "$@"
if command -v rsvg-convert >/dev/null; then
  rsvg-convert -z 0.9 "$out.svg" -o "$out.png"
fi
echo "wrote $out.svg $out.txt$(command -v rsvg-convert >/dev/null && echo " $out.png")"
