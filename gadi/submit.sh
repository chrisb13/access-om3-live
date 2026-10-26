#!/bin/bash
# Login-node step: update the paper checkout, snapshot each run's status, and qsub one
# mkfigs_live.sh job per experiment with new output (MODE=collect: snapshots and job states only).
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
jobinfo() {   # exit status and resources used by finished PBS job $1, as JSON (empty fields if PBS has forgotten it)
    { qstat -xf "$1" 2>/dev/null || true; } | awk -F' = ' -v j="$1" '/Exit_status/ {x=$2} /resources_used.walltime/ {w=$2}
        /resources_used.mem =/ {m=$2} END {printf "{\"job\": \"%s\", \"exit\": \"%s\", \"walltime\": \"%s\", \"mem\": \"%s\"}", j, x, w, m}'
}
note() {   # $1: experiment, $2: what this run did with it; for the workflow log and its run summary
    echo "$1: $2" >&2
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
# A bare clone of access-om3-configs, for each status page's "Where are my friends?" (run_status.py)
CONFIGS="$WORK/access-om3-configs.git"
if [ "$MODE" != collect ]; then
    [ -d "$CONFIGS" ] || git clone -q --bare https://github.com/ACCESS-NRI/access-om3-configs.git "$CONFIGS" \
        || echo "could not clone access-om3-configs" >&2
    git --git-dir="$CONFIGS" fetch -q --prune --tags origin '+refs/heads/*:refs/heads/*' 2>/dev/null \
        || echo "could not update access-om3-configs" >&2
fi

# payu, for each run directory's `payu status` (run_status.py); skipped if it isn't available
command -v payu >/dev/null || { module use /g/data/vk83/modules && module load payu; } >/dev/null 2>&1 || true

rm -rf "$WORK/status" && mkdir -p "$WORK/status"   # one run_status.py snapshot per experiment
jobids=()
for i in "${!EXPERIMENTS[@]}"; do
    path=${EXPERIMENTS[$i]} run_dir=${RUN_DIRS[$i]:-}   # run_dir: optional payu control directory (status page only)
    nbs=${EXP_NOTEBOOKS[$i]:-${NOTEBOOKS[*]}}             # this experiment's own list, else the standard one
    key="$commit $nbs"
    e=${NAMES[$i]}   # the experiment's name (see ename in scripts/build_site.py)
    if [ "$e" = archive ]; then echo "$path: give the folder that 'archive' links to, not the link" >&2; continue; fi
    out=$(readlink -f "$path") || true
    if [ ! -d "$out" ] && [ ! -f "$out" ]; then   # a folder, or a datastore (.json) given directly
        note "$e" "no output directory found at $path"
        printf '{"decision": "no output folder found at %s"}\n' "$path" > "$WORK/status/$e.job.json"
        continue
    fi
    add_storage "$out"
    python3 "$WORK/gadi/run_status.py" "$out" "$WORK/status/$e.json" ${run_dir:+"$run_dir"} || echo "could not read run status for $e" >&2
    # One-off move: results used to be read straight from the paper checkout's mkfigs_output folders.
    old="$PAPER/notebooks/mkfigs_output_$e"
    if [ ! -d "$WORK/results/$e" ] && [ -d "$old" ]; then cp -r "$old" "$WORK/results/$e"; fi

    # This experiment's last job: still going, or ended without finishing (walltime, memory, qdel)?
    job=$(cat "$STATE/$e.jobid" 2>/dev/null || true) state="" last="{}"
    if running "$job"; then
        state="\"running\": \"$job\", " decision="its job $job is still running"
    else
        [ -z "$job" ] || last=$(jobinfo "$job")
        if [ -e "$STATE/$e.pending" ] && [ -n "$job" ]; then state="\"unfinished\": \"$job\", "; fi
        stamp="$STATE/$e.stamp" reason=""
        if [ "$MODE" = collect ]; then reason=""
        elif [ "$FORCE" = true ]; then reason="forced"
        elif [ ! -e "$stamp" ]; then reason="first run"
        elif [ "$(cat "$stamp")" != "$key" ]; then reason="notebooks or paper commit changed"
        elif [ -d "$out" ] && [ -n "$(find "$out" -name '*.nc' -newer "$stamp" -print -quit)" ]; then reason="new output"
        elif [ -f "$out" ] && [ "$out" -nt "$stamp" ]; then reason="datastore updated"
        fi
        if [ -n "$reason" ]; then
            echo "$e=$out=$nbs" > "$WORK/run_lists/$e"
            echo "$key" > "$STATE/$e.pending"   # becomes $e.stamp when its job finishes
            job=$(qsub -P "$PROJECT" -l storage="$STORAGE" -o "$WORK/logs" -e "$WORK/logs" \
                       -v "WORK=$WORK,RUN_LIST=$WORK/run_lists/$e" "$WORK/gadi/mkfigs_live.sh")   # a failed qsub stops the run
            echo "$job" > "$STATE/$e.jobid"
            state="\"running\": \"$job\", " decision="submitted job $job ($reason; storage $STORAGE)"
            jobids+=("$job")
        elif [ "$MODE" = collect ]; then decision=$(cat "$STATE/$e.decision" 2>/dev/null || echo "status refreshed")
        else decision="skipped: no new output"
        fi
    fi
    [ "$MODE" = collect ] || echo "$decision" > "$STATE/$e.decision"   # kept for the refresh after the jobs
    note "$e" "$decision"
    printf '{%s"decision": "%s", "last": %s}\n' "$state" "$decision" "$last" > "$WORK/status/$e.job.json"
done

# Rebuild the site if the experiment list, any run's status or job, any finished job's results,
# or the Evaluation Summary changed.
echo "$SUMMARY_SHA ${EXPERIMENTS[*]} $(cat "$WORK"/status/*.json 2>/dev/null | md5sum)" \
     "$(ls -l --time-style=+%s "$STATE"/*.stamp 2>/dev/null | md5sum)" > "$STATE/published.pending"
echo "jobids=${jobids[*]}"
if ! cmp -s "$STATE/published.pending" "$STATE/published"; then
    echo "rebuild=true"
else
    echo "rebuild=false"
fi
