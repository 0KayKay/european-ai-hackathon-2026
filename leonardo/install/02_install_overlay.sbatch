#!/bin/bash
# Install a runtime's pinned extra packages (serve/catalog.json "overlay_packages") into a
# separate Python overlay next to the image, without touching any other dependency. The backport
# image needs the official TileLang wheel; stock vLLM needs nothing, but still gets an (empty)
# overlay so every runtime registers the same way. Run after 01_pull_runtime.sbatch:
#   sbatch 02_install_overlay.sbatch vllm-backport-sm80-0.13.1
#SBATCH --job-name=install-overlay
#SBATCH --account=YOUR_PROJECT
#SBATCH --partition=lrd_all_serial
#SBATCH --cpus-per-task=2
#SBATCH --mem=8000M
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
set -euo pipefail
RUNTIME="${1:?usage: sbatch 02_install_overlay.sbatch <runtime id from serve/catalog.json>}"
BASE="${BASE:-${WORK:?}/team_vllm}"
CATALOG="${SLURM_SUBMIT_DIR:?}/../serve/catalog.json"
STAGING="$BASE/staging/$RUNTIME"
SIF="$STAGING/image.sif"
WHEELS="$BASE/wheels"
mkdir -p "$STAGING/python-overlay" "$BASE/cache/xdg" "$BASE/tmp/pip" "$WHEELS"
export SINGULARITYENV_PYTHONPATH="$STAGING/python-overlay"
export SINGULARITYENV_XDG_CACHE_HOME="$BASE/cache/xdg"
export SINGULARITYENV_TILELANG_CACHE_DIR="$BASE/cache/tilelang"
export SINGULARITYENV_PIP_DISABLE_PIP_VERSION_CHECK=1
export SINGULARITYENV_TMPDIR="$BASE/tmp/pip"
in_image() { singularity exec --cleanenv --no-home --contain --bind "$BASE" "$SIF" "$@" </dev/null; }
# One line per package: name version wheel sha256 modules-to-import
while read -r NAME VERSION WHEEL SHA MODULES; do
    if [[ ! -s "$WHEELS/$WHEEL" ]]; then
        in_image python3 -m pip download --no-deps --only-binary=:all: --dest "$WHEELS" "$NAME==$VERSION"
    fi
    printf '%s  %s\n' "$SHA" "$WHEELS/$WHEEL" | sha256sum -c -
    in_image python3 -m pip install --no-index --no-deps --no-compile --upgrade \
        --target "$STAGING/python-overlay" "$WHEELS/$WHEEL"
    # The image's own TileLang was incomplete (no tilelang.webgpu): import every listed module.
    in_image python3 -c 'import importlib, importlib.metadata, sys
name, version, modules = sys.argv[1:]
assert importlib.metadata.version(name) == version, importlib.metadata.version(name)
for module in modules.split(","):
    print("OVERLAY_IMPORT_OK", module, importlib.import_module(module).__file__)' "$NAME" "$VERSION" "$MODULES"
done < <(python3 -c 'import json, sys
for p in json.load(open(sys.argv[1]))["runtimes"][sys.argv[2]]["overlay_packages"]:
    print(p["name"], p["version"], p["wheel"], p["sha256"], ",".join(p.get("import_check", [p["name"]])))' "$CATALOG" "$RUNTIME")
python3 -c 'import json, os, sys
for name in json.load(open(sys.argv[1]))["runtimes"][sys.argv[2]]["overlay_required"]:
    assert os.path.getsize(os.path.join(sys.argv[3], name)) > 0, name' "$CATALOG" "$RUNTIME" "$STAGING/python-overlay"
echo OVERLAY_READY "$STAGING/python-overlay"
