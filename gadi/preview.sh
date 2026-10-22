#!/bin/bash
# Preview the whole site on Gadi, OM3 Monitor included. Read the Docs previews of a pull request leave
# the monitor pages empty, because their results only exist on Gadi; this builds this checkout's pages
# from a copy of the nightly job's results and run status ($WORK/results, $WORK/status), so a pull
# request's changes can be seen with real runs. Nothing in $WORK is changed.
#
# It can also run notebooks that the nightly job doesn't run yet (e.g. one a pull request adds to
# experiments.yml), from any access-om3-paper-1 checkout, for each experiment whose archive (or run
# directory's archive) has a datastore. They run here, one at a time, so keep to quick notebooks.
#
# Usage, from an access-om3-live checkout on a Gadi login node or ARE session:
#   bash gadi/preview.sh [-w WORK] [-p PAPER] [-n NOTEBOOK]... [-e EXPERIMENT]...
#     -w  the nightly job's work folder (default /g/data/tm70/cyb561/access-om3-live)
#     -p  the access-om3-paper-1 checkout to run -n notebooks from (default WORK/access-om3-paper-1)
#     -n  a notebook to run first, e.g. -n Truncation_locations (repeatable)
#     -e  run -n notebooks only for this experiment (repeatable; default: all that have a datastore)
# The site is written to build/site; the script ends by printing how to look at it.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
WORK=/g/data/tm70/cyb561/access-om3-live PAPER="" nbs=() only=()
while getopts "w:p:n:e:" opt; do
    case $opt in
        w) WORK=$OPTARG;; p) PAPER=$OPTARG;; n) nbs+=("$OPTARG");; e) only+=("$OPTARG");;
        *) sed -n '11,17p' "$0"; exit 1;;
    esac
done
PAPER=$(readlink -f "${PAPER:-$WORK/access-om3-paper-1}")
source "$WORK/config.sh"   # EXPERIMENTS, NAMES, RUN_DIRS, CONDA_MODULE (written by the nightly workflow)

command -v module >/dev/null || source /etc/profile
module use /g/data/xp65/public/modules
module load "$CONDA_MODULE"

# The nightly results and run status, copied as the workflow copies them (.github/workflows/live.yml)
mkdir -p content status
for e in "${NAMES[@]}"; do
    [ -d "$WORK/results/$e" ] || continue
    mkdir -p "content/$e"
    rsync -a --delete --delete-excluded --include='mkmd/***' --include='*_rendered.ipynb' --include='sypd.png' \
        --include='timings.txt' --exclude='*' "$WORK/results/$e/" "content/$e/"
done
rsync -a --delete "$WORK/status/" status/

# Extra notebooks, run as mkfigs runs them: papermill, with the datastore in a folder named after the
# experiment (mkfigs takes the experiment's name from it) and the figures written to content/<name>/mkmd
CAT=$(mktemp -d)
trap 'rm -rf "$CAT"' EXIT
for i in "${!NAMES[@]}"; do
    e=${NAMES[$i]} archive=${EXPERIMENTS[$i]} run_dir=${RUN_DIRS[$i]:-}
    [ ${#nbs[@]} -gt 0 ] || break
    if [ ${#only[@]} -gt 0 ] && [[ " ${only[*]} " != *" $e "* ]]; then continue; fi
    ds=""
    for d in "$archive" ${run_dir:+"$run_dir/archive"}; do
        for f in "$d/experiment_datastore.json" "$d/datastore.json"; do
            if [ -r "$f" ]; then ds=$f; break 2; fi
        done
    done
    if [ -z "$ds" ]; then echo "$e: no datastore found in its archive; notebooks not run" >&2; continue; fi
    mkdir -p "$CAT/$e" "content/$e/mkmd"
    ln -sf "$ds" "$CAT/$e/datastore.json"
    for f in "${ds%.json}".csv*; do [ -e "$f" ] && ln -sf "$f" "$CAT/$e/"; done   # its catalogue, beside it
    for nb in "${nbs[@]}"; do
        rm -f "content/$e/mkmd/$nb.md" "content/$e/mkmd/${nb}"_[0-9]*.png   # mkmd appends to an existing page
        echo "$e: running $nb" >&2
        (cd "$PAPER/notebooks" && papermill --log-level WARNING "$nb.ipynb" "$ROOT/content/$e/${nb}_rendered.ipynb" \
            -p esm_file "$CAT/$e/datastore.json" -p papermill True -p cwd "$ROOT/content/$e/" -p nbname "$nb.ipynb") \
            || echo "$e: $nb failed; see content/$e/${nb}_rendered.ipynb" >&2
    done
done

# A Python environment with mkdocs (not in the conda environment), made once and reused
VENV=${PREVIEW_VENV:-$HOME/.cache/access-om3-live-preview}
if ! cmp -s requirements.txt "$VENV/requirements.txt" 2>/dev/null; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r requirements.txt
    cp requirements.txt "$VENV/requirements.txt"
fi
PATH="$VENV/bin:$PATH" bash scripts/build_preview.sh build/site

port=$((8000 + RANDOM % 1000))
cat <<EOF

Preview built in $PWD/build/site. To look at it:
  on your own computer (copies about $(du -sh build/site | cut -f1)):
    rsync -a gadi.nci.org.au:$PWD/build/site/ om3-live-preview/ && python3 -m http.server -d om3-live-preview $port
    then open http://localhost:$port/
  or serve it from Gadi:
    here:                 python3 -m http.server -d $PWD/build/site $port
    on your own computer: ssh -N -L $port:$(hostname -s):$port gadi.nci.org.au   # through a login node to $(hostname -s)
    then open http://localhost:$port/
EOF

