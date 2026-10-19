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


def _start_year(run: dict, bundle: dict | None) -> int | None:
    m = re.search(r"\((\d{4})-\d{4}\)", run["length"])
    if m:
        return int(m.group(1))
    years = [int(x[:4]) for f in (bundle or {}).get("figures", []) for p in f.get("panels", [])
             if p.get("x_kind") == "time" for line in p["lines"] for x in line["x"][:1] if x]
    return min(years) if years else None


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
        if r.forcing_mismatch:
            log.append(f"{r.name}: Base Configuration says {r.base_configuration}, datastore path says {r.forcing}")
        run.update(has_data=bundles[r.name] is not None, start_year=_start_year(run, bundles[r.name]),
                   provenance=(bundles[r.name] or {}).get("provenance"), id=_id(r.name),
                   figshare_article=(entry or {}).get("figshare_article"))
        runs.append(run)
    names = {r["name"] for r in runs}

    # A line drawn identically in two or more runs' figures (e.g. an unlabelled OM2 panel) is a
    # comparison, not either run's own data: show it once, as a reference.
    seen_in: dict[str, set] = {}
    for name, bundle in bundles.items():
        for rec in (bundle or {}).get("figures", []):
            for i, p in enumerate(rec.get("panels", [])):
                for line in p["lines"]:
                    seen_in.setdefault(f"{rec.get('key')}|{i}|{line['label']}|{_id(json.dumps([line['x'], line['y']]))}", set()).add(name)
    shared = {k for k, who in seen_in.items() if len(who) > 1}

    figures: dict[str, dict] = {}
    refs: dict[str, dict] = {}
    for name, bundle in bundles.items():
        for rec in (bundle or {}).get("figures", []):
            if rec["status"] in ("empty", "no-own-lines"):
                log.append(f"{name}: {rec['png']} {rec['status']}")
            if not rec.get("key") or rec["status"] in ("empty", "no-lines", "no-own-lines"):
                continue
            fid = _id(rec["key"])
            panels = rec["panels"]
            fig = figures.setdefault(fid, {
                "id": fid, "key": rec["key"], "notebook": rec["notebook"], "caption": rec.get("caption", ""),
                "display": panels[0]["title"] if len(panels) == 1 and panels[0]["title"] else (rec["suptitle"] or rec["title_clean"] or rec["notebook"]),
                "subtitle": ", ".join(p["title"] for p in panels if p["title"]) if len(panels) > 1 else "",
                "x_kind": panels[0]["x_kind"],
                "panels": [{k: p[k] for k in ("title", "x_label", "y_label", "y_log")} for p in panels],
                "runs": {},
            })
            own = []
            for i, p in enumerate(panels):
                keep = []
                for line in p["lines"]:
                    fp = f"{rec['key']}|{i}|{line['label']}|{_id(json.dumps([line['x'], line['y']]))}"
                    if line["role"] == "own" and fp not in shared:
                        keep.append({"name": _series_name(line["label"], name), "style": line["style"],
                                     "x": line["x"], "y": line["y"]})
                    elif line["role"] == "other" and any(n != name and n in line["label"] for n in names):
                        continue  # another listed run: its own bundle is the source
                    elif i < len(fig["panels"]):
                        k = f"{fid}|{fp}"
                        refs.setdefault(k, {"panel": i, "kind": "points" if line["style"] == "points" else "line",
                                            "name": line["label"], "x": line["x"], "y": line["y"], "fid": fid})
                for mark in p["marks"]:
                    k = f"{fid}|{i}|{json.dumps(mark, sort_keys=True)}"
                    refs.setdefault(k, {"panel": i, **mark, "name": mark.get("label", ""), "fid": fid})
                own.append({"lines": keep, "periods": p["periods"], "title_full": p["title_full"]})
            fig["runs"][name] = {"periods": rec["periods"], "png": rec["png"], "status": rec["status"],
                                 "fignum": rec["fignum"]}
            data = out / "data" / fid
            data.mkdir(parents=True, exist_ok=True)
            (data / f"{_id(name)}.json").write_text(json.dumps({"panels": own}, separators=(",", ":")))

    for fid in figures:
        mine = [{k: v for k, v in r.items() if k != "fid"} for r in refs.values() if r["fid"] == fid]
        (out / "data" / fid / "refs.json").write_text(json.dumps(mine, separators=(",", ":")))

    issues, categories = load_issues(paper)
    commits = Counter((b or {}).get("provenance", {}).get("paper_commit") for b in bundles.values() if b)
    commit = next((c for c, _ in commits.most_common() if c), "main")
    notebooks = sorted({f["notebook"] for f in figures.values()})
    cats = []
    for cname, nbs in categorise(notebooks, issues, categories).items():
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
