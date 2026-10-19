#!/usr/bin/env bash
# Builds the whole site as a preview: everything except what needs the nightly Gadi run, so the OM3 Monitor pages are empty.
# Read the Docs runs this for pull request previews (see .readthedocs.yaml); you can run it too:
#     pip install -r requirements.txt && bash scripts/build_preview.sh && python3 -m http.server -d build/site
# Usage: scripts/build_preview.sh [output folder; default build/site]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-build/site}
PY=python; command -v python >/dev/null || PY=python3

# the Experiments page of access-om3-configs, which the Evaluation Summary and the Config Diff page are built from
curl -fsSL -o Experiments.md https://raw.githubusercontent.com/ACCESS-NRI/access-om3-configs/main/documentation/docs/pages/Experiments.md
# the theme comes from access-om3-paper-1 (only the folders the site needs)
rm -rf theme
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/ACCESS-Community-Hub/access-om3-paper-1.git theme
git -C theme sparse-checkout set documentation/overrides documentation/docs/assets documentation/docs/css documentation/docs/fontello
# Figure explorer: the paper's categories, and mkfigs at the commit paper-1 pins. (A cone-mode sparse checkout takes
# directories only; documentation/figure-data.json comes with documentation/overrides.)
mkdir -p theme/notebooks && git -C theme show HEAD:notebooks/mkfigs_issues.py > theme/notebooks/mkfigs_issues.py
MKFIGS_SHA=$(git -C theme ls-tree HEAD external/access-model-mkfigs | awk '{print $3}')
MKFIGS_URL=$(git config -f theme/.gitmodules submodule.external/access-model-mkfigs.url)
git init -q theme/external/access-model-mkfigs
git -C theme/external/access-model-mkfigs fetch -q --depth 1 "$MKFIGS_URL" "$MKFIGS_SHA"
git -C theme/external/access-model-mkfigs checkout -q FETCH_HEAD
mkdir -p content status
"$PY" scripts/build_site.py Experiments.md
"$PY" -m mkdocs build -f build/mkdocs.yml -d "$OUT"
