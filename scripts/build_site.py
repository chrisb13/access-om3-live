"""Assemble build/ (docs tree + nav) from Gadi outputs in content/, theme/, pages/ and Experiments.md."""
import os
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import json

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT, THEME, BUILD, STATUS = ROOT / "content", ROOT / "theme" / "documentation", ROOT / "build", ROOT / "status"
CONFIGS_SITE = "https://access-om3-configs.access-hive.org.au/latest/"
CANBERRA = ZoneInfo("Australia/Canberra")


def ename(path: str) -> str:
    """An experiment's name: its archive folder's name."""
    return Path(path).name


def _sypd(run: dict) -> str:
    """Simulated years per day for one payu run, or '' if its dates can't be read."""
    try:
        days = (datetime.fromisoformat(run["model_finish"]) - datetime.fromisoformat(run["model_start"])).days
        return f"{days / 365.25 / (run['seconds'] / 86400):.2f}"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return ""


def _date(value, n: int = 10) -> str:
    """First n characters of a payu model date ('1958-01-01T00:00:00'), or '?' when payu didn't record one."""
    return str(value)[:n] if value else "?"


def _start(run: dict, runs: list):
    """A run's model start date; when payu didn't record it, the previous run's finish date."""
    if run.get("model_start") or not str(run.get("run")).isdigit():
        return run.get("model_start")
    prev = [r for r in runs if str(r.get("run")) == str(int(run["run"]) - 1) and r.get("model_finish")]
    return prev[-1]["model_finish"] if prev else None


def _progress(runs: list) -> tuple:
    """(model years completed, average SYPD) from an experiment's completed payu runs; None where they can't be worked out.
    A run whose start payu didn't record (usually the first) counts as the median length of the others."""
    import statistics

    days, known = [], []
    for r in runs:
        if r.get("status") != 0 or not r.get("model_finish"):
            continue
        start = _start(r, runs)
        try:
            d = (datetime.fromisoformat(r["model_finish"]) - datetime.fromisoformat(start)).days if start else None
        except ValueError:
            d = None
        days.append(d)
        if d is not None and r.get("seconds"):
            known.append((d, r["seconds"]))
    if not days:
        return None, None
    typical = statistics.median([d for d in days if d is not None] or [0])
    years = sum(d if d is not None else typical for d in days) / 365.25
    sypd = sum(d for d, _ in known) / 365.25 / (sum(s for _, s in known) / 86400) if known else None
    return years, sypd


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


def _log_order(item) -> tuple:
    """Sort key for (path, log): model .err, .out, med.log, then PBS .o/.e logs, payu_postscript last; newest first."""
    name = os.path.basename(item[0])
    rank = 4 if name.startswith("payu_postscript") else next(
        (i for i, end in enumerate((".err", ".out", "med.log")) if name.endswith(end)), 3)
    return rank, -item[1]["mtime"]


def _block(title: str, text: str, kind: str = "note", lang: str = "text", opts: str = "", fence: str = "```") -> list:
    """A collapsible block holding text, highlighted as lang (opts: extra code-fence options; a longer fence lets the
    text contain ``` blocks of its own)."""
    return [f'??? {kind} "{title}"', f"    {fence} {lang} {opts}".rstrip(), "\n".join("    " + line for line in text.splitlines()),
            f"    {fence}", ""]


# Syntax highlighting for the run directory's configuration files (Fortran for namelist-style files)
LEXERS = {"config.yaml": "yaml", "MOM_input": "fortran", "ice_in": "fortran",
          "nuopc.runconfig": "ini", "nuopc.runseq": "ini"}


def _logs(logs: dict) -> list:
    """Collapsible blocks with the last lines of each log, titled with its full Gadi path and modification time."""
    md = []
    for path, log in sorted(logs.items(), key=_log_order):
        md += _block(f"{path} (last lines; modified {_when(log['mtime'])})", log["text"])
    return md


def description(entry, st: dict) -> tuple:
    """(description, True if it came from metadata.yaml): from experiments.yml, else the first line of
    the description in the run's metadata.yaml."""
    if isinstance(entry, dict) and entry.get("description"):
        return " ".join(str(entry["description"]).split()), False
    try:
        meta = yaml.safe_load(st.get("metadata") or "")
        text = str((meta if isinstance(meta, dict) else {}).get("description") or "")
    except yaml.YAMLError:
        text = ""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return (first if len(first) <= 200 else first[:197] + "…"), bool(first)


