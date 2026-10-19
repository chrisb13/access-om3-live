"""The figure explorer's data step, fed with bundles made by mkfigs' own exporter.

Needs mkfigs with the figure-explorer code (access-om3-paper-1's submodule, on PYTHONPATH);
skipped otherwise.
"""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mkfigs.experiments")
responses = pytest.importorskip("responses")
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mkfigs
import numpy as np
from mkfigs import figuredata, linedata

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import figure_explorer_data as fed

T4 = "MC_25km_jra_iaf+wombatlite-test4-d28e0359"
T3V2 = "MC_25km_jra_iaf+wombatlite-test3v2-00532b88"
RYF = "MC_25km_jra_ryf+wombatlite-test3-f4d79e82"
EXPERIMENTS = f"""## Key experiments

| Experiment | Evaluation | Base Configuration | Date completed | Description | Model build | Length (years) | ESM Datastore |
|---|---|---|---|---|---|---|---|
| [{T4}](https://x/a) | [link](https://access-om3-paper-1.readthedocs.io/t4/) | dev-MC_25km_jra_iaf+wombatlite | 1-Jul-26 | test4 | 2026.05.002 | 66 years (1958-2023) | `/g/data/ol01/outputs/access-om3-25km/{T4}/datastore.json` |
| [{T3V2}](https://x/b) | [link](https://access-om3-paper-1.readthedocs.io/t3v2/) | dev-MC_25km_jra_iaf+wombatlite | 7-Apr-26 | test3v2 | 2025.08.003 | 66 years (1958-2023) | `/g/data/ol01/outputs/access-om3-25km/{T3V2}/datastore.json` |
| [{RYF}](https://x/c) | [link](https://access-om3-paper-1.readthedocs.io/t3/) | dev-MC_25km_jra_iaf+wombatlite | 26-Jun-26 | RYF | 2026.05.002 | 31 years (1900-1930) | `/g/data/ol01/outputs/access-om3-25km/{RYF}/experiment_datastore.json` |
"""


