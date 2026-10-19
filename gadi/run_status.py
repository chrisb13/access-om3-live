"""Write a small JSON snapshot of a payu archive folder for its status page (stdlib only, Python 3.6+).

Usage: run_status.py ARCHIVE_DIR OUT.json [RUN_DIR]
RUN_DIR is the payu control directory: it holds the current (or crashed) run's PBS and model logs.
"""
import csv
import glob
import gzip
import json
import math
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


# Settings history: edits to these files in the control directory's git log, after payu's first run commit.
HISTORY_FILES = ("config.yaml", "MOM_input", "MOM_override", "nuopc.runconfig", "ice_in")
BUILD_KEYS = ("model", "exe", "modules.use", "modules.load")  # the config.yaml entries that say which model build ran
RUN_COMMIT = re.compile(r": Run (\d+)$")  # payu's per-run commit message, e.g. "2026-06-30 20:11:46: Run 65"


def git(run_dir, *args):
    """Output of a git command in run_dir, or None if it fails."""
    try:
        # safe.directory: some run directories belong to other accounts, which git otherwise refuses to read
        p = subprocess.run(("git", "-c", "safe.directory=" + run_dir, "-C", run_dir) + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def parse_params(text):
    """MOM_input or MOM_override: {NAME: value} from unindented 'NAME = value ! comment' lines."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"(?:#override\s+)?([A-Za-z]\w*(?:%\w+)*)\s*=\s*([^!]*)", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def parse_runconfig(text):
    """nuopc.runconfig: {'SECTION.key': value} from the 'SECTION::' blocks."""
    out, section = {}, None
    for line in text.splitlines():
        s = line.strip()
        if s == "::":
            section = None
        elif s.endswith("::"):
            section = s[:-2]
        elif section and "=" in s:
            key, value = s.split("=", 1)
            out[section + "." + key.strip()] = value.strip()
    return out


def parse_namelist(text):
    """ice_in (Fortran namelist): {'group.key': value}; several 'key = value' pairs may share a line."""
    out, group = {}, None
    for line in text.splitlines():
        s = line.split("!", 1)[0].strip()
        if s.startswith("&"):
            group = s[1:].split()[0] if s[1:].split() else None
        elif s == "/":
            group = None
        elif group:
            for m in re.finditer(r"(\w+)\s*=\s*(.*?)(?=,\s*\w+\s*=|$)", s):
                out[group + "." + m.group(1)] = m.group(2).strip().rstrip(",").strip()
    return out


def parse_config(text):
    """config.yaml: {'a.b': value} for plain maps, lists and scalars; list items are joined with ', '."""
    out, stack = {}, []  # stack: (indent, key) of the enclosing keys
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            key = ".".join(k for _, k in stack)
            out[key] = (out[key] + ", " if out.get(key) else "") + s[2:].strip()
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        key, _, value = s.partition(":")
        stack.append((indent, key.strip()))
        if value.strip():
            out[".".join(k for _, k in stack)] = value.strip()
    return out


def norm(value):
    """Comparable form of a value, so formatting alone (quotes, case, 1.0E-04 vs 0.0001) is not a change."""
    if value is None:
        return None
    s = value.strip().strip("\"'").strip().lower()
    s = {".true.": "true", ".false.": "false"}.get(s, s)
    try:
        number = float(s.replace("d", "e"))
    except ValueError:
        return s
    return number if math.isfinite(number) else s


def settings_at(run_dir, sha):
    """{(file, key): (value, source file)} for the tracked files at a commit; MOM_override wins over MOM_input."""
    out = {}
    for name, parse in (("config.yaml", parse_config), ("MOM_input", parse_params), ("MOM_override", parse_params),
                        ("nuopc.runconfig", parse_runconfig), ("ice_in", parse_namelist)):
        for key, value in parse(git(run_dir, "show", "%s:%s" % (sha, name)) or "").items():
            if name == "config.yaml" and key == "input":  # one entry per input file, so a changed file shows up on its own
                seen = {}
                for item in value.split(", "):
                    base = item.rstrip("/").rpartition("/")[2] or item
                    seen[base] = seen.get(base, 0) + 1
                    out[(name, "input/" + base + ("" if seen[base] == 1 else "#%d" % seen[base]))] = (item, name)
                continue
            out[("MOM_input" if name == "MOM_override" else name, key)] = (value, name)
    return out


def settings_history(run_dir, ref="HEAD"):
    """Edits to the tracked settings since payu's first run commit, from the git log of its control directory."""
    log = git(run_dir, "log", "--first-parent", "--reverse", "--format=%H%x1f%cI%x1f%s", ref)
    if log is None:
        return None
    commits = [line.split("\x1f") for line in log.splitlines() if line.count("\x1f") == 2]
    first = next((i for i, c in enumerate(commits) if RUN_COMMIT.search(c[2])), None)
    if first is None:
        return {"runs": [], "changes": [], "build": {}}
    touched = set((git(run_dir, "log", "--first-parent", "--format=%H", commits[first][0] + ".." + ref, "--",
                       *HISTORY_FILES) or "").split())
    prev, runs, changes, counter = settings_at(run_dir, commits[first][0]), [], [], None
    for sha, when, subject in commits[first:]:
        if sha in touched:  # a run commit can carry edits too (e.g. config.yaml), so diff before counting it
            now = settings_at(run_dir, sha)
            for key in sorted(set(prev) | set(now)):
                (old, old_src), (new, new_src) = prev.get(key, (None, None)), now.get(key, (None, None))
                if norm(old) != norm(new):
                    changes.append({"when": when, "sha": sha[:8], "subject": subject, "seq": len(runs),
                                    "counter": counter, "file": new_src or old_src, "key": key[1], "old": old, "new": new})
            prev = now
        m = RUN_COMMIT.search(subject)
        if m:
            counter = int(m.group(1))
            runs.append([when, counter])
    build = {key: prev.get(("config.yaml", key), (None,))[0] for key in BUILD_KEYS}  # as of the latest commit
    return {"runs": runs, "changes": changes, "build": build}


# "Where are my friends?": the closest history this run's control directory shares with access-om3-configs,
# from a bare clone kept next to this script's folder (submit.sh keeps it up to date).
CONFIGS_CLONE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "access-om3-configs.git")
DIFF_LINES = 3000  # longest diff kept in the snapshot


