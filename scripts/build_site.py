"""Assemble build/ (docs tree + nav) from Gadi outputs in content/, theme/, pages/ and Experiments.md."""
import os
import re
import shutil
import tempfile
import sys
import textwrap
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

import json

from urllib.parse import quote, urlencode

import markdown
import yaml

import checks
import config_diff_data

ROOT = Path(__file__).resolve().parent.parent
CONTENT, THEME, BUILD, STATUS = ROOT / "content", ROOT / "theme" / "documentation", ROOT / "build", ROOT / "status"
CONFIGS_SITE = "https://access-om3-configs.access-hive.org.au/latest/"
CANBERRA = ZoneInfo("Australia/Canberra")
REPO = os.environ.get("GITHUB_REPOSITORY", "ACCESS-NRI/access-om3-live")  # set by GitHub Actions
# A new GitHub issue with a short template filled in (pages/contributing.md links to it)
ISSUE_URL = f"https://github.com/{REPO}/issues/new?" + urlencode({
    "title": "Feedback: ",
    "body": "**Page** (paste its link):\n\n**Experiment** (if it's about a run):\n\n"
            "**What's wrong, or what would help?**\n\n"})


def ename(path: str) -> str:
    """An experiment's name: its archive folder's name; for a datastore given directly (.json, e.g. ACCESS-CM3), the
    nearest folder above it named differently from the file (.../cm3-run-X/cm3-datastore/cm3-datastore.json -> cm3-run-X)."""
    p = Path(path)
    if p.suffix != ".json":
        return p.name
    return next((parent.name for parent in p.parents if parent.name and parent.name != p.stem), p.stem)


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


def monitor_plot(series: dict, out: Path) -> bool:
    """Draw each experiment's SYPD for every completed run against the model year it reached; False if there is nothing to draw."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("::warning::matplotlib is not installed, so the throughput plot is left out")
        return False
    fig, ax = plt.subplots(figsize=(10, 4.2), facecolor="white")
    for name, runs in series.items():
        pts = []
        for r in runs:
            if r.get("status") == 0 and r.get("model_finish") and _sypd(r):
                d = datetime.fromisoformat(r["model_finish"])
                pts.append((d.year + (d.timetuple().tm_yday - 1) / 365.25, float(_sypd(r))))
        if pts:
            ax.plot(*zip(*sorted(pts)), marker="o", ms=3, lw=1.2, label=name)
    if not ax.lines:
        plt.close(fig)
        return False
    ax.set(xlabel="model year reached", ylabel="simulated years per day (SYPD)")
    ax.set_ylim(bottom=0)
    ax.grid(alpha=0.3)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=8, frameon=False)
    fig.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return True


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


def _check(title: str, code: str, where: str = "on Gadi") -> list:
    """A collapsed block with a snippet to re-check a statement (the code block gets a copy button)."""
    return _block(f"Check this {where}: {title}", code, kind="tip", lang="bash")


# Syntax highlighting for the run directory's configuration files (Fortran for namelist-style files)
LEXERS = {"config.yaml": "yaml", "MOM_input": "fortran", "ice_in": "fortran",
          "nuopc.runconfig": "ini", "nuopc.runseq": "ini"}


def _paths(logs: dict) -> list:
    """The log paths, in the order the page shows them."""
    return [path for path, _ in sorted(logs.items(), key=_log_order)]


def _logs(logs: dict) -> list:
    """Collapsible blocks with the last lines of each log, titled with its full Gadi path and modification time,
    and a check of those times and lines."""
    md = []
    for path, log in sorted(logs.items(), key=_log_order):
        md += _block(f"{path} (last lines; modified {_when(log['mtime'])})", log["text"])
    return md + _check("when these logs were written, and their last lines", checks.log_files(_paths(logs))) if md else md


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


def truncation_section(st: dict, work: bool = False) -> list:
    """'Truncation locations' with a check for it, if there is anything to show."""
    md = _truncation_text(st, work)
    t = st.get("work_truncations" if work else "truncations") or {}
    example = next((f'"$R"/work/{uv}_velocity_truncations' if work else f'"$A"/{s["output"]}/{uv}_velocity_truncations'
                    for s in t.get("segments", []) for uv, n in s["records"].items() if n), None)
    return md + _check("the truncation counts, locations and settings",
                       checks.truncations(st.get("archive"), st.get("run_dir"), work, example)) if md else md


def _truncation_text(st: dict, work: bool = False) -> list:
    """Markdown for 'Truncation locations': MOM6 velocity truncations and where they happen, either per
    archived output folder, or (work=True) for the run directory's work/ folder: the current or crashed run."""
    t = st.get("work_truncations" if work else "truncations") or {}
    segs = [s for s in t.get("segments", []) if s["truncs"] is not None]
    if work and not segs:
        return []  # no work/ocean.stats: nothing running, or not started yet
    md = ["### Truncation locations\n"]
    if not segs:
        return md + ["No `ocean.stats` files found in the output folders.\n"]
    total, hit = sum(s["truncs"] for s in segs), [s for s in segs if s["truncs"]]
    if work and not total:
        return md + ["✅ No velocity truncations so far in this run (`work/ocean.stats`).\n"]
    if not total:
        return md + [f"✅ No velocity truncations: the Truncs column of `ocean.stats` is zero in all {len(segs)} output folders.\n"]
    runs = st.get("runs", [])
    periods = {f"output{int(r['run']):03d}": f"{_date(_start(r, runs), 7)} → {_date(r.get('model_finish'), 7)}"
               for r in runs if str(r.get("run")).isdigit() and r.get("model_finish")}
    where = ("so far in this run (the Truncs column of `work/ocean.stats`)" if work else
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
        which = ("this run" if work else
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


# Settings changes: gadi/run_status.py lists every edit to the tracked files; this sorts them into the build,
# model settings and housekeeping (paths, diagnostic output, job setup), and draws the model ones.
sys.path.insert(0, str(ROOT / "gadi"))
from run_status import BUILD_KEYS  # noqa: E402  the config.yaml entries that say which model build ran

GROUPS = {  # group: (title, files, colour)
    "Build": ("Build (model version)", "config.yaml", "#8e6bbf"),
    "Run length": ("Run length", "nuopc.runconfig: stop_n, restart_n", "#a87a12"),
    "Inputs": ("Input files", "config.yaml: input", "#c2689b"),
    "Ocean": ("Ocean (MOM6)", "MOM_input, MOM_override", "#3b82c4"),
    "Coupler": ("Coupler (CMEPS)", "nuopc.runconfig", "#e0701a"),
    "Sea ice": ("Sea ice (CICE)", "ice_in", "#2a9d8f"),
}
RUN_LENGTH = {"stop_n", "stop_option", "restart_n", "restart_option"}  # nuopc.runconfig: model time per run, restart interval


def _group_of(c: dict) -> str:
    """The part of the model an edit belongs to; config.yaml's input files and the run length are their own groups."""
    if c["file"] == "nuopc.runconfig" and c["key"].rsplit(".", 1)[-1] in RUN_LENGTH:
        return "Run length"
    return "Inputs" if c["file"] == "config.yaml" and c["key"].startswith("input/") else GROUP_OF[c["file"]]


GROUP_OF = {"config.yaml": "Build", "MOM_input": "Ocean", "MOM_override": "Ocean", "nuopc.runconfig": "Coupler",
            "ice_in": "Sea ice"}
HOUSEKEEPING = {  # per file: settings (by name, without section) that are paths, parallel layout or output choices
    "MOM_input": r"_FILE$|_DIR$|^INPUTDIR$|LAYOUT|MASKTABLE|^NONBLOCKING_UPDATES$|^DEBUG|^ENERGYSAVEDAYS$",
    "nuopc.runconfig": r"^history_|_ntasks$|_nthreads$|_rootpe$|_pestride$|mesh|^pio_|diro$|logdir$|^restart_|^stop_|^start_",
    "ice_in": r"^f_|^hist|^dumpfreq|^restart|_file$|^block_size_|^max_blocks$|^distribution_|^processor_shape$"
              r"|^diagfreq$|^debug_|^print_|^pointer_file$",
}
TIMELINE_MAX_SETTINGS = 15  # more changed settings than this: a grid of runs instead of one row per setting


def _local(ts: str) -> datetime:
    """A git timestamp as Canberra time."""
    return datetime.fromisoformat(ts).astimezone(CANBERRA).replace(tzinfo=None)


def _housekeeping(c: dict) -> bool:
    """True for edits that are not model settings: job setup, paths, layout, diagnostic output."""
    if _group_of(c) == "Run length":  # sets how model years map onto runs, so it is charted
        return False
    if c["file"] == "config.yaml":
        return c["key"] not in BUILD_KEYS and not c["key"].startswith("input/")
    pattern = HOUSEKEEPING.get("MOM_input" if c["file"] == "MOM_override" else c["file"])
    return bool(pattern and re.search(pattern, c["key"].rsplit(".", 1)[-1]))


def _change(c: dict) -> str:
    """'old → new'; for module lists only the entries that changed; for input files the folders that differ."""
    if c["key"].startswith("input/"):
        return _input_diff(c["old"], c["new"])
    if c["key"].startswith("modules."):
        old, new = ([x for x in (c[k] or "").split(", ") if x] for k in ("old", "new"))
        return ", ".join(["-" + x for x in old if x not in new] + ["+" + x for x in new if x not in old]) or "reordered"
    return " → ".join("(not set)" if c[k] is None else c[k] for k in ("old", "new"))


def _short(key: str, old, new) -> str:
    """A setting's value at the first run → now, for its chart row; for module lists, just the versions that changed."""
    if key.startswith("input/"):
        return _input_diff(old, new)
    if key.startswith("modules."):
        was, now = ([x for x in (v or "").split(", ") if x] for v in (old, new))
        gone, added = [x for x in was if x not in now], [x for x in now if x not in was]
        bumps = [(a, b) for a in gone for b in added if a.rpartition("/")[0] == b.rpartition("/")[0]]  # same module, new version
        if bumps:
            return "; ".join(f"{a.rpartition('/')[0]}: {a.rpartition('/')[2]} → {b.rpartition('/')[2]}" for a, b in bumps)
        return " ".join(["-" + x for x in gone] + ["+" + x for x in added]) or "reordered"
    plain = lambda v: "(not set)" if v is None else v
    if (old or "").strip().strip("\"'").lower() == (new or "").strip().strip("\"'").lower():
        return f"{plain(old)} (back to the start)"
    return f"{plain(old)} → {plain(new)}"


def _component(c: dict) -> str:
    """Which part of the model an edit belongs to (config.yaml entries other than the build are job setup)."""
    return "Job setup" if c["file"] == "config.yaml" and _housekeeping(c) else GROUPS[c["group"]][0]


def _model_date(payu: list, counter) -> str:
    """The model date payu reached at the end of run `counter`, e.g. '1963-01-01', or '?' if it wasn't recorded."""
    done = [r for r in payu if str(r.get("run")) == str(counter) and r.get("model_finish")]
    return _date(done[-1]["model_finish"]) if done else "?"


def _year(payu: list, counter) -> str:
    """The model year simulated in payu run `counter`, e.g. '1963', or '' if it wasn't recorded."""
    rec = [r for r in payu if str(r.get("run")) == str(counter) and (r.get("model_start") or r.get("model_finish"))]
    if not rec:
        return ""
    return str(rec[-1]["model_start"])[:4] if rec[-1].get("model_start") else str(int(str(rec[-1]["model_finish"])[:4]) - 1)


_CACHE = {}
_INDEX = {}
CONFIGS_REPO = "ACCESS-NRI/access-om3-configs"
EXPERIMENTS_REPO = "ACCESS-Community-Hub/access-om3-experiments"  # one branch per experiment, named like its archive folder


def _get(url: str, headers: dict = None) -> str:
    """The text at a URL, or '' if it can't be fetched (offline, rate-limited, not found); cached for the build."""
    if url not in _CACHE:
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "access-om3-live"})
            _CACHE[url] = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace")
        except (OSError, ValueError):
            _CACHE[url] = ""
    return _CACHE[url]


