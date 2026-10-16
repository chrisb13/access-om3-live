#!/bin/bash
# Login-node step: update the paper checkout, snapshot each run's status, and qsub one
# mkfigs_live.sh job per experiment with new output (unless MODE=collect).
# Usage: submit.sh WORK_DIR SUMMARY_SHA FORCE [MODE]   (prints key=value lines for $GITHUB_OUTPUT)
set -euo pipefail
WORK=$1 SUMMARY_SHA=$2 FORCE=$3 MODE=${4:-submit}
# Non-interactive SSH gets PATH=/bin:/usr/bin; load the Gadi login environment for qsub/qselect.
if ! command -v qsub >/dev/null; then set +eu; source /etc/profile >/dev/null 2>&1; set -eu; fi
command -v qsub >/dev/null || { echo "qsub not found on PATH ($PATH)" >&2; exit 1; }
source "$WORK/config.sh"   # EXPERIMENTS, NOTEBOOKS, PAPER_REF, PROJECT, STORAGE, CONDA_MODULE
STATE="$WORK/state" PAPER="$WORK/access-om3-paper-1"
mkdir -p "$STATE" "$WORK/logs" "$WORK/results" "$WORK/run_lists"

running() {   # is PBS job $1 still queued or running?
    [ -n "$1" ] && qstat -f "$1" 2>/dev/null | grep -qE 'job_state = [QRHE]'
}

# Compute nodes have no internet, so fetch the notebooks here.
[ -d "$PAPER/.git" ] || git clone -q https://github.com/ACCESS-Community-Hub/access-om3-paper-1.git "$PAPER"
git -C "$PAPER" fetch -q origin "$PAPER_REF"
git -C "$PAPER" checkout -q --detach FETCH_HEAD
git -C "$PAPER" submodule -q update --init --recursive

# The PBS job must be able to see each folder it reads: add its /g/data or /scratch project to STORAGE.
add_storage() {
    local s
    case $1 in /g/data/*) s=gdata/$(cut -d/ -f4 <<< "$1");; /scratch/*) s=scratch/$(cut -d/ -f3 <<< "$1");; *) return 0;; esac
    if [[ "+$STORAGE+" != *"+$s+"* ]]; then STORAGE+="+$s"; fi
}
add_storage "$(readlink -f "$WORK")"   # config.sh, the paper checkout and outputs live here

# Re-run an experiment if it has new .nc output, or its notebooks (commit or list) changed.
commit=$(git -C "$PAPER" rev-parse HEAD)
rm -rf "$WORK/status" && mkdir -p "$WORK/status"   # one run_status.py snapshot per experiment
jobids=()
for i in "${!EXPERIMENTS[@]}"; do
    path=${EXPERIMENTS[$i]} run_dir=${RUN_DIRS[$i]:-}   # run_dir: optional payu control directory (status page only)
    extra=${EXTRA_NOTEBOOKS[$i]:-}                        # optional notebooks for this experiment only
    key="$commit ${NOTEBOOKS[*]} $extra"
    e=$(basename "$path")   # ENAME = name of the archive folder
    if [ "$e" = archive ]; then echo "$path: give the folder that 'archive' links to, not the link" >&2; continue; fi
    out=$(readlink -f "$path") || true
    if [ ! -d "$out" ]; then echo "no output directory found at $path" >&2; continue; fi
    add_storage "$out"
    python3 "$WORK/gadi/run_status.py" "$out" "$WORK/status/$e.json" ${run_dir:+"$run_dir"} || echo "could not read run status for $e" >&2
    # One-off move: results used to be read straight from the paper checkout's mkfigs_output folders.
    old="$PAPER/notebooks/mkfigs_output_$e"
    if [ ! -d "$WORK/results/$e" ] && [ -d "$old" ]; then cp -r "$old" "$WORK/results/$e"; fi

    # This experiment's last job: still going, or ended without finishing (walltime, memory, qdel)?
    job=$(cat "$STATE/$e.jobid" 2>/dev/null || true)
    if running "$job"; then
        printf '{"running": "%s"}\n' "$job" > "$WORK/status/$e.job.json"
        continue   # leave it be; its results are published once it finishes
    elif [ -e "$STATE/$e.pending" ] && [ -n "$job" ]; then
        printf '{"unfinished": "%s"}\n' "$job" > "$WORK/status/$e.job.json"
    fi

    stamp="$STATE/$e.stamp"
    if [ "$MODE" != collect ] && { [ "$FORCE" = true ] || [ ! -e "$stamp" ] || [ "$(cat "$stamp")" != "$key" ] \
       || [ -n "$(find "$out" -name '*.nc' -newer "$stamp" -print -quit)" ]; }; then
        echo "$e=$out=$extra" > "$WORK/run_lists/$e"
        echo "$key" > "$STATE/$e.pending"   # becomes $e.stamp when its job finishes
        echo "qsub $e: -P $PROJECT -l storage=$STORAGE" >&2
        job=$(qsub -P "$PROJECT" -l storage="$STORAGE" -o "$WORK/logs" -e "$WORK/logs" \
                   -v "WORK=$WORK,RUN_LIST=$WORK/run_lists/$e" "$WORK/gadi/mkfigs_live.sh")   # a failed qsub stops the run
        echo "$job" > "$STATE/$e.jobid"
        printf '{"running": "%s"}\n' "$job" > "$WORK/status/$e.job.json"
        jobids+=("$job")
    fi
done

# Rebuild the site if the experiment list, any run's status or job, any finished job's results,
# or the Evaluation Summary changed. Jobs submitted now are published by a later run.
echo "$SUMMARY_SHA ${EXPERIMENTS[*]} $(cat "$WORK"/status/*.json 2>/dev/null | md5sum)" \
     "$(ls -l --time-style=+%s "$STATE"/*.stamp 2>/dev/null | md5sum)" > "$STATE/published.pending"
echo "jobids=${jobids[*]}"
if ! cmp -s "$STATE/published.pending" "$STATE/published"; then
    echo "rebuild=true"
else
    echo "rebuild=false"
fi