def cgit(*args):
    """Output of a git command in the access-om3-configs clone, or None if it fails."""
    try:  # safe.directory=*: fetching from run directories that belong to other accounts
        p = subprocess.run(("git", "-c", "safe.directory=*", "--git-dir=" + CONFIGS_CLONE) + args, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def _diff(old, new):
    """{'stat', 'text'} for the configuration difference between two commits (manifests left out)."""
    spec = ("--", ".", ":(exclude)manifests")
    text = cgit("diff", old, new, *spec) or ""
    lines = text.splitlines()
    if len(lines) > DIFF_LINES:
        text = "\n".join(lines[:DIFF_LINES]) + "\n... (%d more lines)" % (len(lines) - DIFF_LINES)
    return {"stat": (cgit("diff", "--shortstat", old, new, *spec) or "").strip(), "text": text}


def friends(run_dir, name):
    """Closest commit (and tag) this run's control directory shares with access-om3-configs, with diffs to both.
    None if there is no clone or no git history to compare; {"found": False} if nothing is shared."""
    head = (git(run_dir, "rev-parse", "HEAD") or "").strip()
    if not head or not os.path.isdir(CONFIGS_CLONE):
        return None
    ref = "refs/runs/" + re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if cgit("fetch", "-q", "--no-tags", run_dir, "+HEAD:" + ref) is None:
        return None
    # Commits shared with any branch or tag: rev-list's boundary ("-") lines, or the run's own HEAD if nothing is new
    out = cgit("rev-list", "--boundary", ref, "--not", "--branches", "--tags")
    if out is None:
        return None
    shared = [line[1:] for line in out.splitlines() if line.startswith("-")] or ([] if out.strip() else [head])
    if not shared:
        return {"found": False}
    dated = sorted((cgit("log", "-1", "--format=%ct", c) or "0").strip() + " " + c for c in shared)
    sha = dated[-1].split()[1]  # the most recent shared commit
    sha_short, when, subject = (cgit("log", "-1", "--format=%h%x1f%cI%x1f%s", sha) or "\x1f\x1f").strip().split("\x1f")
    branches = (cgit("branch", "--contains", sha, "--format=%(refname:short)") or "").split()
    ahead = lambda b: int((cgit("rev-list", "--count", sha + ".." + b) or "0").strip() or 0)
    branch = min(branches[:60], key=lambda b: (b.startswith("auto/"), ahead(b)), default=None)
    found = {"found": True, "sha": sha, "short": sha_short, "when": when, "subject": subject, "branch": branch,
             "run_head": head[:8], "diff": _diff(sha, ref)}
    tag = (cgit("describe", "--tags", "--abbrev=0", sha) or "").strip()
    if tag:
        found["tag"] = {"name": tag, "behind": int((cgit("rev-list", "--count", tag + ".." + sha) or "0").strip() or 0),
                        "diff": _diff(tag, ref)}
    return found


def payu_status(run_dir):
    """Output of `payu status` in the run's control directory, or None if payu isn't available or it fails
    (for instance for a run owned by another account: payu looks for the archive under this account's /scratch)."""
    try:
        p = subprocess.run(["payu", "status"], cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() if p.returncode == 0 and "Run:" in p.stdout else None


def datastore_summary(path):
    """For an experiment given as an intake-esm datastore (.json), e.g. ACCESS-CM3: its catalogue's file count and years."""
    out = {"files": 0, "years": None, "mtime": os.path.getmtime(path) if readable(path) else None}
    try:
        with open(path) as f:
            catalog = json.load(f).get("catalog_file") or ""
        catalog = catalog[len("file://"):] if catalog.startswith("file://") else catalog
        csv_path = os.path.join(os.path.dirname(path), catalog)
        with (gzip.open if csv_path.endswith(".gz") else open)(csv_path, "rt") as f:
            years = set()
            for row in csv.DictReader(f):
                out["files"] += 1
                start = row.get("start_date") or ""
                if row.get("frequency") != "fx" and start[:4].isdigit():  # fixed files carry placeholder dates
                    years.add(start[:4])
        out["years"] = [min(years), max(years)] if years else None
        out["n_years"] = len(years)
    except (OSError, ValueError, csv.Error):
        pass
    return out


def main(archive, dest, run_dir=None):
    """Collect payu run records (payu_jobs/*/run/*.json), output count and recent log tails."""
    if archive.endswith(".json"):  # an experiment given as its datastore (e.g. ACCESS-CM3): no payu records to read
        with open(dest + ".tmp", "w") as f:
            json.dump({"archive": archive, "datastore_only": True, "datastore": datastore_summary(archive)}, f, indent=1)
        os.replace(dest + ".tmp", dest)
        return
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
                   "settings": settings_history(run_dir) if run_dir else None,
                   "friends": friends(run_dir, os.path.basename(dest)[:-5]) if run_dir else None,
                   "payu_status": payu_status(run_dir) if run_dir else None,
                   "work_truncations": truncations([os.path.join(run_dir, "work")]) if run_dir else None, "run_logs": run_dir_logs(run_dir) if run_dir else {},
                   "configs": {p: {"text": tail(p, lines=100000, nbytes=500000), "mtime": os.path.getmtime(p)}
                               for p in configs if readable(p)}}, f, indent=1)
    os.replace(dest + ".tmp", dest)


if __name__ == "__main__":
    main(*sys.argv[1:4])
