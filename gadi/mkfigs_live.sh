#!/bin/bash
#PBS -N mkfigs_live
#PBS -q normal
#PBS -l ncpus=16
#PBS -l mem=190GB
#PBS -l jobfs=20GB
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
    OUT="$PAPER/notebooks/mkfigs_output_$ENAME" RESULTS="$WORK/results/$ENAME"
    rm -rf "$OUT" && mkdir -p "$OUT" "$RESULTS"   # this run's output only
    t0=$SECONDS   # timings.txt: seconds spent on each step, for the workflow's run summary

    # Datastore, in a jobfs folder named ENAME (mkfigs takes the experiment name from it).
    # Reuse the one payu keeps in the archive when it covers the newest output folder and lists
    # files in this archive; otherwise build our own (minutes, sometimes much longer).
    CAT="$PBS_JOBFS/$ENAME" newest=$(ls -d "$OUTDIR"/output[0-9]* 2>/dev/null | tail -1) how=built
    mkdir -p "$CAT"
    if [ -f "$OUTDIR" ]; then   # a datastore (.json) given in experiments.yml, e.g. ACCESS-CM3: use it as it is
        ln -sf "$OUTDIR" "$CAT/datastore.json"
        for f in "${OUTDIR%.json}".*; do [ "$f" = "$OUTDIR" ] || ln -sf "$f" "$CAT/"; done   # its catalogue (.csv)
        how="given in experiments.yml"
    fi
    for ds in "$OUTDIR/experiment_datastore.json" "$OUTDIR/datastore.json"; do
        base=${ds%.json}
        csv=$(ls "$base".csv* 2>/dev/null | head -1)
        if [ -r "$ds" ] && [ -n "$csv" ] && [ -n "$newest" ] && [ "$ds" -nt "$newest" ] \
           && python3 "$WORK/gadi/datastore_ok.py" "$ds" "$csv" "$OUTDIR"; then
            ln -sf "$ds" "$CAT/datastore.json"
            ln -sf "$csv" "$CAT/"   # a relative catalog_file in the json is found next to it
            how="reused ${ds##*/}"
            break
        fi
    done
    if [ "$how" = built ] && [ -d "$OUTDIR" ]; then
        build-esm-datastore --builder AccessOm3Builder --expt-dir "$OUTDIR" --cat-dir "$CAT" --datastore-name datastore
    fi
    echo "datastore $((SECONDS - t0)) $how" >> "$OUT/timings.txt"

    # Model throughput of the completed submissions (access-om3-paper-1's plot_sypd.py, PR #145):
    # first, and straight to the results, so it is updated even if a notebook later fails.
    t0=$SECONDS
    sypd=""
    if [ -f "$PAPER/notebooks/plot_sypd.py" ] && [ -d "$OUTDIR" ]; then
        # Up to 3 tries: the conda environment's container occasionally fails to start ("container creation failed")
        for try in 1 2 3; do
            python3 "$PAPER/notebooks/plot_sypd.py" "$OUTDIR" --title "$ENAME" -o "$OUT/sypd.png" \
                && cp "$OUT/sypd.png" "$RESULTS/" && break
            sypd="failed (try $try)"; sleep 30
        done
        [ -s "$OUT/sypd.png" ] && sypd=""
    fi
    echo "sypd $((SECONDS - t0)) $sypd" >> "$OUT/timings.txt"
    t0=$SECONDS
    python3 -m mkfigs.run --ename "$ENAME" --esmdir "$CAT/datastore.json" --wfolder "$PAPER/"
    echo "notebooks $((SECONDS - t0))" >> "$OUT/timings.txt"
    rsync -a --delete "$OUT/" "$RESULTS/"   # publish this run's results as a whole
    mv "$WORK/state/$ENAME.pending" "$WORK/state/$ENAME.stamp"
done < "${RUN_LIST:-$WORK/run_list}"