def _drake(run, start):
    t = np.arange(f"{start}-01", f"{start + 3}-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(160, 175, t.size), color="C3", alpha=0.3)
    ax.plot(t, np.linspace(161, 174, t.size), color="C3", label=f"ACCESS-OM3 {run}")
    if run == T4:  # test4's figure also draws test3v2, as the real StraitTransports does
        ax.plot(t, np.linspace(150, 155, t.size), color="C4", label=f"ACCESS-OM3 {T3V2}")
    om2 = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    ax.plot(om2, np.linspace(140, 145, om2.size), color="C0", label="ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1 sans GM")
    ax.axhline(173.3, color="k", ls="--", label="Donohue et al. (2016)")
    ax.axvspan(np.datetime64("2007-01-01"), np.datetime64("2011-12-31"), color="grey", alpha=0.2)
    ax.set_title("Drake Passage transport")
    ax.set_ylabel("Transport (Sv)")
    return fig


def _bundle(tmp: Path, run: str, start: int) -> bytes:
    mdfol = tmp / run / "mkmd"
    mdfol.mkdir(parents=True)
    rec = linedata.capture(_drake(run, start), run=run, notebook="StraitTransports", fignum=12, title="Strait Transports",
                           caption="Drake Passage transport. [GitHub issue: Drake](https://github.com/x/1)",
                           png="StraitTransports_12.png", seen=set())
    linedata.write(rec, mdfol / "StraitTransports_12.lines.json")
    prov = {"paper_commit": "53c08d0c73db56c2b109abdced5f55b483cdc34b", "mkfigs_commit": "4c5b4d8", "generated_at": "2026-10-18T00:00:00+00:00"}
    return figuredata.build_bundle(mdfol, run, tmp / run, prov).read_bytes()


@pytest.fixture
def paper(tmp_path):
    root = tmp_path / "paper"
    (root / "external" / "access-model-mkfigs").mkdir(parents=True)
    (root / "external" / "access-model-mkfigs" / "src").symlink_to(Path(mkfigs.__file__).resolve().parents[1])
    (root / "notebooks").mkdir()
    (root / "notebooks" / "mkfigs_issues.py").write_text(
        'ISSUES = {"StraitTransports": "[#1](https://github.com/x/1) Drake Passage"}\n'
        'CATEGORIES = {"Temperature and salinity structure": [3], "Circulation": [1, 16]}\n')
    return root


def _publish(paper: Path, tmp: Path, mock, bundles: dict) -> None:
    runs = {}
    for run, blob in bundles.items():
        url = f"https://ndownloader.figshare.com/files/{abs(hash(run)) % 10**8}"
        mock.add(responses.GET, url, body=blob)
        runs[run] = {"url": url, "sha256": __import__("hashlib").sha256(blob).hexdigest(), "figshare_article": 33093320}
    (paper / "documentation").mkdir(exist_ok=True)
    (paper / "documentation" / "figure-data.json").write_text(json.dumps({"schema": 1, "runs": runs}))


def test_runs_lines_and_references_end_to_end(paper, tmp_path):
    out, cache = tmp_path / "site", tmp_path / "cache"
    with responses.RequestsMock() as mock:
        _publish(paper, tmp_path, mock, {T4: _bundle(tmp_path, T4, 1958), RYF: _bundle(tmp_path, RYF, 1900)})
        log = fed.build(EXPERIMENTS, paper, out, cache)
    index = json.loads((out / "index.json").read_text())
    runs = {r["name"]: r for r in index["runs"]}
    assert runs[RYF]["forcing"] == "RYF" and runs[RYF]["forcing_mismatch"] and runs[RYF]["start_year"] == 1900
    assert runs[T4]["has_data"] and not runs[T3V2]["has_data"] and runs[T4]["figshare_article"] == 33093320
    assert any("no data yet" in line and T3V2 in line for line in log)
    (fid,) = index["figures"]
    fig = index["figures"][fid]
    assert fig["display"] == "Drake Passage transport" and set(fig["runs"]) == {T4, RYF}
    assert index["categories"][0]["name"] == "Circulation"
    own = json.loads((out / "data" / fid / f"{runs[T4]['id']}.json").read_text())["panels"][0]["lines"]
    assert [(line["style"], line["name"]) for line in own] == [("thin", ""), ("line", "")]  # test3v2's line dropped
    refs = json.loads((out / "data" / fid / "refs.json").read_text())
    kinds = sorted((r["kind"], r["name"]) for r in refs)
    assert kinds == [("hline", "Donohue et al. (2016)"), ("line", "ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1 sans GM"),
                     ("span", "")]  # each once, although both runs' figures draw them


def test_a_failed_download_keeps_the_last_good_copy(paper, tmp_path):
    out, cache = tmp_path / "site", tmp_path / "cache"
    blob = _bundle(tmp_path, T4, 1958)
    with responses.RequestsMock() as mock:
        _publish(paper, tmp_path, mock, {T4: blob})
        fed.build(EXPERIMENTS, paper, out, cache)
    (cache / next(p.name for p in cache.glob("*.json.gz"))).rename(cache / "kept.json.gz")
    (cache / "last-good.json").write_text(json.dumps({T4: "kept"}))
    with responses.RequestsMock() as mock:
        index = json.loads((paper / "documentation" / "figure-data.json").read_text())
        mock.add(responses.GET, index["runs"][T4]["url"], status=503)
        log = fed.build(EXPERIMENTS, paper, out, cache)
    assert any("last good copy" in line for line in log)
    assert {r["name"]: r for r in json.loads((out / "index.json").read_text())["runs"]}[T4]["has_data"]


def test_a_bundle_that_does_not_match_its_checksum_is_not_shown(paper, tmp_path):
    out, cache = tmp_path / "site", tmp_path / "cache"
    with responses.RequestsMock() as mock:
        _publish(paper, tmp_path, mock, {T4: _bundle(tmp_path, T4, 1958)})
        index_path = paper / "documentation" / "figure-data.json"
        index = json.loads(index_path.read_text())
        index["runs"][T4]["sha256"] = "0" * 64
        index_path.write_text(json.dumps(index))
        log = fed.build(EXPERIMENTS, paper, out, cache)
    assert any("checksum differs" in line for line in log)
    assert json.loads((out / "index.json").read_text())["figures"] == {}


def test_lines_identical_in_several_runs_become_one_reference(paper, tmp_path):
    """An unlabelled comparison panel (as in the sea-ice budget) is credited to each run by the
    exporter's single-run rule; the build sees it in both runs and shows it once instead."""
    def budget(run):
        fig, (a, b) = plt.subplots(1, 2)
        m = np.arange(1, 13)
        a.plot(m, np.sin(m) * (1.1 if run == T4 else 0.9), label="Total freeze")
        b.plot(m, np.cos(m), label="Total freeze")  # OM2, identical in every run's figure
        a.set_title("a) ACCESS-OM3"); b.set_title("b) ACCESS-OM2")
        return fig
    blobs = {}
    for run in (T4, T3V2):
        md = tmp_path / run / "mkmd"; md.mkdir(parents=True)
        linedata.write(linedata.capture(budget(run), run=run, notebook="SeaIce_mass_budget_climatology", fignum=1,
                                        title="Sea Ice Mass Budget", caption="", png="b_01.png", seen=set()), md / "b_01.lines.json")
        blobs[run] = figuredata.build_bundle(md, run, tmp_path / run, {}).read_bytes()
    out = tmp_path / "site"
    with responses.RequestsMock() as mock:
        _publish(paper, tmp_path, mock, blobs)
        fed.build(EXPERIMENTS, paper, out, tmp_path / "cache")
    index = json.loads((out / "index.json").read_text())
    (fid,) = index["figures"]
    runs = {r["name"]: r for r in index["runs"]}
    own = json.loads((out / "data" / fid / f"{runs[T4]['id']}.json").read_text())["panels"]
    assert [len(p["lines"]) for p in own] == [1, 0]
    refs = json.loads((out / "data" / fid / "refs.json").read_text())
    assert [(r["panel"], r["name"]) for r in refs] == [(1, "Total freeze")]
