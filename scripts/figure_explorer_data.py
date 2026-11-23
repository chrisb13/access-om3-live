"""Build the figure explorer's data from the Experiments page and access-om3-paper-1's index.

* Runs: every row of the Experiments page, read with mkfigs' own parser (from the
  paper repo's pinned submodule), so run names and forcing match the notebooks.
* Data: each run's bundle, listed in the paper repo's documentation/figure-data.json,
  downloaded straight from Figshare (no API), checked against its sha256 and cached.
  A run whose bundle fails keeps its last good copy; one run never breaks the build.
* Lines: each run's own lines come from its own bundle. Other lines naming a run on
  the page are dropped (that run's bundle is the source); the rest (OM2, CM3,
  observations) become references, shown once per figure.

Writes <out>/index.json plus <out>/data/<figure>/<run>.json and refs.json.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import requests

PAPER_REPO = "https://github.com/ACCESS-Community-Hub/access-om3-paper-1"


def _id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _md_links(text: str) -> str:
    """Escape text, keeping [label](url) links (the ISSUES entries use them)."""
    out, pos = [], 0
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", text):
        out += [esc(text[pos:m.start()]), f'<a href="{m.group(2)}">{esc(m.group(1))}</a>']
        pos = m.end()
    return "".join(out + [esc(text[pos:])])


def load_issues(paper: Path) -> tuple[dict, dict]:
    """ISSUES and CATEGORIES from the paper repo's notebooks/mkfigs_issues.py (empty if absent)."""
    path = paper / "notebooks" / "mkfigs_issues.py"
    if not path.exists():
        return {}, {}
    spec = importlib.util.spec_from_file_location("mkfigs_issues", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "ISSUES", {}), getattr(mod, "CATEGORIES", {})


