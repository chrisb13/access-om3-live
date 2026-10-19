"""Write a small JSON snapshot of a payu archive folder for its status page (stdlib only, Python 3.6+).

Usage: run_status.py ARCHIVE_DIR OUT.json [RUN_DIR]
RUN_DIR is the payu control directory: it holds the current (or crashed) run's PBS and model logs.
"""
import glob
import json
import os
import re
import subprocess
import sys


CONFIG_FILES = ("config.yaml", "MOM_input", "ice_in", "nuopc.runconfig", "nuopc.runseq")  # shown in full


def readable(path):
    """True for a file this account may read: other people's runs can have files only they can read."""
    return os.path.isfile(path) and os.access(path, os.R_OK)


def tail(path, lines=30, nbytes=16384):
    """Last few lines of a (possibly huge) text file."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - nbytes))
        return b"".join(f.readlines()[-lines:]).decode(errors="replace")


def logs_of(paths):
    """{full path: {"text": last lines, "mtime": modification time}} for the non-empty files among paths."""
    return {p: {"text": tail(p), "mtime": os.path.getmtime(p)}
            for p in paths if readable(p) and os.path.getsize(p)}


def run_dir_logs(run_dir):
    """Tails of the newest PBS logs, the model's .out/.err and work/log/med.log in a payu control directory."""
    pbs = sorted(glob.glob(os.path.join(run_dir, "*.[oe][0-9]*")), key=os.path.getmtime)[-4:]
    paths = pbs + [os.path.join(run_dir, f) for f in ("access-om3.out", "access-om3.err",
                                                       os.path.join("work", "log", "med.log"))]
    return logs_of(paths)


# First line of each record in U/V_velocity_truncations, e.g.
# Time 1900 0   0.97 U-velocity violation at 6051: 35, 14 (   2.83 E   79.14 N) Layers 52 to 68. dt =  240.0
TRUNC_HEAD = re.compile(r"Time\s+\d+\s+\d+\s+[\d.]+\s+([UV])-velocity violation at\s+(\d+):\s*(\d+),?\s+(\d+)\s*"
                        r"\(\s*(-?[\d.]+)\s*E\s+(-?[\d.]+)\s*N\)\s*Layers\s+(\d+)\s+to\s+(\d+)\.\s*dt\s*=\s*([\d.]+)")
TRUNC_CAP = 50  # MOM6 MAX_TRUNC_FILE_SIZE_PER_PE default: records written per PE per run


