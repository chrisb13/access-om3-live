# access-om3-live

Nightly evaluation website for ACCESS-OM3 runs in progress. Each night, GitHub Actions
logs in to Gadi, re-runs a few [access-om3-paper-1](https://github.com/ACCESS-Community-Hub/access-om3-paper-1)
notebooks for every run listed in `experiments.yml` that has new output, and publishes the
latest figures and rendered notebooks to GitHub Pages. Nothing is kept from previous nights.

Started from [COSIMA-recipes-workflow](https://github.com/ACCESS-NRI/COSIMA-recipes-workflow)
(commit 7024289); its SSH set-up step is reused unchanged.

## Bring a run in or out of focus

Edit `experiments.yml`: add or remove the full path to a run's archive folder
(on `/scratch` or `/g/data`). The folder name is shown as the experiment name, and
the folder's project is added to the PBS storage flags automatically.

## How it works

| File | What it does |
|---|---|
| `.github/workflows/live.yml` | Midnight (or manual) run: submit a Gadi job per changed experiment, wait for them (up to 5 h 20 min), copy the results back, build and publish the site |
| `gadi/submit.sh` | On a Gadi login node: update the paper repo, pick runs with new `.nc` output, `qsub` the job |
| `gadi/mkfigs_live.sh` | The PBS job (one per experiment): builds a temporary datastore, runs `plot_sypd.py` and `mkfigs.run` (as `mkfigs.sh` does), then copies the results to `results/` |
| `gadi/run_status.py` | On the login node: snapshot each run's payu records (job history, latest log tails) for its status page |
| `scripts/build_site.py` | Lays out the pages and menu; the theme files come from access-om3-paper-1 |
| `scripts/config_diff_data.py`, `pages/config_diff.*`, `pages/config-diff.md` | The **Config Diff** page: compares any two experiments of the Experiments table (the MOM6 parameters that differ, and how each one's settings changed during its runs); the data is read from `access-om3-experiments` at build time |
| `mkdocs.yml` | Website settings, copied from access-om3-paper-1 |

A run is skipped when it has no new `.nc` files since its last run and the notebooks have not
changed. The site is only rebuilt when something ran, the list of runs changed, or the
[Experiments page](https://access-om3-configs.access-hive.org.au/latest/Experiments/) changed.
Use **Run workflow → force** to re-run everything.

## Previewing a pull request

Read the Docs builds a preview of every pull request (see `.readthedocs.yaml`); the link is in a comment on the pull
request. It has everything except the OM3 Monitor's results, which need the nightly Gadi run. To build the same preview
yourself:

```bash
pip install -r requirements.txt
bash scripts/build_preview.sh
python3 -m http.server -d build/site     # then open http://localhost:8000/
```

## Set-up

Repository secrets: `GADI_USER`, `GADI_KEY` (and `GADI_KEY_PASSPHRASE` if the key has one),
`GADI_WORK_DIR` (a folder on Gadi the service user can write to).
Settings → Pages → Source: **GitHub Actions**.

## Figure explorer

`pages/figure-explorer.md` overlays the line-plot figures of every run on the
[Experiments page](https://access-om3-configs.access-hive.org.au/latest/Experiments/).
At build time `scripts/figure_explorer_data.py` reads that page with mkfigs' own
parser (fetched at the commit access-om3-paper-1 pins), takes each run's data
bundle from paper-1's `documentation/figure-data.json` index, downloads it from
Figshare, checks its sha256, and writes the page's data under
`assets/figure_explorer/`. Bundles are cached in `.cache/figure-data`; a run whose
download fails keeps its last good copy. Tests:
`PYTHONPATH=theme/external/access-model-mkfigs/src python -m pytest tests`.
