# ACCESS-OM3 Evaluation

This website is the starting point for evaluating ACCESS-OM3 experiments. It brings together
quick-look diagnostics of runs that are still going and links to the full, permanent evaluation of
finished experiments.

It has two parts:

- **[OM3 Monitor](live/index.md)** follows selected simulations **while they are running**, so
  problems can be spotted early and runs compared as they progress. It shows figures from a small
  set of evaluation notebooks, re-run on the latest output of each monitored experiment. Each
  experiment also has a status page built from the run's own payu records: recent runs, the model
  date reached, throughput (simulated years per day) and the latest model and PBS logs. The OM3
  Monitor pages are regenerated nightly and replaced each time; nothing is archived. When an
  experiment stops being monitored, its pages disappear.
- **[Evaluation Summary](evaluation-summary.md)** is the permanent evaluation record. It lists the
  key ACCESS-OM3 experiments, one per row. To see an experiment's evaluation, click **Evaluation** in
  its row: that opens the fixed, versioned evaluation website for that run, with all its figures and
  notebooks. The table is taken directly from the
  [Experiments](https://access-om3-configs.access-hive.org.au/latest/Experiments/) page of the
  access-om3-configs documentation, and is refreshed whenever this site is rebuilt.

## How it works

Each night, around midnight Canberra time, a GitHub Actions workflow in
[{repo}](https://github.com/{repo}) logs in to NCI's Gadi and checks every monitored experiment for
new output. It re-runs the notebooks, from
[access-om3-paper-1](https://github.com/ACCESS-Community-Hub/access-om3-paper-1), only for
experiments that have changed; the others keep the figures from their last run. The
[OM3 Monitor](live/index.md) page shows when each experiment was last run and which notebooks failed.

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
  run's PBS and model logs.
- **Notebooks** (`notebooks`, optional): extra access-om3-paper-1 notebooks to run for this experiment,
  on top of the standard ones ({notebooks}).
- **Description** (`description`, optional): one line shown in the OM3 Monitor table and at the top
  of the status page. Without it, the first line of the description in the run's `metadata.yaml` is
  used.

The folders must be readable by the Gadi account that runs the nightly job. To stop monitoring an
experiment, remove its entry.
