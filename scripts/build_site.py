"""Assemble build/ (docs tree + nav) from Gadi outputs in content/, theme/, pages/ and Experiments.md."""
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import json

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT, THEME, BUILD, STATUS = ROOT / "content", ROOT / "theme" / "documentation", ROOT / "build", ROOT / "status"
CONFIGS_SITE = "https://access-om3-configs.access-hive.org.au/latest/"
CANBERRA = ZoneInfo("Australia/Canberra")
REPO = os.environ.get("GITHUB_REPOSITORY", "ACCESS-NRI/access-om3-live")  # set by GitHub Actions


def _sypd(run: dict) -> str:
    """Simulated years per day for one payu run, or '' if its dates can't be read."""
    try:
        days = (datetime.fromisoformat(run["model_finish"]) - datetime.fromisoformat(run["model_start"])).days
        return f"{days / 365.25 / (run['seconds'] / 86400):.2f}"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return ""


def _when(ts) -> str:
    """A Unix time as Canberra local time, e.g. '2026-10-16 10:20 AEDT'."""
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(CANBERRA).strftime("%Y-%m-%d %H:%M %Z") if ts else ""


def _result(run: dict) -> str:
    """✅ / ❌ for a finished payu run, else its stage."""
    return {0: "✅"}.get(run.get("status"), "❌" if run.get("status") is not None else run.get("stage") or "?")


def health(st: dict) -> tuple:
    """(emoji, sentence) for how the latest submission went, judged from payu's records and the logs."""
    runs, now = st.get("runs", []), datetime.now(timezone.utc).timestamp()
    if not runs:
        return "❔", "No payu run records found."
    last = runs[-1]
    errors = [v["mtime"] for k, v in st.get("logs", {}).items() if "/error_logs/" in k]
    med = next((v for k, v in st.get("run_logs", {}).items() if k.endswith("work/log/med.log")), None)
    if last.get("status") not in (0, None):
        return "❌", f"Run {last['run']} failed (payu exit status {last['status']}): probably a model crash. See the logs below."
    if last.get("status") == 0:
        if errors and max(errors) > (last.get("mtime") or 0):
            return "❌", f"Run {last['run']} completed, but a newer error log exists: possible model crash since."
        return "✅", f"Run {last['run']} completed successfully ({_when(last.get('mtime'))})."
    if med and now - med["mtime"] < 3 * 3600:
        return "🏃", f"Run {last['run']} is in progress (`work/log/med.log` updated {_when(med['mtime'])})."
    return "⚠️", (f"Run {last['run']} has no result yet (stage: {last.get('stage')}) and no recent progress: "
                  "it may be queued, or may have been killed.")


def _logs(logs: dict) -> list:
    """Collapsible blocks with the last lines of each log, titled with its full Gadi path and modification time."""
    md = []
    for path, log in logs.items():
        body = "\n".join("        " + line for line in log["text"].splitlines())
        md += [f'??? note "{path} (last lines; modified {_when(log["mtime"])})"', "    ```text", body, "    ```", ""]
    return md


def status_page(e: str, st: dict, pages: list, sypd_png: bool) -> str:
    """Markdown for an experiment's run-status page, from gadi/run_status.py's snapshot."""
    runs, archive, run_dir = st.get("runs", []), st.get("archive", "?"), st.get("run_dir")
    done = [r for r in runs if r.get("model_finish")]
    emoji, sentence = health(st)
    md = [f"# {e}\n", f"{emoji} **Latest submission:** {sentence}\n",
          "**Notebooks:** " + (", ".join(f"[{nb}]({nb}/index.md)" for p in pages for nb in p) or "none yet") + "\n"]
    if run_dir:
        md += ["## Run directory: current or crashed run\n", f"Read from the payu control directory `{run_dir}`.\n"]
        med = next((v["text"] for k, v in st["run_logs"].items() if k.endswith("work/log/med.log")), "")
        dates = [line for line in med.splitlines() if "Model Date:" in line]
        if dates:
            md.append(f"**Latest model date in `work/log/med.log`:** {dates[-1].split('Model Date:')[1].split()[0]}"
                      f" (rate {dates[-1].split('rate =')[-1].strip()})\n")
        md += _logs(st["run_logs"]) or ["No logs found there.\n"]
    md += ["## Archive folder: completed runs\n", f"Read from the payu archive folder `{archive}`.\n",
           f"- **Output folders:** {st.get('outputs', 0)} (latest `{st.get('latest_output')}`)",
           f"- **Model date reached:** {done[-1]['model_finish'][:10] if done else 'unknown'}",
           f"- **Runs recorded by payu:** {len(runs)}, of which failed: {sum(1 for r in runs if r.get('status') not in (0, None))}", ""]
    if sypd_png:
        md += ["### Model throughput\n", "Simulated years per day, made by `plot_sypd.py` from the "
               f"`outputNNN/log/med.log` files of the completed submissions in `{archive}`.\n",
               "![Simulated years per day](sypd.png)\n"]
    if runs:
        md += ["### Recent runs\n", f"From payu's job records in `{archive}/payu_jobs`; times are Canberra time.\n",
               "| Run | PBS job | Result | Model period | Wall time (h) | SYPD | Record last modified |",
               "|---|---|---|---|---|---|---|"]
        for r in runs[-10:][::-1]:
            period = f"{(r.get('model_start') or '?')[:10]} → {(r.get('model_finish') or '?')[:10]}"
            hours = f"{r['seconds'] / 3600:.1f}" if r.get("seconds") else ""
            md.append(f"| {r.get('run')} | {r.get('job_id') or ''} | {_result(r)} | {period} | {hours} | {_sypd(r)} | {_when(r.get('mtime'))} |")
        md.append("")
    if st.get("logs"):
        md += ["### Latest logs in the archive folder\n"] + _logs(st["logs"])
    if st.get("metadata"):
        md += [f'??? info "{archive}/metadata.yaml"', "    ```yaml", "\n".join("    " + l for l in st["metadata"].splitlines()), "    ```", ""]
    return "\n".join(md)