def failures(errors_log: Path) -> dict:
    """{notebook: error text} from mkfigs' mkfigs_errors.log (sections headed 'FAILED: <notebook>')."""
    out, nb = {}, None
    for line in errors_log.read_text().splitlines() if errors_log.exists() else []:
        if line.startswith("FAILED: "):
            nb = line[8:].strip()
            out[nb] = []
        elif nb and not set(line) <= {"="}:
            out[nb].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def failure_page(e: str, nb: str, error: str, ran: str, partial_md: str, has_notebook: bool) -> str:
    """Markdown for a notebook that failed: the key error lines, the full error, and anything it made first."""
    key = [l for l in error.splitlines() if re.search(r"Error|Exception|No such|Permission|direct cause|During handling", l)]
    md = [f"# {nb} ❌\n", f"This notebook **failed** in the latest run for **{e}** ({ran}).\n", "## Why it failed\n",
          "The lines below are picked out of the error; the first error listed is usually the cause.\n",
          "```text", "\n".join(key) or error[-2000:], "```", ""]
    md += _block("Full error (from mkfigs_errors.log)", error, kind="failure")
    if has_notebook:
        md.append("The [partly-run notebook](notebook.ipynb) shows every cell up to and including the one that failed.\n")
    if partial_md:
        md += ["## Figures made before the failure\n", partial_md.split("\n", 1)[-1]]  # drop mkmd's own heading
    return "\n".join(md)


TIPS = "https://access-om3-configs.access-hive.org.au/latest/Tips-and-tricks/#mom6-velocity-truncations"
RECIPE = "https://github.com/ACCESS-NRI/access-eval-recipes/blob/main/ocean/Examine_truncation_data.ipynb"


# A pin at the point, zoomed out to regional scale (zoom 5, as in a typical "@lat,lon,5z" Google Maps link)
MAPS = "https://www.google.com/maps/place/{lat:.2f},{lon:.2f}/@{lat:.2f},{lon:.2f},5z"


def _place(lon: float, lat: float) -> str:
    """'73.3°E, 67.9°N' linked to Google Maps."""
    lon = (lon + 180) % 360 - 180
    return f"[{abs(lon):.1f}°{'E' if lon >= 0 else 'W'}, {abs(lat):.1f}°{'N' if lat >= 0 else 'S'}]({MAPS.format(lat=lat, lon=lon)})"


def _spans(names: list) -> str:
    """['output001', 'output002', 'output005'] -> '001–002, 005'."""
    if not all(n[-3:].isdigit() for n in names):
        return ", ".join(names)  # e.g. 'work'
    nums, out = sorted(int(n[-3:]) for n in names), []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(f"{a:03d}" + (f"–{b:03d}" if b > a else "") for a, b in out)


