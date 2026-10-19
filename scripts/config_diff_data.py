"""Data for the Config Diff page: for each branch in the Experiments table, its MOM_input and its settings history,
read from one clone of access-om3-experiments (every branch is a payu control directory with its run commits)."""
import re
import subprocess
from datetime import datetime, timedelta
import sys
import urllib.parse
from pathlib import Path

REPO = "ACCESS-Community-Hub/access-om3-experiments"
BRANCH_LINK = re.compile(rf"\[[^\]]+\]\(https://github\.com/{REPO}/tree/([^)\s]+)\)")
MODULE = re.compile(r"^!\s*===\s*module\s+(.+?)\s*===")
PARAM = re.compile(r"^(?:#override\s+)?([A-Za-z]\w*)\s*=\s*([^!]*?)\s*(?:!(.*))?$")


def experiments(md: str) -> list:
    """(branch, heading) for the first link of each table row, in order."""
    out, group = [], "Key experiments"
    for line in md.splitlines():
        if line.startswith("## "):
            group = line[3:].strip()
        m = BRANCH_LINK.search(line) if line.startswith("|") else None
        if m and urllib.parse.unquote(m[1]) not in [b for b, _ in out]:
            out.append((urllib.parse.unquote(m[1]), group))
    return out


def parse(text: str, params: dict, fname: str) -> None:
    """Add {module: {NAME: {value, units, default, line}}} entries from one file; later files win."""
    module, where = "MOM_override", {v: m for m, vs in params.items() for v in vs}
    for n, line in enumerate(text.splitlines(), 1):
        if m := MODULE.match(line):
            module = m[1]
        elif m := PARAM.match(line):
            note = m[3] or ""
            units, default = re.search(r"\[([^\]]+)\]", note), re.search(r"default\s*=\s*(.+)", note)
            old = params.get(where.get(m[1]), {}).get(m[1], {})
            mod = where.get(m[1], module)
            params.setdefault(mod, {})[m[1]] = {
                "value": m[2], "units": units and units[1].strip() or old.get("units"),
                "default": default and default[1].strip() or old.get("default"), "line": n,
                **({"file": fname} if fname != "MOM_input" else {})}
            where[m[1]] = mod


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gadi"))
import run_status  # noqa: E402


def _git(clone: Path, *args: str) -> str:
    """Output of a git command in the clone; raises if it fails."""
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True, text=True).stdout


def _clock(clone: Path, ref: str):
    """The run clock (start date, run length) in nuopc.runconfig at the branch's first run commit, or None."""
    first = next((line.split()[0] for line in _git(clone, "log", "--first-parent", "--reverse", "--format=%H %s", ref).splitlines()
                  if re.search(r": Run \d+$", line)), None)
    if not first:
        return None
    rc = run_status.parse_runconfig(_git(clone, "show", f"{first}:nuopc.runconfig"))
    get = lambda k: next((v for key, v in rc.items() if key.endswith("." + k)), None)
    return {k: get(k) for k in ("start_ymd", "restart_n", "restart_option")}


def approx_runs(history, clock) -> list:
    """Approximate payu run records (model dates only) from the run clock, since a branch has no payu archive."""
    try:
        s, n, opt = clock["start_ymd"], int(clock["restart_n"]), clock["restart_option"]
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:8])
    except (TypeError, KeyError, ValueError):
        return []

    def add(k: int):
        if opt == "nyears":
            return datetime(y + k * n, m, d)
        if opt == "nmonths":
            months = m - 1 + k * n
            return datetime(y + months // 12, months % 12 + 1, d)
        if opt == "ndays":
            return datetime(y, m, d) + timedelta(days=k * n)

    if not history or not history["runs"] or add(0) is None:
        return []
    top = max(counter for _, counter in history["runs"])
    return [{"run": k, "status": 0, "model_start": add(k).isoformat(), "model_finish": add(k + 1).isoformat()} for k in range(top + 1)]


def collect(experiments_md: str, work: Path) -> list:
    """One dict per Experiments-table branch that has a MOM_input: branch, group, tip, parameters, settings history, run records."""
    clone = work / "experiments.git"
    subprocess.run(["git", "clone", "--bare", "--quiet", "--no-tags", f"https://github.com/{REPO}", str(clone)], check=True)
    out = []
    for branch, group in experiments(experiments_md):
        ref = f"refs/heads/{branch}"
        try:
            sha = _git(clone, "rev-parse", "--verify", ref).strip()
            params = {}
            parse(_git(clone, "show", f"{ref}:MOM_input"), params, "MOM_input")
            try:
                parse(_git(clone, "show", f"{ref}:MOM_override"), params, "MOM_override")
            except subprocess.CalledProcessError:
                pass  # no MOM_override on this branch
            history = run_status.settings_history(str(clone), ref)
            runs = approx_runs(history, _clock(clone, ref))
        except Exception as err:  # one unreadable branch must not take the page down
            print(f"::warning::{branch}: left out of the Config Diff page: {err}", file=sys.stderr)
            continue
        out.append({"branch": branch, "slug": re.sub(r"[^A-Za-z0-9._-]+", "_", branch), "group": group, "sha": sha,
                    "params": params, "history": history, "runs": runs})
    return out
