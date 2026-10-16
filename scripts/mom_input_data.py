"""Build per-experiment MOM_input JSON for the diff page from the Experiments table's branches."""
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
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


def fetch(sha: str, fname: str) -> str:
    """Raw text of a file at a commit, or '' if absent."""
    try:
        return urllib.request.urlopen(f"https://raw.githubusercontent.com/{REPO}/{sha}/{fname}").read().decode()
    except urllib.error.HTTPError:
        return ""


def build(experiments_md: str, out: Path) -> None:
    """Write out/index.json and out/<slug>.json for every experiment branch, all at one set of branch tips."""
    out.mkdir(parents=True, exist_ok=True)
    heads = dict(reversed(l.split("\trefs/heads/")) for l in subprocess.run(
        ["git", "ls-remote", "--heads", f"https://github.com/{REPO}"], check=True, capture_output=True, text=True).stdout.splitlines())
    index = []
    for branch, group in experiments(experiments_md):
        sha = heads.get(branch)
        if not sha or not (text := fetch(sha, "MOM_input")):
            print(f"::warning::no MOM_input on branch {branch}", file=sys.stderr)
            continue
        params = {}
        parse(text, params, "MOM_input")
        parse(fetch(sha, "MOM_override"), params, "MOM_override")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", branch)
        (out / f"{slug}.json").write_text(json.dumps(params, separators=(",", ":")))
        index.append({"slug": slug, "label": branch, "group": group, "sha": sha})
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"MOM_input diff: {len(index)} experiments at current branch tips")


if __name__ == "__main__":
    build(Path(sys.argv[1]).read_text(), Path(sys.argv[2]))
