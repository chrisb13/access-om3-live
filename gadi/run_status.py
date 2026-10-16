"""Write a small JSON snapshot of a payu archive folder for its status page (stdlib only, Python 3.6+).

Usage: run_status.py ARCHIVE_DIR OUT.json [RUN_DIR]
RUN_DIR is the payu control directory: it holds the current (or crashed) run's PBS and model logs.
"""
import glob
import json
import os
import sys


CONFIG_FILES = ("config.yaml", "MOM_input", "ice_in", "nuopc.runconfig", "nuopc.runseq")  # shown in full


def tail(path, lines=30, nbytes=16384):
    """Last few lines of a (possibly huge) text file."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - nbytes))
        return b"".join(f.readlines()[-lines:]).decode(errors="replace")


def logs_of(paths):
    """{full path: {"text": last lines, "mtime": modification time}} for the non-empty files among paths."""
    return {p: {"text": tail(p), "mtime": os.path.getmtime(p)}
            for p in paths if os.path.isfile(p) and os.path.getsize(p)}


def run_dir_logs(run_dir):
    """Tails of the newest PBS logs, the model's .out/.err and work/log/med.log in a payu control directory."""
    pbs = sorted(glob.glob(os.path.join(run_dir, "*.[oe][0-9]*")), key=os.path.getmtime)[-4:]
    paths = pbs + [os.path.join(run_dir, f) for f in ("access-om3.out", "access-om3.err",
                                                       os.path.join("work", "log", "med.log"))]
    return logs_of(paths)


def main(archive, dest, run_dir=None):
    """Collect payu run records (payu_jobs/*/run/*.json), output count and recent log tails."""
    runs = []
    for jf in glob.glob(os.path.join(archive, "payu_jobs", "*", "run", "*.json")):
        try:
            with open(jf) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        t = d.get("timings", {})
        runs.append({"run": d.get("payu_current_run"), "job_id": d.get("scheduler_job_id"),
                     "stage": d.get("stage"), "status": d.get("payu_run_status"),
                     "start": t.get("payu_start_time"), "seconds": t.get("payu_total_duration_seconds"),
                     "model_start": d.get("model_start_time"), "model_finish": d.get("model_finish_time"),
                     "mtime": os.path.getmtime(jf)})
    runs.sort(key=lambda r: (int(r["run"] or 0), r["start"] or ""))

    outputs = sorted(glob.glob(os.path.join(archive, "output[0-9]*")))
    logs = [os.path.join(outputs[-1], f) for f in ("access-om3.out", "access-om3.err", os.path.join("log", "med.log"))
            ] if outputs else []
    for sub in ("pbs_logs", "error_logs"):  # the two most recent of each
        logs += sorted(glob.glob(os.path.join(archive, sub, "*")), key=os.path.getmtime)[-2:]
    meta = os.path.join(archive, "metadata.yaml")
    configs = [os.path.join(run_dir, f) for f in CONFIG_FILES] if run_dir else []
    with open(dest, "w") as f:
        json.dump({"archive": archive, "outputs": len(outputs),
                   "latest_output": os.path.basename(outputs[-1]) if outputs else None,
                   "runs": runs,
                   "logs": logs_of(logs),
                   "metadata": open(meta).read() if os.path.isfile(meta) else None,
                   "run_dir": run_dir, "run_logs": run_dir_logs(run_dir) if run_dir else {},
                   "configs": {p: {"text": tail(p, lines=100000, nbytes=500000), "mtime": os.path.getmtime(p)}
                               for p in configs if os.path.isfile(p)}}, f, indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
