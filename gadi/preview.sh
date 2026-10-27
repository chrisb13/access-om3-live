#!/bin/bash
# Preview the whole site on Gadi, OM3 Monitor included. Read the Docs previews of a pull request leave
# the monitor pages empty, because their results only exist on Gadi; this builds this checkout's pages
# from a copy of the nightly job's results and run status ($WORK/results, $WORK/status), so a pull
# request's changes can be seen with real runs. Nothing in $WORK is changed.
#
# It can also run notebooks that the nightly job doesn't run yet (e.g. one a pull request adds to
# experiments.yml), from any access-om3-paper-1 checkout, against each experiment's datastore, chosen as the
# nightly job chooses it. They run here, one at a time, so keep to quick notebooks.
#
# Usage, from an access-om3-live checkout on a Gadi login node or ARE session:
#   bash gadi/preview.sh [-w WORK] [-p PAPER] [-n NOTEBOOK]... [-e EXPERIMENT]...
#     -w  the nightly job's work folder (default /g/data/tm70/cyb561/access-om3-live)
#     -p  the access-om3-paper-1 checkout to run -n notebooks from (default WORK/access-om3-paper-1)
#     -n  a notebook to run first, e.g. -n Truncation_locations (repeatable)
#     -e  run -n notebooks only for this experiment (repeatable; default: all)
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
source "$WORK/config.sh"   # EXPERIMENTS, NAMES, CONDA_MODULE (written by the nightly workflow)

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
# experiment (mkfigs takes the experiment's name from it) and the figures written to content/<name>/mkmd.
# The datastore is chosen as gadi/mkfigs_live.sh chooses it: a .json given in experiments.yml as it is;
# else payu's in the archive if it covers the newest output folder and lists files in this archive;
# else one built here (minutes, sometimes much longer).
CAT=$(mktemp -d)
trap 'rm -rf "$CAT"' EXIT
for i in "${!NAMES[@]}"; do
    e=${NAMES[$i]} out=${EXPERIMENTS[$i]}
    [ ${#nbs[@]} -gt 0 ] || break
    if [ ${#only[@]} -gt 0 ] && [[ " ${only[*]} " != *" $e "* ]]; then continue; fi
    if [ ! -e "$out" ]; then echo "$e: $out can't be read here; notebooks not run" >&2; continue; fi
    mkdir -p "$CAT/$e" "content/$e/mkmd"
    newest=$(ls -d "$out"/output[0-9]* 2>/dev/null | tail -1 || true) how=built
    if [ -f "$out" ]; then
        ln -sf "$out" "$CAT/$e/datastore.json"
        for f in "${out%.json}".*; do [ "$f" = "$out" ] || ln -sf "$f" "$CAT/$e/"; done   # its catalogue (.csv)
        how=given
    fi
    for ds in "$out/experiment_datastore.json" "$out/datastore.json"; do
        csv=$(ls "${ds%.json}".csv* 2>/dev/null | head -1 || true)
        if [ -r "$ds" ] && [ -n "$csv" ] && [ -n "$newest" ] && [ "$ds" -nt "$newest" ] \
           && python3 gadi/datastore_ok.py "$ds" "$csv" "$out"; then
            ln -sf "$ds" "$CAT/$e/datastore.json"
            ln -sf "$csv" "$CAT/$e/"   # a relative catalog_file in the json is found next to it
            how=reused
            break
        fi
    done
    if [ "$how" = built ]; then
        echo "$e: building a datastore" >&2
        build-esm-datastore --builder AccessOm3Builder --expt-dir "$out" --cat-dir "$CAT/$e" --datastore-name datastore \
            || { echo "$e: couldn't build a datastore; notebooks not run" >&2; continue; }
    fi
    # Each notebook runs in a folder under PAPER/notebooks (gitignored there), as in the nightly job: mkfigs
    # looks two folders up from it for the notebook's git history, to list its co-authors
    run="$PAPER/notebooks/mkfigs_output_preview_$e"
    for nb in "${nbs[@]}"; do
        rm -f "content/$e/mkmd/$nb.md" "content/$e/mkmd/${nb}"_[0-9]*.png   # mkmd appends to an existing page
        rm -rf "$run" && mkdir -p "$run/mkmd"
        echo "$e: running $nb" >&2
        (cd "$PAPER/notebooks" && papermill --log-level WARNING -k python3 "$nb.ipynb" "$run/${nb}_rendered.ipynb" \
            -p esm_file "$CAT/$e/datastore.json" -p papermill True -p cwd "$run/" -p nbname "$nb.ipynb") \
            || { ok=0; echo "$e: $nb failed; see content/$e/${nb}_rendered.ipynb" >&2; }
        rsync -a "$run/" "content/$e/" && rm -rf "$run"
        # The site shows a notebook as failed from mkfigs_errors.log (copied from the nightly run above):
        # replace this notebook's entry in it with this run's result
        errlog="content/$e/mkmd/mkfigs_errors.log" sep=$(printf '=%.0s' {1..56})
        if [ -f "$errlog" ]; then
            awk -v nb="$nb" '{ l[NR] = $0 } END { keep = 1
                for (i = 1; i <= NR; i++) {
                    if (l[i] ~ /^=+$/ && l[i + 1] ~ /^FAILED: /) keep = (l[i + 1] != "FAILED: " nb)
                    if (keep) print l[i] } }' "$errlog" > "$errlog.new" && mv "$errlog.new" "$errlog"
        fi
        if [ "${ok:-1}" = 0 ]; then
            { printf '%s\nFAILED: %s\n%s\n' "$sep" "$nb" "$sep"
              jq -r '[.cells[].outputs[]? | select(.output_type == "error") | "\(.ename): \(.evalue)"][0]
                     // "(no error output found in rendered notebook)"' "content/$e/${nb}_rendered.ipynb"
              echo; } >> "$errlog"
        fi
        unset ok
    done
done

# A Python environment with mkdocs (not in the conda environment), made once and reused
VENV=${PREVIEW_VENV:-$HOME/.cache/access-om3-live-preview}
if ! cmp -s requirements.txt "$VENV/requirements.txt" 2>/dev/null; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q -r requirements.txt
    cp requirements.txt "$VENV/requirements.txt"
fi
PATH="$VENV/bin:$PATH" bash scripts/build_preview.sh "$ROOT/build/site"   # absolute: mkdocs takes -d relative to build/mkdocs.yml

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