def _lines_of(repo: str, ref: str, path: str) -> dict:
    """{name: first line number} for the 'NAME = value' lines (or, in config.yaml, the input paths) of a file on GitHub."""
    key = (repo, ref, path)
    if key not in _INDEX:
        out = {}
        for n, line in enumerate(_get(f"https://raw.githubusercontent.com/{repo}/{ref}/{path}").splitlines(), 1):
            m = re.match(r"\s*([A-Za-z_][\w%.]*)\s*[=:]", line) or re.search(r"/([^/\s]+)\s*$", line)
            if m:
                out.setdefault(m[1], n)
        _INDEX[key] = out
    return _INDEX[key]


def _param_link(c: dict, e: str = ""):
    """A link to the line that documents or sets a setting, or None if there is none. MOM6 settings go to the documented
    MOM_input of access-om3-configs; the rest go to the experiment's own branch in access-om3-experiments, if it has one."""
    ref = urllib.parse.quote(e, safe="")
    if c["group"] == "Ocean":
        name, where = c["key"], [(CONFIGS_REPO, "main", "doc/MOM_input"), (CONFIGS_REPO, "main", "doc/MOM_parameter_doc.all")]
    elif c["group"] == "Inputs":
        name, where = c["key"][6:].split("#")[0], [(EXPERIMENTS_REPO, ref, "config.yaml")]
    elif c["file"] in ("nuopc.runconfig", "ice_in"):
        name, where = c["key"].rsplit(".", 1)[-1], [(EXPERIMENTS_REPO, ref, c["file"])]
    else:
        return None
    for repo, r, f in where:
        if repo == EXPERIMENTS_REPO and not e:
            continue  # no experiment name, so no branch to look in
        n = _lines_of(repo, r, f).get(name)
        if n:
            return f"https://github.com/{repo}/blob/{r}/{f}#L{n}"
    return None


def _prerelease_link(pr: str, number: str) -> str:
    """The comment on the access-om3 pull request that announces a pre-release build (access-om3/pr264-11), else the PR."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "access-om3-live"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for page in range(1, 6):
        try:
            comments = json.loads(_get(f"https://api.github.com/repos/ACCESS-NRI/access-om3/issues/{pr}/comments"
                                       f"?per_page=100&page={page}", headers) or "[]")
        except ValueError:
            break
        if not isinstance(comments, list) or not comments:
            break
        for c in comments:
            if re.search(rf"pr{pr}-{number}\b", c.get("body") or ""):
                return c["html_url"]
    return f"https://github.com/ACCESS-NRI/access-om3/pull/{pr}"


def _module_link(entry: str):
    """Where a loaded module comes from: its release's spack.yaml, or its pre-release's announcement; None for others."""
    name, _, version = entry.partition("/")
    if name != "access-om3":
        return None
    pre = re.fullmatch(r"pr(\d+)-(\d+)", version)
    if pre:
        return _prerelease_link(pre[1], pre[2])
    if _get(f"https://raw.githubusercontent.com/ACCESS-NRI/ACCESS-OM3/{version}/spack.yaml"):  # only a real release has one
        return f"https://github.com/ACCESS-NRI/ACCESS-OM3/blob/{version}/spack.yaml"
    return None


def _input_diff(old, new) -> str:
    """An input file's change: the folders that differ ('2026.03.16 → 2026.06.11'), or 'added …' / 'removed …'."""
    if old is None or new is None:
        path = new if old is None else old
        return ("added " if old is None else "removed ") + "/".join(path.rstrip("/").split("/")[-3:])
    a, b = old.rstrip("/").split("/"), new.rstrip("/").split("/")
    if len(a) == len(b) and a != b:
        return " / ".join(x for x, y in zip(a, b) if x != y) + " → " + " / ".join(y for x, y in zip(a, b) if x != y)
    return f"{old} → {new}"