def _truncation_text(st: dict, work: bool = False) -> list:
    """Markdown for 'Truncation locations': MOM6 velocity truncations and where they happen, either per
    archived output folder, or (work=True) for the run directory's work/ folder: the current or crashed run."""
    t = st.get("work_truncations" if work else "truncations") or {}
    segs = [s for s in t.get("segments", []) if s["truncs"] is not None]
    if work and not segs:
        return []  # no work/ocean.stats: nothing running, or not started yet
    md = ["### Truncation locations" + ("" if work else " for all completed run segments") + "\n"]
    if not segs:
        return md + ["No `ocean.stats` files found in the output folders.\n"]
    total, hit = sum(s["truncs"] for s in segs), [s for s in segs if s["truncs"]]
    if work and not total:
        return md + ["✅ No velocity truncations so far in the run segment currently underway (`work/ocean.stats`).\n"]
    if not total:
        return md + [f"✅ No velocity truncations: the Truncs column of `ocean.stats` is zero in all {len(segs)} output folders.\n"]
    runs = st.get("runs", [])
    periods = {f"output{int(r['run']):03d}": f"{_date(_start(r, runs), 7)} → {_date(r.get('model_finish'), 7)}"
               for r in runs if str(r.get("run")).isdigit() and r.get("model_finish")}
    where = ("so far in the run segment currently underway (the Truncs column of `work/ocean.stats`)" if work else
             f"in {len(hit)} of {len(segs)} output folders (the Truncs column of each `outputNNN/ocean.stats`)")
    md += [f"⚠️ MOM6 truncated velocities **{total:,}** times {where}. MOM6 truncates a velocity when its CFL number "
           "exceeds `CFL_TRUNCATE` (0.5 by default), and stops the run if more than `MAXTRUNC` truncations happen between "
           "energy saves (MOM6's default is 0, so any truncation stops it; the ACCESS-OM3 configurations set 10,000). "
           f"See [MOM6 velocity truncations]({TIPS}) for how to read the records, and "
           f"[Examine_truncation_data.ipynb]({RECIPE}) for mapping them.\n"]
    if not work:  # one row per output folder that had truncations
        md += ["*Location records* are the detailed records MOM6 also writes, one per truncation, to "
               "`U_velocity_truncations` (zonal velocity) and `V_velocity_truncations` (meridional velocity); the "
               "location table below is built from them. MOM6 writes at most 50 per PE per run, so there are usually "
               "fewer of them than truncations; – means no file of that kind.\n",
               "| Output folder | Model period | Truncations | Max CFL | Location records (U / V) | PEs at the record cap |",
               "|---|---|---:|---:|---|---|"]
    for s in ([] if work else hit):
        rec = " / ".join(str(s["records"].get(uv, "–")) for uv in "UV") if s["records"] else "–"
        if s["truncs"] and not s["records"]:
            rec = "⚠️ files unreadable" if s.get("unreadable") else "⚠️ no files"
        md.append(f"| {s['output']} | {periods.get(s['output'], '')} | {s['truncs']:,} | "
                  f"{'–' if s['max_cfl'] is None else format(s['max_cfl'], '.2f')} | {rec} | {', '.join(map(str, sorted(set(s['capped_pes'])))) or '–'} |")
    if not work and len(hit) < len(segs):
        md.append(f"\nThe other {len(segs) - len(hit)} output folders had no truncations.\n")
    no_files = [s for s in hit if not s["records"]]
    if no_files:
        which = ("the run segment currently underway" if work else
                 f"the only output folder with truncations ({_spans([s['output'] for s in no_files])})"
                 if len(hit) == 1 else
                 f"every output folder with truncations ({_spans([s['output'] for s in no_files])})"
                 if len(no_files) == len(hit) else
                 f"{len(no_files)} of the {len(hit)} output folders with truncations "
                 f"({_spans([s['output'] for s in no_files])})")
        md += ["", f"⚠️ `ocean.stats` reports truncations in {which}, but there are no readable "
               "`U_velocity_truncations` / `V_velocity_truncations` files there, so where those truncations happened "
               "can't be shown. MOM6 only writes these files when `U_TRUNC_FILE` and `V_TRUNC_FILE` are set in "
               "`MOM_input` (both default to empty); otherwise they may not have been copied to the archive, or "
               "they belong to someone else and can't be read.\n"]
    if not t.get("boxes"):
        return md
    md += ["", "**Where MOM6 truncates** (1° boxes, most records first; from the `U_velocity_truncations` and "
           "`V_velocity_truncations` files):\n",
           "| Location | Records | Grid cells | Seen in output | Velocity | Layers | Max speed (m/s) | Max CFL "
           "| Surface height (m) | Depth (m) |", "|---|---:|---:|---|---|---|---:|---:|---:|---:|"]
    for b in t.get("boxes", []):
        md.append(f"| {_place(b['lon'], b['lat'])} | {b['records']}{'*' if b['capped'] else ''} | {b['cells']} | "
                  f"{_spans(b['outputs'])} | {', '.join(b['uv'])} | {b['layers'][0]}–{b['layers'][1]} | "
                  f"{b['max_vel']:.1f} | {b['max_cfl']:.2f} | "
                  f"{'' if b['top_e'] is None else format(b['top_e'], '.2f')} | "
                  f"{'' if b['depth'] is None else format(b['depth'], '.0f')} |")
    md += ["", "\\* MOM6 writes at most 50 records per PE per run (`MAX_TRUNC_FILE_SIZE_PER_PE`), and this box's PE hit "
           "that cap in at least one run, so it truncated more often than the record count shows. Surface height is "
           "the top-interface height `e`, shown only for records that include layer 1; depth is the shallower of the "
           "two cells (`D`).\n"]
    return md


