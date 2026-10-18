# Contributing and feedback

This site is new and still changing, and feedback from people looking at the runs is the most useful
thing it can get.

## Reporting a problem or suggesting an improvement

[Open a GitHub issue]({issue_url}): the link starts one with a short template to fill in. Please include
the link to the page you were looking at and, for a run, the experiment's name. Anything is welcome: a
number that looks wrong or is confusing, a missing figure, a failing notebook, a layout that doesn't
work, or an idea for something that would help.

## Contributing changes

Everything that builds this site is in [{repo}](https://github.com/{repo}). The main ways to contribute:

- **Monitor an experiment:** add its archive folder to `experiments.yml` and open a pull request; see
  [Monitoring a new experiment](live/index.md#monitoring-a-new-experiment).
- **Change a notebook or add a figure:** the notebooks come from
  [access-om3-paper-1](https://github.com/ACCESS-Community-Hub/access-om3-paper-1). Changes go there as
  a pull request; once merged, the commit in `paper_ref` in `experiments.yml` is updated to use them.
- **Change the text of a page:** the pencil at the top of each page opens the file it is built from on
  GitHub; this page, for example, is `pages/contributing.md`.
- **Change how the site works:** `scripts/build_site.py` builds the pages, `gadi/` holds what runs on
  Gadi, and `.github/workflows/live.yml` runs it all each night. Small, focused pull requests are the
  easiest to review.

Scientific feedback on a finished experiment's evaluation is welcome too: see the
[Evaluation Summary](evaluation-summary.md) for each experiment's evaluation and how to get involved.