def _change_lines(c: dict) -> list:
    """The change as separate lines: one per module added or removed, otherwise 'old → new'."""
    return _change(c).split(", ") if c["key"].startswith("modules.") else [_change(c)]


def _tip_lines(c: dict, label: str) -> list:
    """The lines of a dot's tooltip: what changed, when and why, each on its own line."""
    lines = [f"#{c['n'] + 1}  {label}  ({c['group']})", *_change_lines(c),
             f"{c['t']:%-d %b %Y %H:%M}  ·  after run {c['counter']}", f"model date {c['model_date']}",
             *textwrap.wrap(c["subject"], 50)[:3]]
    return [x if len(x) <= 60 else x[:59] + "…" for x in lines]


def _table(changes: list) -> list:
    """HTML table of the edits; each row can be linked to from the chart (#chg-N)."""
    rows = []
    for c in changes:
        now = " <small>(current run)</small>" if c["current"] else ""
        section, _, name = c["key"].rpartition(".")  # nuopc.runconfig and ice_in keys are 'SECTION.name'
        if c["group"] == "Build":
            section, name = "", c["key"]
        elif c["group"] == "Inputs":
            section, name = "", c["key"][6:]
        where = c["group"] + (f" · {section}" if section else "")
        code = f"<code>{escape(name)}</code>"
        if c.get("link"):
            code = f'<a href="{escape(c["link"])}" target="_blank" rel="noopener">{code}</a>'
        change = "<br>".join(f"<code>{escape(x)}</code>" for x in _change_lines(c))
        rows.append(f"<tr id=\"chg-{c['n']}\"><td>{c['n'] + 1}</td><td>{c['t']:%-d %b %H:%M}</td>"
                    f"<td>{c['counter']}{now}<br><small>{c['model_date']}</small></td>"
                    f"<td><small>{escape(where)}</small><br>{code}</td><td>{change}</td>"
                    f"<td>{escape(c['subject'])} <code>{c['sha']}</code></td></tr>")
    return ['<div class="settings-table">', "<table><colgroup><col style='width:5%'><col style='width:11%'><col style='width:12%'>"
            "<col style='width:24%'><col style='width:22%'><col style='width:26%'></colgroup><thead><tr><th>#</th><th>When</th>"
            "<th>After run (model date)</th><th>Setting</th><th>Change</th><th>Commit</th></tr></thead><tbody>",
            *rows, "</tbody></table>", "</div>"]


def _md_table(changes: list) -> list:
    """The same table as Markdown, for inside the collapsed housekeeping block."""
    cell = lambda v: escape(str(v)).replace("|", "\\|").replace("`", "'")
    return ["| When | After run | Component | Setting | Change | Commit |", "|---|---|---|---|---|---|"] + [
        f"| {c['t']:%-d %b %H:%M} | {c['counter']} | {_component(c)} | `{cell(c['key'])}` | `{cell(_change(c))}` | "
        f"{cell(c['subject'])} `{c['sha']}` |" for c in changes]


def _ticks(t0: datetime, t1: datetime) -> list:
    """Day-aligned axis ticks, at most about nine."""
    days = (t1 - t0).total_seconds() / 86400
    step = next((d for d in (1, 2, 7, 14, 28, 56, 112) if days / d <= 9), 224)
    t, out = datetime(t0.year, t0.month, t0.day), []
    while t <= t1:
        out += [t] if t >= t0 else []
        t += timedelta(days=step)
    return out


def _label(key: str, group: str, keys: list) -> str:
    """A setting's row label: its name, with the file section only if two rows would otherwise match."""
    short = key[6:] if group == "Inputs" else (key.rsplit(".", 1)[-1] if group != "Build" else key)
    return short if sum(k.rsplit(".", 1)[-1] == short for k in keys) < 2 else key


def _svg(width: int, height: int, parts: list, label: str, cls: str = "wide") -> str:
    """One inline SVG block (no blank lines, so Markdown leaves it alone)."""
    return (f'<div class="settings-chart {cls}"><svg viewBox="0 0 {width} {height}" fill="currentColor" role="img" aria-label="{escape(label)}">'
            + "\n".join(parts) + "</svg></div>")


