<style>.md-sidebar--secondary:not([hidden]) { display: none }</style>

# Figure explorer

Overlay any line-plot figure from the runs on the [Experiments](evaluation-summary.md) page. Each run's
data was saved by the notebooks when they drew its evaluation figures, so the lines are exactly what the
notebook plotted. The [evaluation pages](evaluation-summary.md) (each run's **Evaluation** link) stay the
reference for a run; this page is for comparing runs.

Not every figure has every run: a run has a figure only when its notebook drew it (a notebook can fail for a
run, or not apply to RYF runs); runs without it are shown with a dashed outline. Each figure has **Use this
figure** (a Markdown summary of what you are looking at and where its data is, a short Python script to plot
it, and a PNG export) and **Run details** (configuration, model build, where the data came from). Hover a
legend entry for a run's or comparison line's data source. Problems or ideas: see
[Contributing and feedback](contributing.md).

<div id="fx-app" data-base="../assets/figure_explorer/" data-evaluation="../evaluation-summary/" data-contributing="../contributing/">Loading…</div>
