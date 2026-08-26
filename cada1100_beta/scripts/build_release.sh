#!/usr/bin/env bash
# =============================================================================
# build_release.sh - ICCAD 2026 final submission package builder
#
# Target platform: AlmaLinux 8 / Rocky 8 (x86_64)
#
# Usage:
#   ./build_release.sh [/path/to/oss-cad-suite] [output_dir]
#
#   arg1: path to an unpacked oss-cad-suite toolchain (optional; if omitted,
#         the script looks for tools/oss-cad-suite under the project root)
#   arg2: output directory for the final tar.gz (default: current directory)
#
# Produces:
#   <output_dir>/ICCAD2026-beta-submit.tar.gz
#     └── cada1100_beta/
#         ├── cada1100_beta          (launcher script)
#         ├── bin/cada1100_beta.bin  (PyInstaller onefile binary)
#         ├── tools/oss-cad-suite/    (bundled toolchain)
#         ├── main.py config.py config.yaml requirements.txt README.md
#         ├── agent/
#         └── eda/  (incl. pareto_seeds)
# =============================================================================
set -euo pipefail

# --- Locate project root (parent of this script's directory) ----------------
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJ_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

OSS_CAD_SUITE="${1:-$PROJ_ROOT/tools/oss-cad-suite}"
OUTPUT_DIR="${2:-$(pwd)}"

PKG_NAME="cada1100_beta"
BIN_NAME="cada1100_beta.bin"
TARBALL="ICCAD2026-beta-submit.tar.gz"

log()  { printf '[build_release] %s\n' "$*"; }
die()  { printf '[build_release][ERROR] %s\n' "$*" >&2; exit 1; }

# --- Sanity checks -----------------------------------------------------------
[ -f "$PROJ_ROOT/main.py" ]          || die "main.py not found under $PROJ_ROOT"
[ -f "$PROJ_ROOT/requirements.txt" ] || die "requirements.txt not found under $PROJ_ROOT"
[ -f "$PROJ_ROOT/cada1100_beta" ]   || die "launcher script cada1100_beta not found under $PROJ_ROOT"
[ -d "$OSS_CAD_SUITE" ]              || die "oss-cad-suite not found at: $OSS_CAD_SUITE"
[ -x "$OSS_CAD_SUITE/bin/yosys" ]    || log "WARNING: $OSS_CAD_SUITE/bin/yosys not found or not executable"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(CDPATH= cd -- "$OUTPUT_DIR" && pwd)"

# --- Step 1: build environment (Python 3.11 + deps) --------------------------
log "Setting up build environment ..."
PYTHON=""
for cand in python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
        if [ "$ver" = "3.11" ]; then PYTHON="$cand"; break; fi
        [ -z "$PYTHON" ] && PYTHON="$cand"
    fi
done
[ -n "$PYTHON" ] || die "no python3 interpreter found (install python3.11: dnf install python3.11)"
log "Using interpreter: $PYTHON ($($PYTHON --version 2>&1))"

VENV_DIR="$PROJ_ROOT/.build_venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

pip install --upgrade pip >/dev/null
pip install -r "$PROJ_ROOT/requirements.txt"
pip install pyinstaller

# --- Step 2: PyInstaller onefile build ---------------------------------------
log "Running PyInstaller ..."
cd "$PROJ_ROOT"
rm -rf build dist "$BIN_NAME.spec"

pyinstaller --onefile \
  --name "$BIN_NAME" \
  --add-data "eda/pareto_seeds:eda/pareto_seeds" \
  --hidden-import openai \
  --hidden-import anthropic \
  --hidden-import yaml \
  --copy-metadata openai \
  --copy-metadata anthropic \
  main.py

[ -f "dist/$BIN_NAME" ] || die "PyInstaller output dist/$BIN_NAME not found"

# --- Step 3: assemble package structure --------------------------------------
log "Assembling package structure ..."
STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/$PKG_NAME"

mkdir -p "$STAGE/bin" "$STAGE/tools" "$STAGE/agent" "$STAGE/eda/pareto_seeds"

# PyInstaller binary
cp "dist/$BIN_NAME" "$STAGE/bin/$BIN_NAME"
chmod 755 "$STAGE/bin/$BIN_NAME"

# Launcher (must keep LF endings and exec bit)
cp "$PROJ_ROOT/cada1100_beta" "$STAGE/cada1100_beta"
chmod 755 "$STAGE/cada1100_beta"

# Top-level runtime files
for f in main.py config.py config.yaml requirements.txt README.md; do
    cp "$PROJ_ROOT/$f" "$STAGE/$f"
done

# agent module (runtime closure only)
for f in __init__.py llm_client.py react_agent.py tool_schema.py; do
    cp "$PROJ_ROOT/agent/$f" "$STAGE/agent/$f"
done

# eda module (runtime closure only)
for f in __init__.py backend.py constants.py contracts.py netlist_graph.py \
         optimizer.py transformer.py writer.py yosys_backend.py; do
    cp "$PROJ_ROOT/eda/$f" "$STAGE/eda/$f"
done
cp "$PROJ_ROOT"/eda/pareto_seeds/*.v "$STAGE/eda/pareto_seeds/"

# Bundled toolchain
log "Copying oss-cad-suite from $OSS_CAD_SUITE (this may take a while) ..."
cp -a "$OSS_CAD_SUITE" "$STAGE/tools/oss-cad-suite"

# --- Step 4: create tarball ---------------------------------------------------
log "Creating $TARBALL ..."
cd "$STAGE_ROOT"
tar czf "$TARBALL" "$PKG_NAME/"

# --- Step 5: move to output directory -----------------------------------------
mv -f "$TARBALL" "$OUTPUT_DIR/$TARBALL"

log "Done."
log "Package: $OUTPUT_DIR/$TARBALL"
log "Verify with: tar tzf $OUTPUT_DIR/$TARBALL | head"