def _timeline(shown: list, runs: list, restarts: list, t0: datetime, t1: datetime, boundary: datetime,
              current: bool, years: list, width: int = 920, cls: str = "wide has-x") -> str:
    """One row per changed setting: numbered dots at each edit, a bar while it differed from its value at the first run."""
    W, X0 = width, 245  # the plot gets whatever width is left between the names and the 'first run → now' column
    X1, XN = W - 280, W - 265
    span = (t1 - t0).total_seconds()
    x = lambda t: X0 + (t - t0).total_seconds() / span * (X1 - X0)
    norm = lambda v: None if v is None else v.strip().strip("\"'").lower()  # light formatting-only differences
    groups, y = [], 104
    for g, (title, files, colour) in GROUPS.items():
        keys = list(dict.fromkeys(c["key"] for c in shown if c["group"] == g))
        groups.append((g, title, files, colour, keys, y))
        y += (34 + 34 * len(keys) if keys else 52) + 12
    bottom, bx, parts, tips = y, x(boundary), [], []
    if current:  # the current or crashed run: everything right of the last completed run
        parts.append(f'<rect x="{bx:.1f}" y="30" width="{X1 - bx:.1f}" height="{bottom - 42}" fill="currentColor" opacity=".07"/>')
    for t in _ticks(t0, t1):
        parts.append(f'<line x1="{x(t):.1f}" y1="24" x2="{x(t):.1f}" y2="{bottom - 12}" stroke="currentColor" opacity=".12"/>')
        parts.append(f'<text x="{x(t) + 4:.1f}" y="16" font-size="12" opacity=".7">{t:%-d %b}</text>')
    parts.append(f'<text x="0" y="45" font-size="13" opacity=".7">Completed runs ({len(runs)})</text>')
    parts += [f'<line x1="{x(t):.1f}" y1="32" x2="{x(t):.1f}" y2="48" stroke="currentColor" opacity=".45"/>'
              for t, _ in runs[::max(1, len(runs) // 400)]]  # one tick per completed run, thinned for very long runs
    parts.append('<text x="0" y="66" font-size="13" opacity=".7">Model year</text>')
    last = -99.0
    for t, yr in years:  # a year label only where there is room for it
        if x(t) - last >= 40:
            parts.append(f'<text x="{x(t):.1f}" y="66" font-size="11" opacity=".85" text-anchor="middle">{yr}</text>')
            last = x(t)
    parts += [f'<line x1="{x(t):.1f}" y1="32" x2="{x(t):.1f}" y2="{bottom - 12}" stroke="currentColor" opacity=".4" '
              'stroke-dasharray="3 4"/>' for t in restarts]
    parts.append(f'<line x1="{bx:.1f}" y1="30" x2="{bx:.1f}" y2="{bottom - 12}" stroke="currentColor" opacity=".8" '
                 'stroke-width="1.5" stroke-dasharray="7 4"/>')
    parts.append(f'<text x="{bx - 6:.1f}" y="88" font-size="12" text-anchor="end" opacity=".85">◂ archive: completed runs</text>')
    if current:
        parts.append(f'<text x="{bx + 6:.1f}" y="88" font-size="12" opacity=".85">current or crashed run ▸</text>')
    parts.append(f'<text x="{XN}" y="66" font-size="12" opacity=".7">first run → now</text>')
    for g, title, files, colour, keys, top in groups:
        parts.append(f'<text x="0" y="{top + 16}" font-size="14" font-weight="600" fill="{colour}">{title} '
                     f'<tspan class="mono" font-weight="400" font-size="12" opacity=".7" dx="6">{files}</tspan></text>')
        if not keys:
            parts.append(f'<text x="0" y="{top + 48}" font-size="13" opacity=".7">no changes</text>')
        for r, key in enumerate(keys):
            yc = top + 34 + 17 + 34 * r
            es = [c for c in shown if c["group"] == g and c["key"] == key]
            label = _label(key, g, keys)
            short = label if len(label) <= 28 else label[:27] + "…"
            text = f'<text class="mono" x="0" y="{yc + 4}" font-size="13">{escape(short)}<title>{escape(label)}</title></text>'
            link = next((c["link"] for c in es if c.get("link")), None)  # the setting's name links to where it is documented
            parts.append(f'<a href="{escape(link)}" target="_blank" rel="noopener">{text}</a>' if link else text)
            parts.append(f'<line x1="{X0}" y1="{yc}" x2="{X1}" y2="{yc}" stroke="currentColor" opacity=".15"/>')
            first, start, cur = norm(es[0]["old"]), None, norm(es[0]["old"])
            for c in es:
                new = norm(c["new"])
                if cur == first and new != first:
                    start = c["t"]
                if cur != first and new == first:
                    parts.append(f'<rect x="{x(start):.1f}" y="{yc - 5}" width="{max(x(c["t"]) - x(start), 2):.1f}" '
                                 f'height="10" rx="2" fill="{colour}" opacity=".25"/>')
                    start = None
                cur = new
            if start is not None:
                parts.append(f'<rect x="{x(start):.1f}" y="{yc - 5}" width="{max(X1 - x(start), 2):.1f}" height="10" '
                             f'rx="2" fill="{colour}" opacity=".25"/>')
            xs, prev = [], -99.0
            for c in es:  # dots closer than a dot's width are nudged right, so every number can be read
                xs.append(max(x(c["t"]), prev + 18))
                prev = xs[-1]
            for c, cx in zip(es, xs):
                parts.append(f'<a class="d{c["n"]}" href="#chg-{c["n"]}"><circle cx="{cx:.1f}" cy="{yc}" r="9" fill="{colour}" '
                             f'stroke-width="2" style="stroke:var(--md-default-bg-color)"/>'
                             f'<text x="{cx:.1f}" y="{yc + 3.5}" font-size="10" font-weight="600" text-anchor="middle" '
                             f'fill="#fff" style="pointer-events:none">{c["n"] + 1}</text></a>')
                tips.append((c["n"], cx, yc, _tip_lines(c, label)))
            text = _short(key, es[0]["old"], es[-1]["new"])
            cut = lambda v: v if len(v) <= 40 else v[:39] + "…"
            lines = [text] if len(text) <= 40 or " → " not in text else [text.partition(" → ")[0], "→ " + text.partition(" → ")[2]]
            for j, line in enumerate(lines):
                dy = 4 if len(lines) == 1 else (-2, 11)[j]
                parts.append(f'<text class="mono" x="{XN}" y="{yc + dy}" font-size="11">{escape(cut(line))}'
                             f'{"<title>" + escape(text) + "</title>" if j == 0 else ""}</text>')
    ly = bottom + 10  # legend: one item per line
    items = [("dot", "an edit; the number is its row in the table below"),
             ("bar", "the setting differs from its value at the first run"),
             ("tick", "one completed run (a payu run commit); the gaps are pauses between runs"),
             ("end", "end of the completed runs (the archive folder); to the right is the current or crashed run")]
    if restarts:
        items.append(("restart", "the run counter restarted (a run was redone)"))
    for i, (kind, label) in enumerate(items):
        yy = ly + 26 * i
        icon = {"dot": f'<circle cx="9" cy="{yy}" r="9" fill="currentColor" opacity=".6"/><text x="9" y="{yy + 3.5}" font-size="10" '
                       f'font-weight="600" text-anchor="middle" style="fill:var(--md-default-bg-color)">1</text>',
                "bar": f'<rect x="0" y="{yy - 5}" width="18" height="10" rx="2" fill="currentColor" opacity=".25"/>',
                "tick": f'<line x1="9" y1="{yy - 8}" x2="9" y2="{yy + 8}" stroke="currentColor" opacity=".6" stroke-width="1.5"/>',
                "end": f'<line x1="9" y1="{yy - 9}" x2="9" y2="{yy + 9}" stroke="currentColor" opacity=".8" stroke-width="1.5" '
                       'stroke-dasharray="7 4"/>',
                "restart": f'<line x1="9" y1="{yy - 9}" x2="9" y2="{yy + 9}" stroke="currentColor" opacity=".4" '
                           'stroke-dasharray="3 4"/>'}[kind]
        parts.append(f'{icon}<text x="28" y="{yy + 4}" font-size="12.5" opacity=".8">{label}</text>')
    # tooltips come last so they draw on top; hovering a dot shows its own (see the rule below and in wide.css)
    parts.append("<style>" + "".join(f"a.d{n}:hover~g.t{n}{{display:block}}" for n, *_ in tips) + "</style>")
    for n, cx, cy, lines in tips:
        w, h = max(len(x_) for x_ in lines) * 7.4 + 24, 18 * len(lines) + 14
        tx, ty = min(max(cx - w / 2, 2), W - w - 2), cy - 18 - h
        if ty < 0:
            ty = cy + 18
        body = "".join(f'<text x="{tx + 12:.1f}" y="{ty + 22 + 18 * i:.1f}" font-size="13" '
                        f'{"font-weight=" + chr(34) + "600" + chr(34) if i == 0 else ""} style="fill:var(--md-default-fg-color)">'
                        f'{escape(x_)}</text>' for i, x_ in enumerate(lines))
        parts.append(f'<g class="tip t{n}"><rect x="{tx:.1f}" y="{ty:.1f}" width="{w:.1f}" height="{h}" rx="6" '
                     f'style="fill:var(--md-default-bg-color);stroke:var(--md-default-fg-color--light)" stroke-width="1.2"/>{body}</g>')
    return _svg(W, ly + 26 * len(items) + 6, parts, "Timeline of edits to the model build, input files and settings, one row per setting", cls)


def _grid(shown: list, runs: list, restarts: list, current: bool, years: list, width: int = 920, cls: str = "wide has-x") -> str:
    """One square per run and component, darker with more edits just before that run; a last column is the current run."""
    n, X0 = len(runs), 245
    cols = n + (1 if current else 0)
    plot = width - 280
    pitch = min(10.0 * plot / 640, plot / cols)
    parts = ['<text x="0" y="14" font-size="12.5" opacity=".7">Run counter</text>']
    for i, (_, counter) in enumerate(runs):
        if i == 0 or i in restarts:
            parts.append(f'<text x="{X0 + i * pitch:.1f}" y="14" font-size="12" opacity=".7">{counter}</text>')
    if len(runs) > 1:
        parts.append(f'<text x="{X0 + n * pitch:.1f}" y="14" font-size="12" opacity=".7" text-anchor="end">{runs[-1][1]}</text>')
    parts.append('<text x="0" y="32" font-size="12.5" opacity=".7">Model year</text>')
    if years:  # the first run's year at the left edge, the last run's at the right edge of its square
        parts.append(f'<text x="{X0}" y="32" font-size="11" opacity=".85">{years[0][1]}</text>')
        parts.append(f'<text x="{X0 + n * pitch:.1f}" y="32" font-size="11" opacity=".85" text-anchor="end">{years[-1][1]}</text>')
    for r, (g, (title, files, colour)) in enumerate(GROUPS.items()):
        y = 44 + 30 * r
        parts.append(f'<text x="0" y="{y + 15}" font-size="13" font-weight="600" fill="{colour}">{title}</text>')
        at = {}
        for c in shown:
            if c["group"] == g:
                at.setdefault(min(c["seq"], cols - 1), []).append(c)
        for i in range(cols):
            es = at.get(i, [])
            style = '' if es else ' style="fill:var(--md-default-fg-color--lightest)"'
            fill = f' fill="{colour}" opacity="{min(.25 + .25 * len(es), 1):.2f}"' if es else ""
            cell = f'<rect x="{X0 + i * pitch:.1f}" y="{y}" width="{max(pitch - 1, 1):.1f}" height="22"{fill}{style}>'
            if es:
                tip = f"after run {runs[min(i, n) - 1][1] if i else runs[0][1]}: " + "; ".join(f"{c['key']} {_change(c)}" for c in es)
                cell = f'<a href="#chg-{es[0]["n"]}">{cell}<title>{escape(tip)}</title></rect></a>'
            else:
                cell += "</rect>"
            parts.append(cell)
    for i in restarts:
        parts.append(f'<line x1="{X0 + i * pitch - .5:.1f}" y1="36" x2="{X0 + i * pitch - .5:.1f}" y2="{44 + 30 * len(GROUPS)}" '
                     'stroke="currentColor" opacity=".4" stroke-dasharray="3 4"/>')
    if current:
        bx = X0 + n * pitch - .5
        parts.append(f'<line x1="{bx:.1f}" y1="36" x2="{bx:.1f}" y2="{44 + 30 * len(GROUPS)}" stroke="currentColor" opacity=".8" '
                     'stroke-width="1.5" stroke-dasharray="7 4"/>')
    return _svg(width, 56 + 30 * len(GROUPS), parts, "Grid of runs, shaded where the build or settings were edited just before the run", cls)


def _build_box(build: dict, shown: list) -> list:
    """An 'info' box with the current build (the config.yaml entries that say which model ran) and when each last changed."""
    rows = ["| Entry in `config.yaml` | Now | Changes |", "|---|---|---|"]
    for key, label in (("model", "Model"), ("exe", "Executable"), ("modules.load", "Modules loaded")):
        cs = [c for c in shown if c["group"] == "Build" and c["key"] == key]
        items = []
        for v in (build.get(key) or "").split(", "):
            if v:
                url = _module_link(v) if key == "modules.load" else None  # a release's spack.yaml, or the pre-release's post
                items.append(f"[`{v}`]({url})" if url else f"`{v}`")
        now = "<br>".join(items) or "not found"
        if cs:
            last = cs[-1]
            where = ", ".join(f"[#{c['n'] + 1}](#chg-{c['n']})" for c in cs)
            since = (f"changed {len(cs)}×: last after run {last['counter']} ({last['t']:%-d %b %Y}, model date "
                     f"{last['model_date']}); see {where} below")
        else:
            since = "unchanged since the first run"
        rows.append(f"| {label} | {now} | {since} |")
    return ['!!! info "Current build"', ""] + ["    " + r for r in rows] + [""]


def settings_section(st: dict, e: str = "", fragment: bool = False) -> list:
    """'Settings changed during this run': the current build, a timeline (or run grid if many settings) covering the
    archived runs and the current one, the table of edits, and the collapsed housekeeping list."""
    md, hist, payu = ([] if fragment else ["## Settings changed during this run\n"]), st.get("settings"), st.get("runs", [])
    if not st.get("run_dir"):
        return md + ["This comes from the git history of the payu control directory, and this experiment has no `run_dir` in "
                     "`experiments.yml`. Add one to see here every edit made to the model's settings and build during the "
                     "run, with the old and new values.\n"]
    if hist is None:
        return md + ["The git history of the control directory could not be read (is it a git repository that the nightly job can read?).\n"]
    if not hist["runs"]:
        return md + ["No payu run commits were found in the control directory's git history, so there is nothing to compare.\n"]
    runs = [(_local(w), n) for w, n in hist["runs"]]
    boundary = runs[-1][0]  # when the last completed run was committed: edits after this belong to the current or crashed run
    changes = [dict(c, t=_local(c["when"]), group=_group_of(c)) for c in hist["changes"]]
    for c in changes:
        c["current"], c["model_date"] = c["t"] > boundary, _model_date(payu, c["counter"])
    shown, hidden = [c for c in changes if not _housekeeping(c)], [c for c in changes if _housekeeping(c)]
    for n, c in enumerate(shown):
        c["n"], c["link"] = n, _param_link(c, e)  # the link goes to where the setting is documented or set in access-om3-configs
    current = any(c["current"] for c in changes) or bool(payu and payu[-1].get("status") != 0)
    md.append(_short_intro(runs) if fragment else f"Every edit to the model's settings and build during this experiment, from the git history of the payu control "
              f"directory (`{st['run_dir']}`), starting at its first run commit ({runs[0][0]:%-d %b %Y}; {len(runs)} completed runs "
              "so far). It covers the **completed runs** (the archive folder above) and the **current or crashed run** (the run "
              "directory above): the dashed vertical line marks where the completed runs end. It reads `MOM_input`, "
              "`MOM_override`, `nuopc.runconfig`, `ice_in` and, from `config.yaml`, the model build (`model`, `exe`, `modules`) "
              "and the input files (`input`), plus the run length (`stop_n` and `restart_n` in `nuopc.runconfig`: the model time each run "
              "covers and how often it writes restarts). Each numbered dot is one edit: hover it to see the change, or use the table below, "
              "which gives the old and new value and the commit message. The column on the right of the chart gives each "
              "setting's value at the first run and now, and a setting's name links to where it is documented or set in "
              "access-om3-configs.\n")
    md += _build_box(hist.get("build") or {}, shown)
    idx, flat = [], False  # runs whose counter did not go up (a restart), once per streak
    for i in range(1, len(runs)):
        back = runs[i][1] <= runs[i - 1][1]
        if back and not flat:
            idx.append(i)
        flat = back
    restarts = [runs[i][0] for i in idx]
    years = [(t, _year(payu, counter)) for i, (t, counter) in enumerate(runs) if i in (0, len(runs) - 1) or i % 10 == 0]
    years = [(t, yr) for t, yr in years if yr]
    if not shown:
        md.append(f"No model settings or build entries changed in the {len(runs)} completed runs since the first run commit.\n")
    else:
        settings = {(c["group"], c["key"]) for c in shown}
        end = max([runs[-1][0]] + [c["t"] for c in shown])
        if current:  # leave room to the right of the last completed run
            end = max(end, boundary + (boundary - runs[0][0]) * 0.1)
        pad = max((end - runs[0][0]).total_seconds() * 0.03, 3600)
        t0, t1 = runs[0][0] - timedelta(seconds=pad), end + timedelta(seconds=pad)
        if len(settings) <= TIMELINE_MAX_SETTINGS:
            md.append(_timeline(shown, runs, restarts, t0, t1, boundary, current, years)
                      + _timeline(shown, runs, restarts, t0, t1, boundary, current, years, 1600, "xwide")
                      + _timeline_narrow(shown, runs, restarts, t0, t1, boundary, current, years) + "\n")
        else:
            md += [_grid(shown, runs, idx, current, years) + _grid(shown, runs, idx, current, years, 1600, "xwide")
                   + _timeline_narrow(shown, runs, restarts, t0, t1, boundary, current, years)
                   + "\n", "Each square is one completed run, oldest on the left; a coloured "
                   "square means settings were edited just before it, and darker means more edits."
                   + (" The dashed line is the end of the completed runs (the archive folder); the last column is the current "
                      "or crashed run." if current else "") + "\n"]
        md += _table(shown) + [""]
    if hidden:
        md += [f'??? note "Housekeeping: {len(hidden)} changes to {len({c["key"] for c in hidden})} settings in '
               f'{len({c["sha"] for c in hidden})} commits (job setup, paths, diagnostic output)"']
        md += ["    " + line for line in _md_table(hidden)] + [""]
    if fragment:  # no run_dir to check: anyone can clone the branch instead
        return md + _check("every edit listed here, from a clone of the branch", checks.settings_changes_branch(e), "with git")
    return md + _check("the build and every edit listed here", checks.settings_changes(st["run_dir"]))


def _timeline_narrow(shown: list, runs: list, restarts: list, t0: datetime, t1: datetime, boundary: datetime,
                     current: bool, years: list) -> str:
    """The phone version of the timeline: each setting's name above a full-width strip, so nothing needs a side column."""
    W, X0, X1 = 420, 14, 406
    span = (t1 - t0).total_seconds()
    x = lambda t: X0 + (t - t0).total_seconds() / span * (X1 - X0)
    norm = lambda v: None if v is None else v.strip().strip("\"'").lower()
    groups, y = [], 100
    for g, (title, files, colour) in GROUPS.items():
        keys = list(dict.fromkeys(c["key"] for c in shown if c["group"] == g))
        groups.append((g, title, files, colour, keys, y))
        y += (30 + 52 * len(keys) if keys else 46) + 8
    bottom, bx, parts = y, x(boundary), []
    if current:
        parts.append(f'<rect x="{bx:.1f}" y="26" width="{X1 - bx:.1f}" height="{bottom - 36}" fill="currentColor" opacity=".07"/>')
    ticks = _ticks(t0, t1)
    for t in (ticks[::2] if len(ticks) > 5 else ticks):
        parts.append(f'<line x1="{x(t):.1f}" y1="20" x2="{x(t):.1f}" y2="{bottom - 8}" stroke="currentColor" opacity=".12"/>'
                     f'<text x="{x(t) + 3:.1f}" y="13" font-size="11" opacity=".7">{t:%-d %b}</text>')
    parts += [f'<line x1="{x(t):.1f}" y1="24" x2="{x(t):.1f}" y2="36" stroke="currentColor" opacity=".45"/>'
              for t, _ in runs[::max(1, len(runs) // 300)]]
    parts.append(f'<text x="{X0}" y="50" font-size="11" opacity=".75">Completed runs ({len(runs)}), model year below</text>')
    last = -99.0
    for t, yr in years:
        if x(t) - last >= 34:
            parts.append(f'<text x="{x(t):.1f}" y="63" font-size="10" opacity=".85" text-anchor="middle">{yr}</text>')
            last = x(t)
    parts += [f'<line x1="{x(t):.1f}" y1="26" x2="{x(t):.1f}" y2="{bottom - 8}" stroke="currentColor" opacity=".4" stroke-dasharray="3 4"/>'
              for t in restarts]
    parts.append(f'<line x1="{bx:.1f}" y1="26" x2="{bx:.1f}" y2="{bottom - 8}" stroke="currentColor" opacity=".8" stroke-width="1.5" '
                 'stroke-dasharray="7 4"/>')
    parts.append(f'<text x="{bx - 5:.1f}" y="82" font-size="11" text-anchor="end" opacity=".85">◂ archive</text>')
    if current:
        parts.append(f'<text x="{bx + 5:.1f}" y="82" font-size="11" opacity=".85">current run ▸</text>')
    for g, title, files, colour, keys, top in groups:
        parts.append(f'<text x="{X0}" y="{top + 14}" font-size="13" font-weight="600" fill="{colour}">{title}</text>')
        if not keys:
            parts.append(f'<text x="{X0}" y="{top + 34}" font-size="12" opacity=".7">no changes</text>')
        for r, key in enumerate(keys):
            yb = top + 30 + 52 * r
            es = [c for c in shown if c["group"] == g and c["key"] == key]
            label = _label(key, g, keys)
            text = f'<text class="mono" x="{X0}" y="{yb + 12}" font-size="12">{escape(label if len(label) <= 26 else label[:25] + "…")}</text>'
            link = next((c["link"] for c in es if c.get("link")), None)
            parts.append(f'<a href="{escape(link)}" target="_blank" rel="noopener">{text}</a>' if link else text)
            now = _short(key, es[0]["old"], es[-1]["new"])
            parts.append(f'<text class="mono" x="{X1}" y="{yb + 12}" font-size="11" text-anchor="end" opacity=".85">'
                         f'{escape(now if len(now) <= 30 else now[:29] + "…")}</text>')
            yc = yb + 30
            parts.append(f'<line x1="{X0}" y1="{yc}" x2="{X1}" y2="{yc}" stroke="currentColor" opacity=".15"/>')
            first, start, cur = norm(es[0]["old"]), None, norm(es[0]["old"])
            for c in es:
                new = norm(c["new"])
                if cur == first and new != first:
                    start = c["t"]
                if cur != first and new == first:
                    parts.append(f'<rect x="{x(start):.1f}" y="{yc - 4}" width="{max(x(c["t"]) - x(start), 2):.1f}" height="8" rx="2" '
                                 f'fill="{colour}" opacity=".25"/>')
                    start = None
                cur = new
            if start is not None:
                parts.append(f'<rect x="{x(start):.1f}" y="{yc - 4}" width="{max(X1 - x(start), 2):.1f}" height="8" rx="2" '
                             f'fill="{colour}" opacity=".25"/>')
            prev = -99.0
            for c in es:
                cx = max(x(c["t"]), prev + 17)
                prev = cx
                parts.append(f'<a href="#chg-{c["n"]}"><circle cx="{cx:.1f}" cy="{yc}" r="8" fill="{colour}" stroke-width="2" '
                             f'style="stroke:var(--md-default-bg-color)"/><text x="{cx:.1f}" y="{yc + 3}" font-size="9" font-weight="600" '
                             f'text-anchor="middle" fill="#fff" style="pointer-events:none">{c["n"] + 1}</text></a>')
    ly = bottom + 6
    for i, (kind, label) in enumerate([("dot", "an edit: tap its number to jump to its row below"), ("bar", "differs from the first run"),
                                       ("end", "end of the completed runs; right of it is the current run")]):
        yy = ly + 20 * i
        icon = {"dot": f'<circle cx="8" cy="{yy}" r="8" fill="currentColor" opacity=".6"/><text x="8" y="{yy + 3}" font-size="9" '
                       f'font-weight="600" text-anchor="middle" style="fill:var(--md-default-bg-color)">1</text>',
                "bar": f'<rect x="0" y="{yy - 4}" width="16" height="8" rx="2" fill="currentColor" opacity=".25"/>',
                "end": f'<line x1="8" y1="{yy - 8}" x2="8" y2="{yy + 8}" stroke="currentColor" opacity=".8" stroke-width="1.5" '
                       'stroke-dasharray="5 3"/>'}[kind]
        parts.append(f'{icon}<text x="24" y="{yy + 4}" font-size="11" opacity=".8">{label}</text>')
    return _svg(W, ly + 62, parts, "Timeline of edits, one strip per setting", "narrow")


def _short_intro(runs: list) -> str:
    """The one-paragraph introduction for an experiment's history on the Config Diff page."""
    return ("From the git history of this experiment's branch in access-om3-experiments, starting at its first run commit "
            f"({runs[0][0]:%-d %b %Y}; {len(runs)} completed runs). Each numbered dot is one edit: tap it to jump to its row in "
            "the table, which gives the old and new value and the commit message.\n")


def settings_fragment(exp: dict) -> str:
    """HTML of one experiment's settings history for the Config Diff page: the status page's section, without its
    heading, with a short introduction and a check that anyone can run by cloning the branch."""
    st = {"run_dir": f"branch {exp['branch']}", "settings": exp["history"], "runs": exp["runs"]}
    md = "\n".join(settings_section(st, exp["branch"], fragment=True))
    return markdown.Markdown(extensions=["admonition", "pymdownx.details", "pymdownx.superfences", "pymdownx.highlight",
                                         "tables", "md_in_html", "attr_list"]).convert(md)


def write_config_diff_data(summary_md: str, out: Path) -> None:
    """For the Config Diff page: index.json, and per experiment <slug>.json (its parameters) and <slug>.html (its
    settings history). If the data can't be read, the page is left with an empty list and the site still builds."""
    out.mkdir(parents=True, exist_ok=True)
    index = []
    try:
        with tempfile.TemporaryDirectory() as work:
            for x in config_diff_data.collect(Path(summary_md).read_text(), Path(work)):
                (out / f"{x['slug']}.json").write_text(json.dumps(x["params"], separators=(",", ":")))
                (out / f"{x['slug']}.html").write_text(settings_fragment(x))
                index.append({"slug": x["slug"], "label": x["branch"], "group": x["group"], "sha": x["sha"]})
    except Exception as err:
        print(f"::error::the Config Diff page has no data: {err!r}")
        traceback.print_exc()
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"Config Diff: {len(index)} experiments")


CONFIGS_URL = "https://github.com/ACCESS-NRI/access-om3-configs"
# The site's address, for links in text copied elsewhere: SITE_URL if set, else the repository's GitHub Pages address
SITE = os.environ.get("SITE_URL") or "https://{}.github.io/{}/".format(*REPO.lower().split("/"))


def _issue_link(url: str) -> str:
    """'[owner/repo#123](url)' for a GitHub issue or pull request, else the link itself."""
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/(?:issues|pull)/(\d+)", url or "")
    return f"[{m[1]}#{m[2]}]({url})" if m else f"<{url}>"


def _tail(text: str, n: int = 8) -> str:
    """The last n non-empty lines of a log tail."""
    return "\n".join([line for line in text.splitlines() if line.strip()][-n:])


def issue_summary(e: str, st: dict) -> str:
    """Markdown to paste into the run's GitHub issue: state, throughput, any crash with its log tails, the busiest
    truncation locations, and references to the files used."""
    runs, refs = st.get("runs", []), []
    ref = lambda path: refs.append(path) or len(refs)  # number a reference, for the list at the end
    emoji, sentence = health(st)
    done = [r for r in runs if r.get("model_finish")]
    years, sypd = _progress(runs)
    md = [f"### OM3 Monitor summary: `{e}`", f"*{st['description']}*" if st.get("description") else "", "",
          f"- **Latest submission:** {emoji} {sentence.replace(' See the logs below.', '')}",
          f"- **Model date reached:** {_date(done[-1]['model_finish']) if done else 'unknown'}"
          + (f" ({years:.1f} model years complete)" if years else ""),
          f"- **Average throughput:** {sypd:.2f} SYPD" if sypd else "- **Average throughput:** not known"]
    segs = [s for s in (st.get("truncations") or {}).get("segments", []) if s["truncs"] is not None]
    total = sum(s["truncs"] for s in segs)
    md.append(f"- **Truncations:** {total:,} in {sum(1 for s in segs if s['truncs'])} of {len(segs)} output folders"
              if total else "- **Truncations:** none recorded in `ocean.stats`" if segs else "- **Truncations:** no `ocean.stats` files found")
    if emoji == "❌" and runs:
        last = runs[-1]
        md[3] += (f" (run {last.get('run')}, PBS job {last.get('job_id') or '?'}, last recorded {_when(last.get('mtime'))}"
                  f", model date {_date(_start(last, runs))})")
        logs = {**st.get("logs", {}), **st.get("run_logs", {})}  # the run directory's own logs win: the crashed run
        # access-om3.err/.out, or payu's copies of a failed run's (error_logs/access-om3.<job>.err/.out); newest first
        picked = [p for p in sorted(logs, key=lambda p: -logs[p]["mtime"])
                  if os.path.basename(p).startswith("access-om3.") and p.endswith((".err", ".out"))]
        err = next((p for p in picked if p.endswith(".err")), None)
        out = next((p for p in picked if p.endswith(".out")), None)
        if err or out:
            md += ["", "<details><summary>Last lines of the model logs</summary>", ""]
            for p in (err, out):
                if p:
                    md += [f"`{os.path.basename(p)}` [{ref(p)}]", "```", _tail(logs[p]["text"]), "```"]
            md += ["</details>"]
    boxes = (st.get("truncations") or {}).get("boxes", [])[:5]
    if boxes:
        md += ["", "| Where | Records | Output folders | Max speed (m/s) | Max CFL |", "|---|---:|---|---:|---:|"]
        md += [f"| {_place(b['lon'], b['lat'])} | {b['records']}{'*' if b['capped'] else ''} | {_spans(b['outputs'])} | "
               f"{b['max_vel']:.1f} | {b['max_cfl']:.2f} |" for b in boxes]
        if any(b["capped"] for b in boxes):
            md.append("\n\\* a PE at MOM6's 50-records-per-run cap, so the true count is higher.")
    ref(f"Archive folder: `{st.get('archive')}`")
    if st.get("run_dir"):
        ref(f"Run directory: `{st['run_dir']}` (current or crashed run in `work/`)")
    ref(f"Status page: {SITE}live/{quote(e)}/")
    md += ["", "**References**"] + [f"{i}. " + (r if r.startswith(("Archive", "Run directory", "Status")) else f"`{r}`")
                                    for i, r in enumerate(refs, 1)]
    md += ["", f"*Generated automatically by the [OM3 Monitor]({SITE}live/) on {_when(datetime.now(timezone.utc).timestamp())}; "
               "it may contain errors, so check against the files above.*"]
    return "\n".join(md)


def _ref_link(kind: str, name: str) -> str:
    """A link to a branch, tag or commit of access-om3-configs, by its readable name."""
    return f"[{name}]({CONFIGS_URL}/{'commit' if kind == 'commit' else 'tree'}/{name})"


def friends_section(st: dict) -> list:
    """'Where are my friends?': the closest history the control directory shares with access-om3-configs."""
    f, run_dir = st.get("friends"), st.get("run_dir")
    if f is None or not run_dir:  # no run directory, no git history there, or no clone to compare with
        return []
    md = ["## Where are my friends?\n"]
    if not f.get("found"):
        return md + ["😢 **Couldn't find any of its friends!** This run's control directory shares no history with "
                     f"[access-om3-configs]({CONFIGS_URL}).\n"] + _check("that nothing is shared", checks.friends(run_dir))
    commit = (_ref_link("tree", f["tag"]["name"]) if f.get("tag") and f["tag"]["behind"] == 0 else _ref_link("commit", f["short"]))
    md += [f"How this run's configuration relates to [access-om3-configs]({CONFIGS_URL}): the newest commit that the control "
           f"directory's history (latest commit `{f['run_head']}`) shares with the configurations repository.\n",
           f"- **Closest shared commit:** {commit}" + (f" on branch {_ref_link('tree', f['branch'])}" if f.get("branch") else "")
           + f" · {_date(f['when'])} · “{f['subject']}”",
           f"- **Changes in this run since then:** {f['diff']['stat'] or 'none'}"]
    if f.get("tag"):
        md += [f"- **Closest tag:** {_ref_link('tree', f['tag']['name'])}"
               + (f", {f['tag']['behind']} commits before the shared commit" if f["tag"]["behind"] else " (the shared commit itself)"),
               f"- **Changes in this run since that tag:** {f['tag']['diff']['stat'] or 'none'}"]
    md.append("")
    label = f["branch"] or f["short"]
    if f["diff"]["text"]:
        md += _block(f"Diff: this run vs {label} @ {f['short']}", f["diff"]["text"], kind="example", lang="diff")
    if f.get("tag") and f["tag"]["diff"]["text"] and f["tag"]["behind"]:
        md += _block(f"Diff: this run vs tag {f['tag']['name']}", f["tag"]["diff"]["text"], kind="example", lang="diff")
    return md + _check("the closest shared commit and tag", checks.friends(run_dir, f["short"]))


def datastore_page(e: str, st: dict, nb_links: str) -> str:
    """Status page for an experiment given as a datastore (.json, e.g. ACCESS-CM3): what the catalogue says, and its notebooks."""
    d = st.get("datastore") or {}
    years = f"{d.get('n_years') or '?'} ({d['years'][0]}–{d['years'][1]})" if d.get("years") else "unknown"
    return "\n".join([f"# {e}\n"] + ([f"{st['description']}\n"] if st.get("description") else [])
                     + ([f"**GitHub issue:** {_issue_link(st['issue'])}\n"] if st.get("issue") else []) + [
        f"**Notebooks:** {nb_links or 'none yet'}\n", "## Datastore\n",
        f"Read from the datastore given in `experiments.yml`: `{st.get('archive')}`.\n",
        f"- **Files catalogued:** {d.get('files', 0):,}", f"- **Model years in the catalogue:** {years}",
        f"- **Datastore last modified:** {_when(d.get('mtime')) or 'unknown'}", "",
        "This experiment isn't a payu run (ACCESS-CM3, for example, runs with Rose/Cylc), so the run-status sections "
        "(recent runs, logs, truncations, settings) don't apply here; its notebooks are run on this datastore as it is.\n"])


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
    md += _block("📋 Summary for the GitHub issue", issue_summary(e, st), kind="abstract", lang="markdown", fence="````")
    if run_dir:
        md += ["## Run directory: current or crashed run\n", f"Read from the payu control directory `{run_dir}`.\n"]
        med = next((v["text"] for k, v in st["run_logs"].items() if k.endswith("work/log/med.log")), "")
        dates = [line for line in med.splitlines() if "Model Date:" in line]
        if dates:
            md.append(f"**Latest model date in `work/log/med.log`:** {dates[-1].split('Model Date:')[1].split()[0]}"
                      f" (rate {dates[-1].split('rate =')[-1].strip()})\n")
            md += _check("the latest model date", checks.model_date(run_dir))
        md += ["### Log files\n"] + (_logs(st["run_logs"]) or ["No logs found there.\n"])
        md += truncation_section(st, work=True)
        if st.get("configs"):
            md.append("### Configuration files\n")
            for path, cfg in st["configs"].items():
                md += _block(f"{path} (modified {_when(cfg['mtime'])})", cfg["text"], kind="info",
                             lang=LEXERS.get(os.path.basename(path), "text"), opts='linenums="1"')
            md += _check("when these files were last modified",
                         checks.config_files(run_dir, [os.path.basename(p) for p in st["configs"]]))
    md += ["## Archive folder: completed runs\n", f"Read from the payu archive folder `{archive}`.\n",
           f"- **Output folders:** {st.get('outputs', 0)} (latest `{st.get('latest_output')}`)",
           f"- **Model date reached:** {_date(done[-1]['model_finish']) if done else 'unknown'}",
           f"- **Runs recorded by payu:** {len(runs)}, of which failed: {sum(1 for r in runs if r.get('status') not in (0, None))}", ""]
    md += _check("these counts", checks.archive_counts(archive))
    if sypd_png:
        md += ["### Model throughput\n", "Simulated years per day, made by `plot_sypd.py` from the "
               f"`outputNNN/log/med.log` files of the completed submissions in `{archive}`.\n",
               "![Simulated years per day](sypd.png)\n"]
        md += _check("the rate in each output's med.log", checks.throughput(archive))
    if runs:
        md += ["### Recent runs\n", f"From payu's job records in `{archive}/payu_jobs`; times are Canberra time.\n",
               "| Run | PBS job | Result | Model period | Wall time (h) | SYPD | Record last modified |",
               "|---|---|---|---|---|---|---|"]
        for r in runs[-10:][::-1]:
            period = f"{_date(_start(r, runs))} → {_date(r.get('model_finish'))}"
            hours = f"{r['seconds'] / 3600:.1f}" if r.get("seconds") else ""
            md.append(f"| {r.get('run')} | {r.get('job_id') or ''} | {_result(r)} | {period} | {hours} | {_sypd(r)} | {_when(r.get('mtime'))} |")
        md.append("")
        md += _check("this table", checks.recent_runs(archive))
    if st.get("logs"):
        md += ["### Latest logs in the archive folder\n"] + _logs(st["logs"])
    if st.get("metadata"):
        md += _block(f"{archive}/metadata.yaml", st["metadata"], kind="info", lang="yaml")
    md += truncation_section(st)
    md += settings_section(st, e)
    md += friends_section(st)
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
    page = (lambda e, st, links, png: datastore_page(e, st, links)) if st.get("datastore_only") else status_page
    (docs / "live" / e / "index.md").write_text(page(e, st, links + (f"\n\n{job_note}" if job_note else ""),
                                                            (src / "sypd.png").exists()))
    done = [r for r in st.get("runs", []) if r.get("model_finish")]
    latest = max((r.get("mtime") or 0 for r in st.get("runs", [])), default=0)  # when payu last recorded a run
    if st.get("datastore_only"):  # e.g. ACCESS-CM3: sorted by when its datastore last changed
        latest = (st.get("datastore") or {}).get("mtime") or 0
    years, avg = _progress(st.get("runs", []))
    progress = (f" ({years:.0f} years complete" if years and years >= 10 else f" ({years:.1f} years complete" if years else "") + (
        f", avg {avg:.1f} SYPD" if years and avg else "") + (")" if years else "")
    ds = st.get("datastore") or {}
    reached = ("⚠️ no status snapshot" if st.get("no_snapshot") else
               "– (datastore given directly" + (f"; {ds.get('n_years') or '?'} model years, {ds['years'][0]}–{ds['years'][1]}"
                                                 if ds.get("years") else "") + f")<br>{_when(latest)}"
               if st.get("datastore_only") else
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
    # Config Diff page: every Experiments-table branch's MOM_input, and its settings history, fetched by the page
    shutil.copy2(ROOT / "pages" / "config_diff.css", docs / "css")
    (docs / "js").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "pages" / "config_diff.js", docs / "js")
    shutil.copy2(ROOT / "pages" / "config-diff.md", docs)
    write_config_diff_data(summary_md, docs / "assets" / "config_diff")

    contributing = (ROOT / "pages" / "contributing.md").read_text()
    (docs / "contributing.md").write_text(contributing.replace("{repo}", REPO).replace("{issue_url}", ISSUE_URL))
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
    series = {n: _snapshot(STATUS / f"{n}.json").get("runs", []) for n in names if n != "archive"}
    plot = ("\n## Throughput of all runs\n\nSimulated years per day (SYPD) of every completed run, from payu's job records, "
            "against the model year it reached: one line per experiment in the table above.\n\n"
            "![Simulated years per day, all experiments](sypd_all.png)\n"
            if monitor_plot(series, docs / "live" / "sypd_all.png") else "")
    help_text = (ROOT / "pages" / "monitor_help.md").read_text().replace("{repo}", REPO).replace("{notebooks}", ", ".join(cfg["notebooks"]))
    (docs / "live" / "index.md").write_text(
        # Hide the empty "On this page" column so the table gets the room (as the Experiments page does).
        "<style>.md-sidebar--secondary:not([hidden]) { display: none }</style>\n\n"
        "# OM3 Monitor\n\nFollows ACCESS-OM3 runs while they are running: each night it re-runs a few evaluation notebooks "
        "on each run's latest output and builds a status page from its payu records and logs. Pages are replaced "
        "nightly; nothing is kept.\n\n"
        "Experiments are listed by their latest activity with newest first. An experiment's notebooks are only re-run "
        "when it has new output. Click an experiment for its run status. Times are Canberra time.\n\n"
        "!!! warning \"Written with Claude: please check\"\n"
        "    OM3 Monitor pages and the code that builds them were written mostly by Claude with feedback from "
        "[@chrisb13](https://github.com/chrisb13/). Each status page has copyable checks that one can run on Gadi. "
        "Please report anything wrong or unclear: see [Contributing and feedback](../contributing.md).\n\n"
        "| Experiment | Description | Latest submission (model date reached)<br>last payu record ▾ | Notebooks last run "
        "| Notebooks shown "
        "| Failed |\n|---|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n" + plot + "\n" + help_text)

    if os.environ.get("GITHUB_STEP_SUMMARY"):  # shown at the top of the workflow run's page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(run_summary([n for n in names if n != "archive"]))
    nav = [{"Home": "index.md"}, {"OM3 Monitor": live_nav}, {"Config Diff": "config-diff.md"}, {"Evaluation Summary": "evaluation-summary.md"},
           {"Contributing and feedback": "contributing.md"}]
    (BUILD / "mkdocs.yml").write_text(yaml.safe_dump({"INHERIT": "../mkdocs.yml", "nav": nav, "hooks": ["../scripts/edit_links.py"]}, sort_keys=False))


if __name__ == "__main__":
    main(sys.argv[1])
