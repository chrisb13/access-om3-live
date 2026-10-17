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
| `mkdocs.yml` | Website settings, copied from access-om3-paper-1 |

A run is skipped when it has no new `.nc` files since its last run and the notebooks have not
changed. The site is only rebuilt when something ran, the list of runs changed, or the
[Experiments page](https://access-om3-configs.access-hive.org.au/latest/Experiments/) changed.
Use **Run workflow → force** to re-run everything.

## Set-up

Repository secrets: `GADI_USER`, `GADI_KEY` (and `GADI_KEY_PASSPHRASE` if the key has one),
`GADI_WORK_DIR` (a folder on Gadi the service user can write to).
Settings → Pages → Source: **GitHub Actions**.