def _issue_link(url: str) -> str:
    """'[owner/repo#123](url)' for a GitHub issue or pull request, else the link itself."""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/(?:issues|pull)/(\d+)", url or "")
    return f"[{m[1]}#{m[2]}]({url})" if m else f"<{url}>"


def status_page(e: str, st: dict, nb_links: str, sypd_png: bool) -> str:
    """Markdown for an experiment's run-status page, from gadi/run_status.py's snapshot."""
    if st.get("no_snapshot"):  # nothing was read from Gadi: say so, rather than showing zeros
        return "\n".join([f"# {e}\n"] + ([f"{st['description']}\n"] if st.get("description") else []) + [
            "⚠️ **No status snapshot for this experiment.** The nightly job couldn't read this run's records on "
            f"Gadi this time (archive folder `{st['no_snapshot']}`), so its run status, logs, configuration and "
            "truncation sections are missing. They will appear after the next nightly run that reads them; if "
            "this persists, check the folder exists and is readable, and see the Submit step's log.\n",
            f"**Notebooks:** {nb_links or 'none yet'}\n"])
    runs, archive, run_dir = st.get("runs", []), st.get("archive", "?"), st.get("run_dir")
    done = [r for r in runs if r.get("model_finish")]
    emoji, sentence = health(st)
    years, _ = _progress(runs)
    reached = (f" **Model date reached:** {_date(done[-1]['model_finish'])}"
               + (f" ({years:.1f} model years complete)." if years else ".")) if done else ""
    md = [f"# {e}\n"] + ([f"{st['description']}\n"] if st.get("description") else []) + [
          f"{emoji} **Latest submission:** {sentence}{reached}\n",
          f"**Notebooks:** {nb_links or 'none yet'}\n"]
    if st.get("issue"):
        md.append(f"**GitHub issue:** {_issue_link(st['issue'])}\n")
    if st.get("payu_status"):
        md += _block("payu status (run in the control directory)", st["payu_status"], kind="info")
    if run_dir:
        md += ["## Run directory: current or crashed run\n", f"Read from the payu control directory `{run_dir}`.\n"]
        med = next((v["text"] for k, v in st["run_logs"].items() if k.endswith("work/log/med.log")), "")
        dates = [line for line in med.splitlines() if "Model Date:" in line]
        if dates:
            md.append(f"**Latest model date in `work/log/med.log`:** {dates[-1].split('Model Date:')[1].split()[0]}"
                      f" (rate {dates[-1].split('rate =')[-1].strip()})\n")
        md += ["### Log files\n"] + (_logs(st["run_logs"]) or ["No logs found there.\n"])
        md += _truncation_text(st, work=True)
        if st.get("configs"):
            md.append("### Configuration files\n")
            for path, cfg in st["configs"].items():
                md += _block(f"{path} (modified {_when(cfg['mtime'])})", cfg["text"], kind="info",
                             lang=LEXERS.get(os.path.basename(path), "text"), opts='linenums="1"')
    md += ["## Archive folder: completed runs\n", f"Read from the payu archive folder `{archive}`.\n",
           f"- **Output folders:** {st.get('outputs', 0)} (latest `{st.get('latest_output')}`)",
           f"- **Model date reached:** {_date(done[-1]['model_finish']) if done else 'unknown'}",
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
            period = f"{_date(_start(r, runs))} → {_date(r.get('model_finish'))}"
            hours = f"{r['seconds'] / 3600:.1f}" if r.get("seconds") else ""
            md.append(f"| {r.get('run')} | {r.get('job_id') or ''} | {_result(r)} | {period} | {hours} | {_sypd(r)} | {_when(r.get('mtime'))} |")
        md.append("")
    if st.get("logs"):
        md += ["### Latest logs in the archive folder\n"] + _logs(st["logs"])
    if st.get("metadata"):
        md += _block(f"{archive}/metadata.yaml", st["metadata"], kind="info", lang="yaml")
    md += _truncation_text(st)
    return "\n".join(md)


def _snapshot(path: Path) -> dict:
    """An experiment's run_status.py snapshot, or {} if it's missing, empty or unreadable."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        if path.exists():
            print(f"::warning::{path.name}: status snapshot is empty or unreadable; showing the page without it")
        return {}


def experiment_pages(e: str, entry, notebooks: list, docs: Path) -> tuple:
    """Write one experiment's notebook, failure and status pages; return (its menu entry, its table row)."""
    src, pages, ok = CONTENT / e, [], []
    errors, log = src / "mkmd" / "mkfigs_errors.log", src / "mkmd" / "mkfigs_run.log"
    failed = failures(errors)
    ran = (datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).astimezone(ZoneInfo("Australia/Canberra"))
           .strftime("%Y-%m-%d %H:%M %Z") if log.exists() else "not run yet")
    job = _snapshot(STATUS / f"{e}.job.json")  # written by submit.sh
    job_note = (f"🏃 Notebooks are being re-run now (PBS job {job['running']}); results below are from the "
                "previous complete run." if job.get("running") else
                f"⏱ The last notebook job ({job['unfinished']}) didn't finish (out of walltime or memory, or "
                "deleted); results below are from the previous complete run." if job.get("unfinished") else "")
    ran += {"🏃": "<br>🏃 re-running now", "⏱": "<br>⏱ last job didn't finish"}.get(job_note[:1], "")
    for nb in notebooks:
        md, ipynb = src / "mkmd" / f"{nb}.md", src / f"{nb}_rendered.ipynb"
        if not ((md.exists() and ipynb.exists()) or nb in failed):
            continue
        dst = docs / "live" / e / nb
        dst.mkdir(parents=True)
        text = md.read_text().replace(f"/assets/experiments/{e}/", "") if md.exists() else ""  # figures sit beside the page
        text = text.replace(f"notebooks/{nb}.ipynb", "notebook.ipynb")
        for png in (src / "mkmd").glob(f"{nb}_[0-9]*.png"):
            shutil.copy2(png, dst)
        if ipynb.exists() and not _valid_json(ipynb):  # e.g. a notebook cut short: don't let it break mkdocs
            print(f"::warning::{e}: {ipynb.name} is not valid JSON; not shown")
            ipynb = Path("/nonexistent")
        if ipynb.exists():
            shutil.copy2(ipynb, dst / "notebook.ipynb")
        if nb in failed:
            print(f"::warning::{e}: notebook {nb} failed")
            (dst / "index.md").write_text(failure_page(e, nb, failed[nb], ran, text, ipynb.exists()))
            sub = [{"Notebook (partly run)": f"live/{e}/{nb}/notebook.ipynb"}] if ipynb.exists() else []
            pages.append({f"{nb} ❌": [f"live/{e}/{nb}/index.md", *sub]})
        else:
            (dst / "index.md").write_text(text)
            ok.append(nb)
            sub = [{"Notebook": f"live/{e}/{nb}/notebook.ipynb"}] if ipynb.exists() else []
            pages.append({nb: [f"live/{e}/{nb}/index.md", *sub]})
    shown = "<br>".join(f"[{nb}]({e}/{nb}/index.md)" for nb in ok)
    broke = "<br>".join(f"[{nb}]({e}/{nb}/index.md)" for nb in failed)
    links = ", ".join([f"[{nb}]({nb}/index.md)" for nb in ok] + [f"❌ [{nb}]({nb}/index.md)" for nb in failed])
    st = _snapshot(STATUS / f"{e}.json")
    if not st:
        st["no_snapshot"] = entry["archive"] if isinstance(entry, dict) else entry  # as listed in experiments.yml
    st["description"], from_metadata = description(entry, st)
    st["issue"] = entry.get("issue") if isinstance(entry, dict) else None  # where the run is discussed on GitHub
    if from_metadata:  # say so, so it isn't mistaken for a description written for this site
        st["description"] += " *(from the run's metadata.yaml)*"
    (docs / "live" / e).mkdir(parents=True, exist_ok=True)
    if (src / "sypd.png").exists():
        shutil.copy2(src / "sypd.png", docs / "live" / e)
    (docs / "live" / e / "index.md").write_text(status_page(e, st, links + (f"\n\n{job_note}" if job_note else ""),
                                                            (src / "sypd.png").exists()))
    done = [r for r in st.get("runs", []) if r.get("model_finish")]
    latest = max((r.get("mtime") or 0 for r in st.get("runs", [])), default=0)  # when payu last recorded a run
    years, avg = _progress(st.get("runs", []))
    progress = (f" ({years:.0f} years complete" if years and years >= 10 else f" ({years:.1f} years complete" if years else "") + (
        f", avg {avg:.1f} SYPD" if years and avg else "") + (")" if years else "")
    reached = ("⚠️ no status snapshot" if st.get("no_snapshot") else
               f"{health(st)[0]} {_date(done[-1]['model_finish']) if done else ''}{progress}<br>{_when(latest)}"
               if st.get("runs") else "–")
    issue = f"[GitHub issue]({st['issue']})" if st.get("issue") else ""
    described = " – ".join(x for x in (st["description"], issue) if x) or "–"
    return ({e: [f"live/{e}/index.md", *pages]},
            f"| [{e}]({e}/index.md) | {described} | {reached} | {ran} | {shown or '–'} | {broke or '–'} |",
            latest)


