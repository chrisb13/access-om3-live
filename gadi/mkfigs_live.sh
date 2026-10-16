#!/bin/bash
#PBS -N mkfigs_live
#PBS -q normal
#PBS -l ncpus=16
#PBS -l mem=120GB
#PBS -l jobfs=1GB
#PBS -l walltime=04:00:00
# Sized from a 5-experiment run (Oct 2026): 79 GB peak memory, 83 MB jobfs, 1 h 12 min, 30% CPU use.
# Gadi charges max(ncpus, mem/4GB) per hour, so memory sets the cost: 120 GB is charged as 30 CPUs.
#PBS -W umask=0022

# Nightly version of access-om3-paper-1/notebooks/mkfigs.sh: runs NOTEBOOKS for each
# experiment in $WORK/run_list (written by submit.sh), against a freshly built datastore.
set -x
source "$WORK/config.sh"
command -v module >/dev/null || source /etc/profile   # as in COSIMA-recipes-workflow
module purge
module use /g/data/xp65/public/modules
module load "$CONDA_MODULE"

PAPER="$WORK/access-om3-paper-1"
export PYTHONPATH="$PAPER/external/access-model-mkfigs/src:${PYTHONPATH:-}"
while IFS='=' read -r ENAME OUTDIR EXTRA; do
    # The standard notebooks plus this experiment's own (EXTRA: space-separated, may be empty)
    read -r -a extra <<< "$EXTRA"
    printf -v MKFIGS_NOTEBOOKS '%s:' "${NOTEBOOKS[@]}" "${extra[@]}"
    export MKFIGS_NOTEBOOKS="${MKFIGS_NOTEBOOKS%:}"
    # Temporary datastore in jobfs. Its folder must be named ENAME: mkfigs takes the
    # experiment name from the datastore's parent folder.
    CAT="$PBS_JOBFS/$ENAME"
    mkdir -p "$CAT"
    build-esm-datastore --builder AccessOm3Builder --expt-dir "$OUTDIR" --cat-dir "$CAT" --datastore-name datastore

    rm -rf "$PAPER/notebooks/mkfigs_output_$ENAME"   # site shows only this run's output
    python3 -m mkfigs.run --ename "$ENAME" --esmdir "$CAT/datastore.json" --wfolder "$PAPER/"
    # Model throughput of the completed submissions in the archive folder, once the paper repo
    # has plot_sypd.py (access-om3-paper-1 PR #145).
    if [ -f "$PAPER/notebooks/plot_sypd.py" ]; then
        python3 "$PAPER/notebooks/plot_sypd.py" "$OUTDIR" --title "$ENAME" \
            -o "$PAPER/notebooks/mkfigs_output_$ENAME/sypd.png"
    fi
    mv "$WORK/state/$ENAME.pending" "$WORK/state/$ENAME.stamp"
done < "$WORK/run_list"