def load_explorer_settings(paper: Path) -> dict:
    """The explorer's settings from the same file: FIGURE_TITLES (png stem -> title), RUN_COLOURS (run -> colour
    slot), REFERENCE_SOURCES (what comparison lines are and where their data is), EXPLORER_CATEGORIES."""
    path = paper / "notebooks" / "mkfigs_issues.py"
    names = ("FIGURE_TITLES", "RUN_COLOURS", "REFERENCE_SOURCES", "EXPLORER_CATEGORIES", "FIGURE_TITLE_RULES", "EXPLORER_MERGE")
    if not path.exists():
        return {n: {} for n in names}
    spec = importlib.util.spec_from_file_location("mkfigs_issues", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {n: getattr(mod, n, [] if n in ("REFERENCE_SOURCES", "FIGURE_TITLE_RULES", "EXPLORER_MERGE") else {}) for n in names}


def describe_reference(ref: dict, notebook: str, forcings: set, rules: list) -> dict:
    """The display name and data source of a comparison line, from the first REFERENCE_SOURCES rule it matches."""
    for rule in rules:
        if rule.get("notebook", notebook) != notebook or not (m := re.search(rule["match"], ref["name"] or "")):
            continue
        if rule.get("drop"):
            return {"drop": True}
        if "by_forcing" in rule:
            if len(forcings) != 1 or next(iter(forcings)) not in rule["by_forcing"]:
                return {}
            rule = rule["by_forcing"][next(iter(forcings))]
        groups = m.groups()
        out = {"name": rule["name"].format(*groups)} if "name" in rule else {}
        for k in ("intake", "source"):
            if k in rule:
                out[k] = rule[k].format(*groups)
        return out
    return {}


def retitle(title: str, subtitle: str, rules: list) -> tuple[str, str]:
    """Apply the first FIGURE_TITLE_RULES rule matching the title."""
    for rule in rules:
        if re.search(rule["match"], title):
            return re.sub(rule["match"], rule["title"], title), rule.get("subtitle", subtitle)
    return title, subtitle


SUFFIX_WORDS = {"_max": "maximum", "_min": "minimum"}


def variable_labels(display: str) -> dict:
    """"var: Long name" figures (the timeseries notebook): the variable as the heading and the long name under it;
    in the figure list "Long name maximum (var)", or just the variable when the long name is over 8 words."""
    m = re.match(r"^([A-Za-z]\w*): (.+)$", display)
    if not m:
        return {}
    var, desc = m.groups()
    suffix = next((w for end, w in SUFFIX_WORDS.items() if var.endswith(end)), "")
    label = var if len(desc.split()) > 8 else f"{desc}{' ' + suffix if suffix else ''} ({var})"
    return {"display": label, "heading": var, "heading_sub": desc, "variable": var}


def merge_figures(figures: dict, refs: dict, out: Path, rules: list, log: list) -> None:
    """EXPLORER_MERGE: show several figures as one (their runs' lines and references together). A rule names figure
    file stems (`into`, `from`); every figure saved under one of them joins, including several under one stem (a
    notebook that draws different panels for different runs). Panels fold into the first one, or with
    `panels: "by_title"` are matched by title (a panel only some runs have is added)."""
    by_stem: dict[str, list] = {}
    for fid, f in figures.items():
        for st in {Path(r["png"]).stem for r in f["runs"].values()}:
            by_stem.setdefault(st, []).append(fid)
    for rule in rules:
        group, stem_of = [], {}
        for st in [rule["into"], *rule.get("from", [])]:
            for fid in sorted(by_stem.get(st, []), key=lambda f: -len(figures[f]["runs"]) if f in figures else 0):
                if fid in figures and fid not in group:
                    group.append(fid); stem_of[fid] = st
        if len(group) < 2:
            continue
        target, sources = group[0], group[1:]
        fig = figures[target]
        tpanels = fig["panels"]
        by_title = rule.get("panels") == "by_title"

        def to_target(fid, i):
            if not by_title:
                return 0
            p = figures[fid]["panels"][i]
            j = next((j for j, t in enumerate(tpanels) if t["title"] == p["title"]), None)
            if j is None:
                tpanels.append(p); j = len(tpanels) - 1
            return j

        merged: dict[str, dict] = {}
        entries: dict[str, dict] = {}
        for fid in group:
            names = rule.get("series", {}).get(stem_of[fid], {})
            scale = rule.get("scale", {}).get(stem_of[fid], 1)
            for run, entry in figures[fid]["runs"].items():
                entries.setdefault(run, entry)
                data = json.loads((out / "data" / fid / f"{_id(run)}.json").read_text())
                for i, panel in enumerate(data["panels"]):
                    j = to_target(fid, i)
                    dest = merged.setdefault(run, {}).setdefault(j, {**panel, "lines": []})
                    for line in panel["lines"]:
                        line = {**line, "name": names.get(line["name"], line["name"]),
                                "y": [v * scale if isinstance(v, (int, float)) else v for v in line["y"]] if scale != 1 else line["y"]}
                        if not any(l["x"] == line["x"] and l["y"] == line["y"] for l in dest["lines"]):
                            dest["lines"].append(line)
        fig["runs"] = entries
        fig.update(display=rule.get("title", fig["display"]), caption=rule.get("caption", fig["caption"]),
                   hidden_series=rule.get("hidden", []))
        if by_title and len(tpanels) > 1:
            fig["subtitle"] = ", ".join(p["title"] for p in tpanels if p["title"])
        for run, panels in merged.items():
            rows = [panels.get(j, {"lines": [], "periods": [], "title_full": tpanels[j]["title"]}) for j in range(len(tpanels))]
            (out / "data" / target).mkdir(parents=True, exist_ok=True)
            (out / "data" / target / f"{_id(run)}.json").write_text(json.dumps({"panels": rows}, separators=(",", ":")))
        seen = {json.dumps([r["kind"], r["panel"], r.get("name"), r.get("x"), r.get("y")]) for r in refs.values() if r["fid"] == target}
        for k, r in list(refs.items()):
            if r["fid"] in sources:
                j = to_target(r["fid"], r["panel"])
                key = json.dumps([r["kind"], j, r.get("name"), r.get("x"), r.get("y")])
                if key in seen:
                    del refs[k]
                else:
                    seen.add(key); r.update(fid=target, panel=j)
        for fid in sources:
            del figures[fid]
            shutil.rmtree(out / "data" / fid, ignore_errors=True)
        log.append(f"{rule['into']}: merged {len(sources)} figure(s) into it ({', '.join(sorted(set(stem_of[f] for f in sources)))})")


def split_categories(cats: dict, extra: dict) -> dict:
    """Move each EXPLORER_CATEGORIES notebook out of its section into the new one, placed right after it."""
    out: dict[str, list] = {}
    for cname, nbs in cats.items():
        out[cname] = [nb for nb in nbs if not any(nb in moved for moved in extra.values())]
        for new, moved in extra.items():
            if new not in out and any(nb in moved for nb in nbs):
                out[new] = [nb for nb in moved if nb in nbs]
    return {c: nbs for c, nbs in out.items() if nbs}


def colour_slots(names: list[str], pinned: dict) -> tuple[dict, list[str]]:
    """Each run's colour slot: pinned ones keep theirs; the rest take the lowest free slots, in table order."""
    slots = {n: pinned[n] for n in names if n in pinned}
    used, log = set(slots.values()), []
    for n in names:
        if n not in slots:
            slots[n] = next(i for i in range(len(names) + len(used) + 1) if i not in used)
            used.add(slots[n])
            log.append(f"{n}: colour slot {slots[n]} (not in RUN_COLOURS; add it there to pin its colour)")
    return slots, log


def categorise(notebooks: list[str], issues: dict, categories: dict) -> dict[str, list[str]]:
    """Same rule as the paper site's landing page: a notebook named under a category goes
    there; otherwise the first category listing one of its issue numbers; else Miscellaneous."""
    try:
        from mkfigs.pushit import (
            group_notebooks_by_category,  # the landing page's own function
        )
        return group_notebooks_by_category(notebooks, issues, categories)
    except (ImportError, AttributeError, SystemExit):
        pass
    groups: dict[str, list[str]] = {name: [] for name in categories}
    for nb in notebooks:
        nums = {int(n) for n in re.findall(r"#(\d+)", issues.get(nb, ""))}
        home = next((c for c, members in categories.items() if nb in members), None)
        home = home or next((c for c, members in categories.items() if nums & {m for m in members if isinstance(m, int)}), None)
        groups.setdefault(home or "Miscellaneous", []).append(nb)
    return {c: nbs for c, nbs in groups.items() if nbs}


def fetch_bundle(name: str, entry: dict, cache: Path, log: list[str]) -> dict | None:
    """The run's bundle: from cache if its sha256 matches, else downloaded and verified.
    Falls back to the run's last good copy if the download fails."""
    cache.mkdir(parents=True, exist_ok=True)
    good = cache / "last-good.json"
    last = json.loads(good.read_text()) if good.exists() else {}
    path = cache / f"{entry['sha256']}.json.gz"
    if not path.exists():
        try:
            response = requests.get(entry["url"], timeout=120)
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest() != entry["sha256"]:
                raise ValueError("checksum differs from the index")
            path.write_bytes(response.content)
        except (requests.RequestException, ValueError) as exc:
            old = cache / f"{last.get(name, '')}.json.gz"
            if last.get(name) and old.exists():
                log.append(f"{name}: download failed ({exc}); using the last good copy")
                path = old
            else:
                log.append(f"{name}: download failed ({exc}); no data shown")
                return None
    last[name] = path.name.removesuffix(".json.gz")
    good.write_text(json.dumps(last, indent=1, sort_keys=True))
    return json.loads(gzip.decompress(path.read_bytes()))


def _series_name(label: str, run: str) -> str:
    name = label.replace(run, " ")
    name = re.sub(r"^\s*(ACCESS-OM3|OM3)(-\d+km)?\s*[:\-]?\s*", "", name)
    return re.sub(r"\s+", " ", name).strip(" :-")


def _iso_time_x(bundle: dict | None) -> int:
    """Numeric x on a time panel -> ISO date strings; returns how many lines were converted.

    mkfigs' linedata can leave raw numbers on a date axis: datetime64[ns] values (|x| > 1e12, ns since 1970;
    their panels can even say x_kind "number") or matplotlib date numbers (days since 1970), e.g. unlabelled
    observed ranges drawn with plt.plot.
    """
    from datetime import timedelta
    epoch, n = datetime(1970, 1, 1), 0
    def iso(v, ns):
        if not isinstance(v, (int, float)):
            return v
        return (epoch + (timedelta(microseconds=v / 1e3) if ns else timedelta(days=v))).isoformat(timespec="seconds")
    def year_iso(v):
        if not isinstance(v, (int, float)):
            return v
        return (datetime(int(v), 1, 1) + timedelta(days=(v - int(v)) * 365.25)).isoformat(timespec="seconds")
    is_year = lambda v: isinstance(v, (int, float)) and 1000 <= v < 3000
    for rec in (bundle or {}).get("figures", []):
        panels = rec.get("panels", [])
        # a grid of annual panels where only the labelled row was read as dates (year numbers elsewhere): all dates
        if any(p.get("x_kind") == "time" for p in panels):
            for p in panels:
                xs = [v for line in p["lines"] for v in line["x"] if v is not None]
                if p.get("x_kind") == "number" and xs and all(is_year(v) for v in xs):
                    for line in p["lines"]:
                        line["x"] = [year_iso(v) for v in line["x"]]
                    p["x_kind"], n = "time", n + len(p["lines"])
        for p in panels:
            if p.get("x_kind") == "time":  # periods (axvspan) and vlines still in year numbers
                for m in p.get("marks", []):
                    if m["kind"] == "span" and all(is_year(v) for v in m["x"]):
                        m["x"] = [year_iso(v) for v in m["x"]]
                    elif m["kind"] == "vline" and is_year(m["x"]):
                        m["x"] = year_iso(m["x"])
        for p in panels:
            # a datetime64 x axis that linedata took for plain numbers (x label time/year/date, values in ns)
            if p.get("x_kind") == "number" and (p.get("x_label") or "").lower() in ("time", "year", "date") and all(
                    max((abs(v) for v in line["x"] if isinstance(v, (int, float))), default=1e13) > 1e12 for line in p["lines"]):
                p["x_kind"] = "time"
            if p.get("x_kind") == "time":
                for line in p["lines"]:
                    nums = [abs(v) for v in line["x"] if isinstance(v, (int, float))]
                    if nums:  # the unit is the line's: an ns series crossing 1970 has small values too
                        ns = max(nums) > 1e12
                        line["x"] = [iso(v, ns) for v in line["x"]]; n += 1
    return n


def _start_year(run: dict, bundle: dict | None) -> int | None:
    m = re.search(r"\((\d{4})-\d{4}\)", run["length"])
    if m:
        return int(m.group(1))
    # The most common first year of the run's own time lines (not the earliest of all lines:
    # references such as observed ranges start earlier, and a few "own" lines can be another run's)
    years = Counter(int(x[:4]) for f in (bundle or {}).get("figures", []) for p in f.get("panels", [])
                    if p.get("x_kind") == "time" for line in p["lines"] if line["role"] == "own"
                    for x in line["x"][:1] if isinstance(x, str))
    return years.most_common(1)[0][0] if years else None


def build(experiments_md: str, paper: Path, out: Path, cache: Path) -> list[str]:
    """Write the explorer's data under *out*; return log lines worth showing in the build."""
    sys.path.insert(0, str(paper / "external" / "access-model-mkfigs" / "src"))
    log: list[str] = []
    try:
        from mkfigs.experiments import parse_experiments
    except ImportError as exc:  # the paper repo's mkfigs predates the figure-explorer code
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.json").write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(
            timespec="seconds"), "runs": [], "categories": [], "figures": {}}))
        return [f"mkfigs in the paper repo has no figure-explorer code yet ({exc}); page left empty"]
    index_path = paper / "documentation" / "figure-data.json"
    index = json.loads(index_path.read_text()).get("runs", {}) if index_path.exists() else {}
    runs, bundles = [], {}
    for r in parse_experiments(experiments_md):
        run = r.as_dict()
        entry = index.get(r.name)
        bundles[r.name] = fetch_bundle(r.name, entry, cache, log) if entry else None
        if (fixed := _iso_time_x(bundles[r.name])):
            log.append(f"{r.name}: {fixed} time-axis lines had numeric x; converted to dates")
        if r.forcing_mismatch:
            log.append(f"{r.name}: Base Configuration says {r.base_configuration}, datastore path says {r.forcing}")
        run.update(has_data=bundles[r.name] is not None, start_year=_start_year(run, bundles[r.name]),
                   provenance=(bundles[r.name] or {}).get("provenance"), id=_id(r.name),
                   figshare_article=(entry or {}).get("figshare_article"), data_url=(entry or {}).get("url"))
        runs.append(run)
    names = {r["name"] for r in runs}
    # A figure where no line names the run but labelled lines name other listed runs is theirs, not this run's (a
    # notebook that failed to load this run but drew the others, e.g. a run missing from its hard-coded list): drop it.
    for name, bundle in bundles.items():
        if bundle:
            def foreign(rec):
                labels = [l["label"] for p in rec.get("panels", []) for l in p["lines"] if l["label"]]
                return (not any(name in lab for lab in labels)
                        and any(n != name and n in lab for lab in labels for n in names))
            dropped = [r for r in bundle.get("figures", []) if foreign(r)]
            if dropped:
                bundle["figures"] = [r for r in bundle["figures"] if r not in dropped]
                log.append(f"{name}: {len(dropped)} figure(s) show only other runs; left out for this run")
    settings = load_explorer_settings(paper)
    titles, pinned = settings["FIGURE_TITLES"], settings["RUN_COLOURS"]
    forcing = {r["name"]: r["forcing"] for r in runs}
    slots, notes = colour_slots([r["name"] for r in runs], pinned)
    log += notes
    for r in runs:
        r["colour_slot"] = slots[r["name"]]

    # A line that two or more runs take as their own, drawn identically (e.g. an unlabelled OM2 panel the
    # exporter credits to each run), is a comparison: show it once, as a reference. A run's line that other
    # runs' figures also draw (notebooks that overlay several runs) stays that run's.
    own_in: dict[str, set] = {}
    for name, bundle in bundles.items():
        for rec in (bundle or {}).get("figures", []):
            for i, p in enumerate(rec.get("panels", [])):
                for line in p["lines"]:
                    # ("all-lines-own" figures can hold lines labelled with another listed run: those are that run's)
                    if line["role"] == "own" and not any(n != name and n in line["label"] for n in names):
                        own_in.setdefault(f"{rec.get('key')}|{i}|{line['label']}|{_id(json.dumps([line['x'], line['y']]))}", set()).add(name)
    shared = {k for k, who in own_in.items() if len(who) > 1}

    figures: dict[str, dict] = {}
    refs: dict[str, dict] = {}
    has_own: set = set()
    for name, bundle in bundles.items():
        for rec in (bundle or {}).get("figures", []):
            if rec["status"] in ("empty", "no-own-lines"):
                log.append(f"{name}: {rec['png']} {rec['status']}")
            if not rec.get("key") or rec["status"] in ("empty", "no-lines", "no-own-lines"):
                continue
            fid = _id(rec["key"])
            panels = rec["panels"]
            fig = figures.setdefault(fid, {
                "id": fid, "key": rec["key"], "notebook": rec["notebook"],
                # the caption comes from one run's notebook; it is shown for every run
                "caption": rec.get("caption", "").replace(name, "each run"),
                "display": titles.get(Path(rec["png"]).stem) or (panels[0]["title"] if len(panels) == 1 and panels[0]["title"]
                                                                  else (rec["suptitle"] or rec["title_clean"] or rec["notebook"])),
                "subtitle": ", ".join(p["title"] for p in panels if p["title"]) if len(panels) > 1 else "",
                "x_kind": panels[0]["x_kind"],
                "panels": [{k: p[k] for k in ("title", "x_label", "y_label", "y_log")} for p in panels],
                "runs": {},
            })
            own = []
            for i, p in enumerate(panels):
                keep = []
                # the run's unlabelled line in the colour of its labelled one: the monthly values behind a running
                # mean (drawn thin in the notebook), shown faint and hidden with "Monthly values" off
                means = {line["color"] for line in p["lines"] if line["role"] == "own" and line["label"]}
                for line in p["lines"]:
                    fp = f"{rec['key']}|{i}|{line['label']}|{_id(json.dumps([line['x'], line['y']]))}"
                    if any(n != name and n in line["label"] for n in names):
                        continue  # labelled with another listed run, whatever its role here
                    if line["role"] == "own" and fp not in shared:
                        thin = line["style"] == "line" and not line["label"] and line.get("color") in means
                        keep.append({"name": _series_name(line["label"], name), "style": "thin" if thin else line["style"],
                                     "x": line["x"], "y": line["y"]})
                    elif line["role"] == "other" and any(n != name and n in line["label"] for n in names):
                        continue  # another listed run: its own bundle is the source
                    elif line["role"] != "own" and own_in.get(fp, set()) - {name}:
                        continue  # another run's own line (e.g. its unlabelled daily values)
                    elif i < len(fig["panels"]):
                        k = f"{fid}|{fp}"
                        refs.setdefault(k, {"panel": i, "kind": "points" if line["style"] == "points" else "line",
                                            "name": line["label"], "x": line["x"], "y": line["y"], "fid": fid,
                                            "notebook": rec["notebook"], "_runs": set(),
                                            **({"yerr": line["yerr"]} if line.get("yerr") else {}),
                                            **({"thin": True} if line["style"] == "thin" else {})})["_runs"].add(name)
                for mark in p["marks"]:
                    k = f"{fid}|{i}|{json.dumps(mark, sort_keys=True)}"
                    refs.setdefault(k, {"panel": i, **mark, "name": mark.get("label", ""), "fid": fid,
                                        "notebook": rec["notebook"], "_runs": set()})["_runs"].add(name)
                own.append({"lines": keep, "periods": p["periods"], "title_full": p["title_full"]})
            fig["runs"][name] = {"periods": rec["periods"], "png": rec["png"], "status": rec["status"],
                                 "fignum": rec["fignum"]}
            data = out / "data" / fid
            data.mkdir(parents=True, exist_ok=True)
            (data / f"{_id(name)}.json").write_text(json.dumps({"panels": own}, separators=(",", ":")))
            if any(p["lines"] for p in own):
                has_own.add(fid)

    for k, r in list(refs.items()):
        r.update(describe_reference(r, r.pop("notebook"), {forcing[n] for n in r.pop("_runs")}, settings["REFERENCE_SOURCES"]))
        if r.pop("drop", False):
            del refs[k]

    # A reference drawn twice under one name in a panel (e.g. ACCESS-OM2's monthly values behind its running mean,
    # both named after the running mean): all but the smoothest are the "thin" monthly values.
    def roughness(y):
        v = [a for a in y if isinstance(a, (int, float))]
        return sum(abs(b - a) for a, b in zip(v, v[1:])) / max(1, len(v) - 1)
    same: dict[tuple, list] = {}
    for r in refs.values():
        if r["kind"] == "line" and r["name"]:
            same.setdefault((r["fid"], r["panel"], r["name"]), []).append(r)
    for group in same.values():
        for r in sorted(group, key=lambda r: roughness(r["y"]))[1:]:
            r["thin"] = True

    # A figure where no run has a line of its own (e.g. one that shows only ACCESS-OM2) has nothing to compare.
    for fid in [fid for fid in figures if fid not in has_own]:
        log.append(f"{figures[fid]['key']}: no run has a line of its own; left out")
        del figures[fid]
        shutil.rmtree(out / "data" / fid, ignore_errors=True)
    merge_figures(figures, refs, out, settings["EXPLORER_MERGE"], log)
    for f in figures.values():
        f["display"], f["subtitle"] = retitle(f["display"], f["subtitle"], settings["FIGURE_TITLE_RULES"])
        f.update(variable_labels(f["display"]))

    for fid in figures:
        mine = [{k: v for k, v in r.items() if k != "fid"} for r in refs.values() if r["fid"] == fid]
        (out / "data" / fid / "refs.json").write_text(json.dumps(mine, separators=(",", ":")))

    issues, categories = load_issues(paper)
    commits = Counter((b or {}).get("provenance", {}).get("paper_commit") for b in bundles.values() if b)
    commit = next((c for c, _ in commits.most_common() if c), "main")
    notebooks = sorted({f["notebook"] for f in figures.values()})
    cats = []
    for cname, nbs in split_categories(categorise(notebooks, issues, categories), settings["EXPLORER_CATEGORIES"]).items():
        entries = [{"nb": nb, "issues": _md_links(issues.get(nb, "")),
                    "source": f"{PAPER_REPO}/blob/{commit}/notebooks/{nb}.ipynb",
                    "figures": sorted((f["id"] for f in figures.values() if f["notebook"] == nb),
                                      key=lambda i: min(r["fignum"] for r in figures[i]["runs"].values()))}
                   for nb in nbs]
        cats.append({"name": cname, "notebooks": [e for e in entries if e["figures"]]})

    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps({
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "runs": runs, "categories": [c for c in cats if c["notebooks"]], "figures": figures,
    }, separators=(",", ":")))
    missing = [r["name"] for r in runs if not r["has_data"]]
    log.append(f"{len(figures)} figures from {len(runs) - len(missing)} of {len(runs)} runs"
               + (f"; no data yet: {', '.join(missing)}" if missing else ""))
    return log


if __name__ == "__main__":
    md, paper_dir, out_dir = sys.argv[1:4]
    for line in build(Path(md).read_text(), Path(paper_dir), Path(out_dir), Path(".cache/figure-data")):
        print("[figure-explorer]", line)
