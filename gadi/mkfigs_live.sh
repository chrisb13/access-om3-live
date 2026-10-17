#!/bin/bash
#PBS -N mkfigs_live
#PBS -q normal
#PBS -l ncpus=16
#PBS -l mem=190GB
#PBS -l jobfs=1GB
#PBS -l walltime=24:00:00
# One job per experiment. A 4-hour job with 120 GB ran out of walltime on its second experiment
# (Oct 2026), using 105 GB with the dask workers pausing at their memory limit; long and 8 km
# runs need much longer. SUs are only charged for the time actually used.
#PBS -W umask=0022

# Nightly version of access-om3-paper-1/notebooks/mkfigs.sh: runs NOTEBOOKS for the experiment
# in $RUN_LIST (written by submit.sh), against a freshly built datastore, then copies the results
# to $WORK/results/<ENAME>/ for the site. A job killed part-way leaves the previous results there.
set -x
source "$WORK/config.sh"
command -v module >/dev/null || source /etc/profile   # as in COSIMA-recipes-workflow
module purge
module use /g/data/xp65/public/modules
module load "$CONDA_MODULE"

PAPER="$WORK/access-om3-paper-1"
export PYTHONPATH="$PAPER/external/access-model-mkfigs/src:${PYTHONPATH:-}"
while IFS='=' read -r ENAME OUTDIR NBS; do
    # The notebooks for this experiment (space-separated): its own list, else the standard one
    read -r -a nbs <<< "${NBS:-${NOTEBOOKS[*]}}"
    printf -v MKFIGS_NOTEBOOKS '%s:' "${nbs[@]}"
    export MKFIGS_NOTEBOOKS="${MKFIGS_NOTEBOOKS%:}"
    # Temporary datastore in jobfs. Its folder must be named ENAME: mkfigs takes the
    # experiment name from the datastore's parent folder.
    CAT="$PBS_JOBFS/$ENAME"
    mkdir -p "$CAT"
    build-esm-datastore --builder AccessOm3Builder --expt-dir "$OUTDIR" --cat-dir "$CAT" --datastore-name datastore

    OUT="$PAPER/notebooks/mkfigs_output_$ENAME" RESULTS="$WORK/results/$ENAME"
    rm -rf "$OUT" && mkdir -p "$OUT" "$RESULTS"   # this run's output only
    # Model throughput of the completed submissions (access-om3-paper-1's plot_sypd.py, PR #145):
    # first, and straight to the results, so it is updated even if a notebook later fails.
    if [ -f "$PAPER/notebooks/plot_sypd.py" ]; then
        python3 "$PAPER/notebooks/plot_sypd.py" "$OUTDIR" --title "$ENAME" -o "$OUT/sypd.png" \
            && cp "$OUT/sypd.png" "$RESULTS/"
    fi
    python3 -m mkfigs.run --ename "$ENAME" --esmdir "$CAT/datastore.json" --wfolder "$PAPER/"
    rsync -a --delete "$OUT/" "$RESULTS/"   # publish this run's results as a whole
    mv "$WORK/state/$ENAME.pending" "$WORK/state/$ENAME.stamp"
done < "${RUN_LIST:-$WORK/run_list}"
