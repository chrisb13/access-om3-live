#!/bin/bash
# Login-node step: update the paper checkout, pick experiments with new output, qsub mkfigs_live.sh.
# Usage: submit.sh WORK_DIR SUMMARY_SHA FORCE   (prints key=value lines for $GITHUB_OUTPUT)
set -euo pipefail
WORK=$1 SUMMARY_SHA=$2 FORCE=$3
source "$WORK/config.sh"   # EXPERIMENTS, NOTEBOOKS, PAPER_REF, PROJECT, STORAGE, CONDA_MODULE
STATE="$WORK/state" PAPER="$WORK/access-om3-paper-1"
mkdir -p "$STATE" "$WORK/logs"

# Don't stack up runs: skip tonight if last night's job is still queued or running.
if [ -n "$(qselect -u "$(whoami)" -N mkfigs_live -s QR)" ]; then
    echo "previous mkfigs_live job still in the queue; skipping" >&2
    echo "rebuild=false"; exit 0
fi

# Compute nodes have no internet, so fetch the notebooks here.
[ -d "$PAPER/.git" ] || git clone -q https://github.com/ACCESS-Community-Hub/access-om3-paper-1.git "$PAPER"
git -C "$PAPER" fetch -q origin "$PAPER_REF"
git -C "$PAPER" checkout -q --detach FETCH_HEAD
git -C "$PAPER" submodule -q update --init --recursive

# Re-run an experiment if it has new .nc output, or the notebooks (commit or list) changed.
key="$(git -C "$PAPER" rev-parse HEAD) ${NOTEBOOKS[*]}"
: > "$WORK/run_list"
for path in "${EXPERIMENTS[@]}"; do
    e=$(basename "$path")   # ENAME = name of the archive folder
    if [ "$e" = archive ]; then echo "$path: give the folder that 'archive' links to, not the link" >&2; continue; fi
    out=$(readlink -f "$path") || true
    if [ ! -d "$out" ]; then echo "no output directory found at $path" >&2; continue; fi
    # Let the PBS job read it: add the folder's /g/data or /scratch project to the storage line.
    case $out in /g/data/*) s=gdata/$(cut -d/ -f4 <<< "$out");; /scratch/*) s=scratch/$(cut -d/ -f3 <<< "$out");; *) s=;; esac
    if [ -n "$s" ] && [[ "+$STORAGE+" != *"+$s+"* ]]; then STORAGE+="+$s"; fi
    stamp="$STATE/$e.stamp"
    if [ "$FORCE" = true ] || [ ! -e "$stamp" ] || [ "$(cat "$stamp")" != "$key" ] \
       || [ -n "$(find "$out" -name '*.nc' -newer "$stamp" -print -quit)" ]; then
        echo "$e=$out" >> "$WORK/run_list"
        echo "$key" > "$STATE/$e.pending"   # becomes $e.stamp when the job finishes
    fi
done

# Rebuild the site if anything runs, the experiment list changed, or the Evaluation Summary changed.
echo "$SUMMARY_SHA ${EXPERIMENTS[*]}" > "$STATE/published.pending"
if [ -s "$WORK/run_list" ]; then
    echo "jobid=$(qsub -P "$PROJECT" -l storage="$STORAGE" -o "$WORK/logs" -e "$WORK/logs" \
                       -v WORK="$WORK" "$WORK/gadi/mkfigs_live.sh")"
    echo "rebuild=true"
elif ! cmp -s "$STATE/published.pending" "$STATE/published"; then
    echo "rebuild=true"
else
    echo "rebuild=false"
fi