def _values(line):
    """Numbers after the first ':' on a truncation-record line."""
    out = []
    for tok in line.split(":", 1)[1].split():
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def truncations(outputs):
    """Per-output truncation counts (ocean.stats) and records (U/V_velocity_truncations), grouped by 1-degree box."""
    segments, boxes = [], {}
    for out in outputs:
        seg = {"output": os.path.basename(out), "truncs": None, "max_cfl": None, "records": {}, "capped_pes": [],
               "unreadable": []}
        stats = os.path.join(out, "ocean.stats")
        if readable(stats):
            seg["truncs"], cfls = 0, []
            for line in open(stats, errors="replace"):
                f = line.split(",")
                if len(f) > 4 and f[0].strip().isdigit():
                    seg["truncs"] += int(float(f[2]))
                    cfls += [float(v) for v in f[4].split()[1:2]]
            seg["max_cfl"] = max(cfls) if cfls else None
        for uv in "UV":
            path = os.path.join(out, uv + "_velocity_truncations")
            if not readable(path):
                if os.path.isfile(path):
                    seg["unreadable"].append(uv)
                continue
            per_pe, rec = {}, None
            for line in list(open(path, errors="replace")) + ["Time end"]:
                if line.startswith("Time"):
                    if rec:
                        key = (round(rec["lon"]), round(rec["lat"]))
                        b = boxes.setdefault(key, {"records": 0, "cells": set(), "outputs": set(), "uv": set(),
                                                   "lon": [], "lat": [], "layers": [], "max_vel": 0, "max_cfl": 0,
                                                   "top_e": None, "depth": None, "capped": False})
                        b["records"] += 1
                        b["cells"].add((rec["lon"], rec["lat"]))
                        b["outputs"].add(seg["output"])
                        b["uv"].add(rec["uv"])
                        b["lon"].append(rec["lon"]), b["lat"].append(rec["lat"])
                        b["layers"] += [rec["k0"], rec["k1"]]
                        b["max_vel"] = max(b["max_vel"], rec.get("vel", 0))
                        b["max_cfl"] = max(b["max_cfl"], rec.get("cfl", 0))
                        if rec.get("top_e") is not None:
                            b["top_e"] = max(b["top_e"] or rec["top_e"], rec["top_e"], key=abs)
                        if rec.get("depth") is not None:
                            b["depth"] = min(b["depth"] or rec["depth"], rec["depth"])
                        b["pes"] = b.get("pes", set()) | {rec["pe"]}
                    m = TRUNC_HEAD.search(line)
                    rec = None
                    if m:
                        rec = {"uv": m.group(1), "pe": int(m.group(2)), "lon": float(m.group(5)),
                               "lat": float(m.group(6)), "k0": int(m.group(7)), "k1": int(m.group(8))}
                        per_pe[rec["pe"]] = per_pe.get(rec["pe"], 0) + 1
                elif rec and ":" in line:
                    name, vals = line.split(":", 1)[0].strip(), _values(line)
                    if vals and name in ("u(m)", "v(m)"):
                        rec["vel"] = max(abs(v) for v in vals)
                    elif vals and name in ("CFL u", "CFL v"):
                        rec["cfl"] = max(vals)
                    elif vals and name in ("e-", "e+") and rec["k0"] == 1:  # top interface = sea surface height
                        rec["top_e"] = max(rec.get("top_e", vals[0]), vals[0], key=abs)
                    elif vals and name == "D":
                        rec["depth"] = min(vals)
            seg["records"][uv] = sum(per_pe.values())
            seg["capped_pes"] += [pe for pe, n in per_pe.items() if n >= TRUNC_CAP]
        segments.append(seg)
    capped = set(pe for s in segments for pe in s["capped_pes"])
    ranked = sorted(boxes.items(), key=lambda kv: -kv[1]["records"])
    return {"segments": segments, "boxes": [
        {"box": [k[0], k[1]], "lon": sum(b["lon"]) / len(b["lon"]), "lat": sum(b["lat"]) / len(b["lat"]),
         "records": b["records"], "cells": len(b["cells"]), "outputs": sorted(b["outputs"]), "uv": sorted(b["uv"]),
         "layers": [min(b["layers"]), max(b["layers"])], "max_vel": b["max_vel"], "max_cfl": b["max_cfl"],
         "top_e": b["top_e"], "depth": b["depth"], "capped": bool(b["pes"] & capped)} for k, b in ranked[:20]]}


def payu_status(run_dir):
    """Output of `payu status` in the run's control directory, or None if payu isn't available or it fails
    (for instance for a run owned by another account: payu looks for the archive under this account's /scratch)."""
    try:
        p = subprocess.run(["payu", "status"], cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 and "Run:" in p.stdout else None


def main(archive, dest, run_dir=None):
    """Collect payu run records (payu_jobs/*/run/*.json), output count and recent log tails."""
    runs = []
    for jf in glob.glob(os.path.join(archive, "payu_jobs", "*", "run", "*.json")):
        if not readable(jf):
            continue
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
    with open(dest + ".tmp", "w") as f:  # written whole, then renamed: never a half-written snapshot
        json.dump({"archive": archive, "outputs": len(outputs),
                   "latest_output": os.path.basename(outputs[-1]) if outputs else None,
                   "runs": runs,
                   "logs": logs_of(logs),
                   "metadata": open(meta).read() if readable(meta) else None,
                   "truncations": truncations(outputs),
                   "run_dir": run_dir,
                   "payu_status": payu_status(run_dir) if run_dir else None,
                   "work_truncations": truncations([os.path.join(run_dir, "work")]) if run_dir else None, "run_logs": run_dir_logs(run_dir) if run_dir else {},
                   "configs": {p: {"text": tail(p, lines=100000, nbytes=500000), "mtime": os.path.getmtime(p)}
                               for p in configs if readable(p)}}, f, indent=1)
    os.replace(dest + ".tmp", dest)


if __name__ == "__main__":
    main(*sys.argv[1:4])
