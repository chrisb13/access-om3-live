"""MkDocs hook: where the pencil ("Edit this page") at the top of each page leads.

Most pages are generated, so the pencil goes to whatever they are generated from; notebook pages have no pencil.
"""

def on_page_markdown(markdown, page, config, files):
    """Point the page's edit link at its source on GitHub, or remove it."""
    repo = config["repo_url"].rstrip("/")
    src = page.file.src_uri
    if src.endswith(".ipynb"):  # results of notebooks run on Gadi: nothing to edit here
        page.edit_url = None
    elif src == "index.md":  # the home page is pages/home.md
        page.edit_url = f"{repo}/edit/main/pages/home.md"
    elif src == "evaluation-summary.md":  # copied from access-om3-configs
        page.edit_url = "https://github.com/ACCESS-NRI/access-om3-configs/edit/main/documentation/docs/pages/Experiments.md"
    elif src.startswith("live/"):  # the monitor and the status pages come from experiments.yml and the nightly run
        page.edit_url = f"{repo}/edit/main/experiments.yml"
    return markdown
