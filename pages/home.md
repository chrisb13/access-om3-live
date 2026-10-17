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