def main(summary_md: str) -> None:
    """Write build/docs and build/mkdocs.yml."""
    cfg = yaml.safe_load((ROOT / "experiments.yml").read_text())
    docs = BUILD / "docs"
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.copytree(THEME / "docs", docs, ignore=shutil.ignore_patterns("pages"))
    shutil.copytree(THEME / "overrides", BUILD / "overrides")
    shutil.copy2(ROOT / "pages" / "wide.css", docs / "css")

    home = (ROOT / "pages" / "home.md").read_text()
    (docs / "index.md").write_text(home.replace("{repo}", REPO).replace("{notebooks}", ", ".join(cfg["notebooks"])))
    # Evaluation Summary: access-om3-configs' Experiments.md, with site-relative links sent back there,
    # and a note under its heading on how to use it.
    summary = Path(summary_md).read_text().replace("](/", f"]({CONFIGS_SITE}")
    (docs / "evaluation-summary.md").write_text(summary.replace("\n# ACCESS-OM3 Experiments\n", (
        "\n# ACCESS-OM3 Experiments\n\n!!! tip \"Finding an experiment's evaluation\"\n"
        "    Each row is one experiment. Click **Evaluation** in its row to open the fixed, versioned "
        "evaluation website for that run, with all its figures and notebooks. This table is copied from the "
        f"[Experiments]({CONFIGS_SITE}Experiments/) page of the access-om3-configs documentation.\n"), 1))

    live_nav, rows = ["live/index.md"], []
    for entry in cfg["experiments"]:
        path = entry["archive"] if isinstance(entry, dict) else entry
        notebooks = cfg["notebooks"] + (entry.get("notebooks", []) if isinstance(entry, dict) else [])
        e = Path(path).name  # ENAME = name of the archive folder
        if e == "archive":  # submit.sh skips these: the link's target folder must be listed instead
            rows.append(f"| `{path}` | ⚠️ not monitored: list the folder this `archive` link points to | | | |")
            continue
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
        st = json.loads((STATUS / f"{e}.json").read_text()) if (STATUS / f"{e}.json").exists() else {}
        (docs / "live" / e).mkdir(parents=True, exist_ok=True)
        if (src / "sypd.png").exists():
            shutil.copy2(src / "sypd.png", docs / "live" / e)
        (docs / "live" / e / "index.md").write_text(status_page(e, st, pages, (src / "sypd.png").exists()))
        live_nav.append({e: [f"live/{e}/index.md", *pages]})
        done = [r for r in st.get("runs", []) if r.get("model_finish")]
        reached = f"{health(st)[0]} {done[-1]['model_finish'][:10] if done else ''}" if st.get("runs") else "–"

        errors, log = src / "mkmd" / "mkfigs_errors.log", src / "mkmd" / "mkfigs_run.log"
        failed = [l[8:].strip() for l in errors.read_text().splitlines() if l.startswith("FAILED: ")] if errors.exists() else []
        for nb in failed:
            print(f"::warning::{e}: notebook {nb} failed")
        ran = (datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).astimezone(ZoneInfo("Australia/Canberra"))
               .strftime("%Y-%m-%d %H:%M %Z") if log.exists() else "not run yet")
        shown = "<br>".join(f"[{nb}]({e}/{nb}/index.md)" for p in pages for nb in p)
        rows.append(f"| [{e}]({e}/index.md) | {reached} | {ran} | {shown or '–'} | {'<br>'.join(failed) or '–'} |")

    (docs / "live").mkdir(exist_ok=True)
    (docs / "live" / "index.md").write_text(
        # Hide the empty "On this page" column so the table gets the room (as the Experiments page does).
        "<style>.md-sidebar--secondary:not([hidden]) { display: none }</style>\n\n"
        "# OM3 Monitor\n\nNotebooks re-run nightly on Gadi for runs in progress; an experiment is only re-run "
        "when it has new output. Click an experiment for its run status. Times are Canberra time.\n\n"
        "| Experiment | Latest submission, model date reached | Notebooks last run | Notebooks shown | Failed |\n|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n")

    nav = [{"Home": "index.md"}, {"OM3 Monitor": live_nav}, {"Evaluation Summary": "evaluation-summary.md"}]
    (BUILD / "mkdocs.yml").write_text(yaml.safe_dump({"INHERIT": "../mkdocs.yml", "nav": nav}, sort_keys=False))


if __name__ == "__main__":
    main(sys.argv[1])