def _valid_json(path: Path) -> bool:
    """True if path holds valid JSON (a rendered notebook that was written completely)."""
    try:
        json.loads(path.read_text())
        return True
    except (OSError, ValueError):
        return False


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
    shutil.copy2(ROOT / "pages" / "wide.css", docs / "css")

    # Evaluation Summary: access-om3-configs' Experiments.md, with site-relative links sent back there.
    (docs / "index.md").write_text(Path(summary_md).read_text().replace("](/", f"]({CONFIGS_SITE}"))

    live_nav, rows = ["live/index.md"], []
    listed = []  # (latest payu record time, menu entry, table row) per experiment
    for entry in cfg["experiments"]:
        path = entry["archive"] if isinstance(entry, dict) else entry
        notebooks = (entry.get("notebooks") if isinstance(entry, dict) else None) or cfg["notebooks"]  # own list replaces
        e = ename(path)
        if e == "archive":  # submit.sh skips these: the link's target folder must be listed instead
            listed.append((-1, None, f"| `{path}` | | ⚠️ not monitored: list the folder this `archive` link points to | | | |"))
            continue
        try:
            nav, row, latest = experiment_pages(e, entry, notebooks, docs)
        except Exception as err:  # one bad experiment must not take the whole site down
            print(f"::error::{e}: its pages could not be built: {err!r}")
            traceback.print_exc()
            shutil.rmtree(docs / "live" / e, ignore_errors=True)
            (docs / "live" / e).mkdir(parents=True)
            (docs / "live" / e / "index.md").write_text(
                f"# {e}\n\n⚠️ This experiment's pages could not be built:\n\n```text\n{traceback.format_exc()}```\n")
            nav, latest = {e: [f"live/{e}/index.md"]}, 0
            row = f"| [{e}]({e}/index.md) | | ⚠️ pages could not be built (see its page) | | | |"
        listed.append((latest, nav, row))
    # Most recently active run first (latest payu record), in the table and the menu
    for latest, nav, row in sorted(listed, key=lambda x: -x[0]):
        rows.append(row)
        if nav:
            live_nav.append(nav)

    (docs / "live").mkdir(exist_ok=True)
    names = [ename(x["archive"] if isinstance(x, dict) else x) for x in cfg["experiments"]]
    (docs / "live" / "index.md").write_text(
        # Hide the empty "On this page" column so the table gets the room (as the Experiments page does).
        "<style>.md-sidebar--secondary:not([hidden]) { display: none }</style>\n\n"
        "# OM3 Monitor\n\nFollows ACCESS-OM3 runs while they are running: each night it re-runs a few evaluation notebooks "
        "on each run's latest output and builds a status page from its payu records and logs. Pages are replaced "
        "nightly; nothing is kept.\n\n"
        "Experiments are listed by their latest activity with newest first. An experiment's notebooks are only re-run "
        "when it has new output. Click an experiment for its run status. Times are Canberra time.\n\n"
        "| Experiment | Description | Latest submission (model date reached)<br>last payu record ▾ | Notebooks last run "
        "| Notebooks shown "
        "| Failed |\n|---|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n")

    if os.environ.get("GITHUB_STEP_SUMMARY"):  # shown at the top of the workflow run's page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(run_summary([n for n in names if n != "archive"]))
    nav = [{"Evaluation Summary": "index.md"}, {"OM3 Monitor": live_nav}]
    (BUILD / "mkdocs.yml").write_text(yaml.safe_dump({"INHERIT": "../mkdocs.yml", "nav": nav}, sort_keys=False))


if __name__ == "__main__":
    main(sys.argv[1])
