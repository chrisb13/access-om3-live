## How it works

Each night, around midnight Canberra time, a GitHub Actions workflow in
[{repo}](https://github.com/{repo}) logs in to NCI's Gadi and checks every monitored experiment for
new output. For each experiment that has changed, it submits a Gadi job that re-runs the notebooks
from [access-om3-paper-1](https://github.com/ACCESS-Community-Hub/access-om3-paper-1); the others
keep the figures from their last run. The jobs run in parallel and the workflow waits for them, then
updates the site. A job still running after about five and a half hours is shown as re-running, and
its results appear the next night. The table above shows when each experiment
was last run, whether a job is running, and which notebooks failed.

## Monitoring a new experiment

Add an entry under `experiments:` in
[`experiments.yml`](https://github.com/{repo}/blob/main/experiments.yml) and open a pull request.
Only the archive folder is needed; the other keys are optional:

```yaml
experiments:
  # minimal: just the archive folder
  - /g/data/ol01/outputs/access-om3-25km/MC_25km_jra_iaf+wombatlite-test4-d28e0359
  # with optional keys
  - archive: /g/data/ol01/outputs/access-om3-8km/MC_8km_jra_ryf-test2-expt-c349f800
    run_dir: /g/data/tm70/cyb561/access-om3-runs/8k/MC_8km_jra_ryf-test2
    notebooks: [MLD, SST]
    description: One line on what this run is testing
```

The terms match the sections of each experiment's status page:

- **Archive folder** (`archive`, required): the folder holding `output000`, `output001`, …: where a
  payu control directory's `archive` link points (`readlink -f archive`), or its synced copy under
  `/g/data/ol01/outputs/`. Its name becomes the experiment's name here, so it must be unique.
- **Run directory** (`run_dir`, optional): the payu control directory, for the current or crashed
  run's PBS and model logs, and the git history of its settings (shown as "Settings changed during
  this run", the last section of the status page).
- **Notebooks** (`notebooks`, optional): the access-om3-paper-1 notebooks to run for this experiment,
  instead of the standard ones ({notebooks}).
- **Description** (`description`, optional): one line shown in the OM3 Monitor table and at the top
  of the status page. Without it, the first line of the description in the run's `metadata.yaml` is
  used.

The folders must be readable by the Gadi account that runs the nightly job. To stop monitoring an
experiment, remove its entry.
