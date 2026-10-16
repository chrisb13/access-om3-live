"""Assemble build/ (docs tree + nav) from Gadi outputs in content/, theme/, pages/ and Experiments.md."""
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT, THEME, BUILD = ROOT / "content", ROOT / "theme" / "documentation", ROOT / "build"
CONFIGS_SITE = "https://access-om3-configs.access-hive.org.au/latest/"
REPO = os.environ.get("GITHUB_REPOSITORY", "ACCESS-NRI/access-om3-live")  # set by GitHub Actions


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
    for path in cfg["experiments"]:
        e = Path(path).name  # ENAME = name of the archive folder
        src, pages = CONTENT / e, []
        for nb in cfg["notebooks"]:
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
        ran = (datetime.fromtimestamp(log.stat().st_mtime, timezone.utc).astimezone(ZoneInfo("Australia/Canberra"))
               .strftime("%Y-%m-%d %H:%M %Z") if log.exists() else "not run yet")
        shown = "<br>".join(f"[{nb}]({e}/{nb}/index.md)" for p in pages for nb in p)
        rows.append(f"| {e} | {ran} | {shown or '–'} | {'<br>'.join(failed) or '–'} |")

    (docs / "live").mkdir(exist_ok=True)
    (docs / "live" / "index.md").write_text(
        # Hide the empty "On this page" column so the table gets the room (as the Experiments page does).
        "<style>.md-sidebar--secondary:not([hidden]) { display: none }</style>\n\n"
        "# OM3 Monitor\n\nNotebooks re-run nightly on Gadi for runs in progress; an experiment is only re-run "
        "when it has new output. Times are Canberra time.\n\n| Experiment | Last run | Notebooks shown | Failed |\n|---|---|---|---|\n"
        + "\n".join(rows) + "\n")

    nav = [{"Home": "index.md"}, {"OM3 Monitor": live_nav}, {"Evaluation Summary": "evaluation-summary.md"}]
    (BUILD / "mkdocs.yml").write_text(yaml.safe_dump({"INHERIT": "../mkdocs.yml", "nav": nav}, sort_keys=False))


if __name__ == "__main__":
    main(sys.argv[1])
