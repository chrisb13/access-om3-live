"""Assemble build/ (docs tree + nav) from Gadi outputs in content/, theme/ and Experiments.md."""
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import json

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT, THEME, BUILD, STATUS = ROOT / "content", ROOT / "theme" / "documentation", ROOT / "build", ROOT / "status"
CONFIGS_SITE = "https://access-om3-configs.access-hive.org.au/latest/"


def ename(path: str) -> str:
    """An experiment's name: its archive folder's name."""
    return Path(path).name


def _snapshot(path: Path) -> dict:
    """An experiment's run_status.py snapshot, or {} if it's missing, empty or unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        if path.exists():
            print(f"::warning::{path.name}: status snapshot is empty or unreadable; showing the page without it")
        return {}


def notebook_times(log: Path) -> list:
    """[(notebook, 'OK' / 'FAILED' / "didn't finish", minutes or None)] from mkfigs' mkfigs_run.log."""
    started, out = {}, []
    for line in log.read_text(errors="replace").splitlines() if log.exists() else []:
        m = re.match(r"(\S+ \S+?),\d+ \S+\s+(START|OK|FAILED)\s+(\S+)", line)
        if not m:
            continue
        t = datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S")
        if m[2] == "START":
            started[m[3]] = t
        elif m[3] in started:
            out.append((m[3], m[2], (t - started.pop(m[3])).total_seconds() / 60))
    return out + [(nb, "didn't finish", None) for nb in started]


def run_summary(experiments: list) -> str:
    """Markdown for the GitHub Actions run summary: per experiment, what this run did, its last notebook job
    and how its notebooks went (from status/<ENAME>.job.json and the job's mkfigs_run.log)."""
    rows = ["### Notebook jobs\n", "| Experiment | This run | Last job: exit, walltime, memory | Time: datastore / SYPD / "
            "notebooks | Notebooks: OK / failed / didn't finish | Slowest notebook |", "|---|---|---|---|---|---|"]
    for e in experiments:
        job, nbs = _snapshot(STATUS / f"{e}.job.json"), notebook_times(CONTENT / e / "mkmd" / "mkfigs_run.log")
        last = job.get("last") or {}
        mem = re.match(r"(\d+)kb$", last.get("mem") or "")
        last["mem"] = f"{int(mem[1]) / 1024 ** 2:.0f} GB" if mem else last.get("mem")  # PBS reports kilobytes
        res = ", ".join(f"{k} {last[k]}" for k in ("exit", "walltime", "mem") if last.get(k)) or "–"
        if last.get("job"):
            res = f"{last['job']}<br>{res}"
        counts = " / ".join(str(sum(r == k for _, r, _ in nbs)) for k in ("OK", "FAILED", "didn't finish")) if nbs else "–"
        timed = sorted((t, nb) for nb, _, t in nbs if t is not None)
        slow = f"{timed[-1][1]} ({timed[-1][0]:.1f} min)" if timed else "–"
        steps = {}  # timings.txt, written by the job: "<step> <seconds> [how]"
        tfile = CONTENT / e / "timings.txt"
        for line in tfile.read_text().splitlines() if tfile.exists() else []:
            name, secs, *how = line.split()
            steps[name] = f"{int(secs) / 60:.1f} min" + (f" ({' '.join(how)})" if how else "")
        took = " / ".join(steps.get(k, "–") for k in ("datastore", "sypd", "notebooks")) if steps else "–"
        rows.append(f"| {e} | {job.get('decision', '–')} | {res} | {took} | {counts} | {slow} |")
    return "\n".join(rows) + "\n\nExit status 0 means the job finished; -29 means it ran out of walltime.\n"


def main(summary_md: str) -> None:
    """Write build/docs and build/mkdocs.yml."""
    cfg = yaml.safe_load((ROOT / "experiments.yml").read_text())
    docs = BUILD / "docs"
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.copytree(THEME / "docs", docs, ignore=shutil.ignore_patterns("pages"))
    shutil.copytree(THEME / "overrides", BUILD / "overrides")

    # Evaluation Summary: access-om3-configs' Experiments.md, with site-relative links sent back there.
    (docs / "index.md").write_text(Path(summary_md).read_text().replace("](/", f"]({CONFIGS_SITE}"))

    live_nav, rows = ["live/index.md"], []
    for entry in cfg["experiments"]:
        path = entry["archive"] if isinstance(entry, dict) else entry
        notebooks = (entry.get("notebooks") if isinstance(entry, dict) else None) or cfg["notebooks"]  # own list replaces
        e = ename(path)
        src, pages = CONTENT / e, []
        for nb in notebooks:
            md, ipynb = src / "mkmd" / f"{nb}.md", src / f"{nb}_rendered.ipynb"
            if not (md.exists() and ipynb.exists()):
                continue
            dst = docs / "live" / e / nb
            dst.mkdir(parents=True)
            text = md.read_text().replace(f"/assets/experiments/{e}/", "")  # figures sit beside the page
            (dst / "index.md").write_text(text.replace(f"notebooks/{nb}.ipynb", "notebook.ipynb"))
            for png in (src / "mkmd").glob(f"{nb}_[0-9]*.png"):
                shutil.copy2(png, dst)
            shutil.copy2(ipynb, dst / "notebook.ipynb")
            pages.append({nb: [f"live/{e}/{nb}/index.md", {"Notebook": f"live/{e}/{nb}/notebook.ipynb"}]})
        if pages:
            live_nav.append({e: pages})

        errors, log = src / "mkmd" / "mkfigs_errors.log", src / "mkmd" / "mkfigs_run.log"
        failed = [l[8:].strip() for l in errors.read_text().splitlines() if l.startswith("FAILED: ")] if errors.exists() else []
        for nb in failed:
            print(f"::warning::{e}: notebook {nb} failed")
        ran = datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if log.exists() else "not run yet"
        rows.append(f"| {e} | {ran} | {len(pages)} | {', '.join(failed) or '–'} |")

    (docs / "live").mkdir(exist_ok=True)
    names = [ename(x["archive"] if isinstance(x, dict) else x) for x in cfg["experiments"]]
    (docs / "live" / "index.md").write_text(
        "# Live\n\nNotebooks re-run nightly on Gadi for runs in progress; an experiment is only re-run "
        "when it has new output.\n\n| Experiment | Last run | Notebooks shown | Failed |\n|---|---|---|---|\n"
        + "\n".join(rows) + "\n")

    if os.environ.get("GITHUB_STEP_SUMMARY"):  # shown at the top of the workflow run's page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(run_summary([n for n in names if n != "archive"]))
    nav = [{"Evaluation Summary": "index.md"}, {"Live": live_nav}]
    (BUILD / "mkdocs.yml").write_text(yaml.safe_dump({"INHERIT": "../mkdocs.yml", "nav": nav}, sort_keys=False))


if __name__ == "__main__":
    main(sys.argv[1])
