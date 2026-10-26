"""Shell snippets that let a reader re-check, on Gadi, what a status page states.

Each function returns the text of one snippet. A snippet starts with a plain-language note on what it does, sets
A (the payu archive folder) and R (the control directory) as the page does, and prints what the page shows. The
python parts need only Python 3.6, which the Gadi login nodes have.
"""
import shlex
import textwrap


def _note(text: str) -> list:
    """A paragraph as '# ' comment lines."""
    return textwrap.wrap(text, 100, initial_indent="# ", subsequent_indent="# ")


def _vars(archive=None, run_dir=None) -> list:
    """The 'A=...' and 'R=...' lines that tell the snippet which folders to look at."""
    return ([f"A={shlex.quote(archive)}"] if archive else []) + ([f"R={shlex.quote(run_dir)}"] if run_dir else [])


# Reads payu's run records into the list `runs`. Used by three snippets.
READ_RUNS = '''import glob, json, os, sys

# payu keeps one small file for every run: payu_jobs/<number>/run/<job>.json. Read them all.
archive = sys.argv[1]                  # the archive folder, handed over by the shell line above
runs = []
for path in glob.glob(archive + "/payu_jobs/*/run/*.json"):
    try:
        with open(path) as f:
            record = json.load(f)
        record["file"] = path          # remember which file it came from
        runs.append(record)
    except (OSError, ValueError):
        pass                           # skip a file we may not read, or that is not valid JSON

# Put the runs in order: by run number, and by start time if a run was tried more than once.
runs.sort(key=lambda r: (int(r.get("payu_current_run") or 0), r.get("timings", {}).get("payu_start_time") or ""))
'''


def _python(body: str) -> list:
    """A python3 heredoc that reads the run records, then runs body."""
    return ['python3 - "$A" <<\'EOF\'', *READ_RUNS.splitlines(), "", *body.splitlines(), "EOF"]


def model_date(run_dir: str) -> str:
    """The last 'Model Date' line of the current run's mediator log."""
    return "\n".join(
        _note("WHAT THIS DOES: prints the last 'Model Date' line of the current run's log. The page takes the latest "
              "model date, and the speed of the run (the 'rate', in simulated years per day), from this line.")
        + _vars(run_dir=run_dir)
        + ["", "# grep keeps the lines that contain 'Model Date:'; tail -n 1 keeps only the last of them",
           "grep 'Model Date:' \"$R\"/work/log/med.log | tail -n 1"])


def log_files(paths: list) -> str:
    """When each log file was last written, and the last lines the page shows."""
    return "\n".join(
        _note("WHAT THIS DOES: for each log file listed above, prints when it was last changed and its last 30 lines, "
              "which are the lines the page shows.")
        + ["", "# 'for' repeats the three lines below once for each file; $f is the file it is on.",
           "# 'ls -l' lists a file with details; --time-style=long-iso writes its time as 2026-07-01 00:02.",
           "for f in " + " ".join(shlex.quote(p) for p in paths) + "; do",
           '    echo "== $f"                          # which file this is',
           '    ls -l --time-style=long-iso "$f"      # when it was last changed (the date and time are in the middle)',
           '    tail -n 30 "$f"                       # its last 30 lines',
           "done"])


def config_files(run_dir: str, names: list) -> str:
    """When each configuration file shown on the page was last modified."""
    return "\n".join(
        _note("WHAT THIS DOES: prints when each configuration file shown above was last changed (the 'modified' time "
              "in each title).")
        + _vars(run_dir=run_dir)
        + ["", "# 'cd' goes into the control directory, where these files are",
           'cd "$R"',
           "# 'ls -l' lists files with details; --time-style=long-iso writes the time as 2026-07-01 00:02",
           "ls -l --time-style=long-iso " + " ".join(names)])


def truncations(archive: str, run_dir: str = None, work: bool = False, example: str = None) -> str:
    """Per-output truncation counts and max CFL, record counts by 1-degree box, PEs at the record cap, settings."""
    where = '"$R"/work' if work else '"$A"/output[0-9]*'
    files = '"$R"/work/[UV]_velocity_truncations' if work else '"$A"/output[0-9]*/[UV]_velocity_truncations'
    folder = "work folder" if work else "output folder"
    lines = _note("WHAT THIS DOES: re-checks the 'Truncation locations' section from the files MOM6 wrote. A truncation is "
                  "when MOM6 had to cut a velocity that was too fast (its CFL number was above 0.5): a sign of numerical "
                  "trouble. The steps below are separate, so you can paste just the one you need. Each prints something "
                  "you can compare with the page.")
    lines += _vars(None if work else archive, run_dir)
    lines += ["", f"# Step 1: for each {folder}: how many truncations MOM6 reported, the largest CFL number, and how many "
                  "location records were written", f"for o in {where}; do",
              "    # ocean.stats is a table with one line per ENERGYSAVEDAYS (usually once per model day), not per time step.",
              "    # Its 3rd column ('Truncs') is the number of truncations since the previous line, so awk adds it up to get the",
              "    # total. (-F, means the columns are separated by commas. The header line is skipped because its first",
              "    # column is not a number.)",
              """    total=$(awk -F, '$1+0 == $1 {sum += $3} END {print sum+0}' "$o/ocean.stats" 2>/dev/null)""",
              "    # The 5th column holds two numbers; the second is the CFL at the moment that line was written (a snapshot,",
              "    # not the peak over the interval). awk keeps the largest of these snapshots.",
              """    cfl=$(awk -F, '$1+0 == $1 {split($5, a, " "); if (a[2] > max) max = a[2]} END {if (max != "") printf "%.2f", max}' "$o/ocean.stats" 2>/dev/null)""",
              "    # Every truncation also gets a 'location record' in the U and V files. grep -c counts the matching lines.",
              """    u=$(grep -c 'velocity violation' "$o/U_velocity_truncations" 2>/dev/null)""",
              """    v=$(grep -c 'velocity violation' "$o/V_velocity_truncations" 2>/dev/null)""",
              "    # ${x:--} means: use x, or a dash if x is empty (no such file)",
              """    echo "$(basename "$o"): truncations ${total:--}, max CFL ${cfl:--}, location records U/V ${u:--}/${v:--}\"""",
              "done",
              "",
              "# Step 2: where it happens. Each record contains the place, like '( 73.27 E 67.89 N)'. This counts the records in",
              "# boxes one degree wide (negative longitude means west), busiest first. The pipeline does this:",
              "#   grep: keep the lines that start a record    sed: keep only the longitude and latitude",
              "#   awk: round them to whole degrees            sort | uniq -c: count the records in each box",
              "#   sort -rn | head: busiest boxes first",
              f"grep -h 'velocity violation' {files} 2>/dev/null \\",
              """  | sed -E 's/.*\\( *(-?[0-9.]+) E +(-?[0-9.]+) N\\).*/\\1 \\2/' \\""",
              """  | awk '{printf "%4.0fE %4.0fN\\n", $1, $2}' \\""",
              "  | sort | uniq -c | sort -rn | head -n 15",
              "",
              "# Step 3: MOM6 writes at most 50 records per PE (one of the parallel processes) in one run, then stops. A PE with",
              "# 50 records may have truncated more often than the records show. This lists the PEs that reached 50.",
              f"for f in {files}; do",
              '    [ -f "$f" ] || continue                 # skip if there is no such file',
              "    # each record says which PE it came from ('violation at 6073:'); count the records per PE, keep PEs with 50",
              """    busy=$(grep -h 'velocity violation' "$f" | sed -E 's/.*violation at ([0-9]+):.*/PE\\1/' | sort | uniq -c | awk '$1 >= 50 {printf "%s (%d records) ", $2, $1}')""",
              """    echo "$(basename "$(dirname "$f")")/$(basename "$f"): $busy\"""",
              "done"]
    n = 4
    if example:
        lines += ["", f"# Step {n}: one record in full. The page reads the speed (u, v), the CFL number, the surface height (e) and the",
                  f"# depth (D) from lines like these.", f"head -n 30 {example}"]
        n += 1
    if archive and not work:
        lines += ["", f"# Step {n}: the model period of each output folder, from payu's run records (output NNN is run NNN)"]
        lines += _python('for r in runs:\n    run, finish = r.get("payu_current_run"), r.get("model_finish_time")\n'
                         '    if str(run).isdigit() and finish:      # only runs that finished have a model period\n'
                         '        start = r.get("model_start_time") or "?"      # payu did not record the start of the first run\n'
                         '        print("output%03d  %s -> %s" % (int(run), start[:7], finish[:7]))')
        n += 1
    if run_dir:
        lines += ["", f"# Step {n}: the settings that decide when MOM6 truncates (CFL_TRUNCATE) and when it stops the run (MAXTRUNC).",
                  "# Each is shown as it is set in MOM_input and in MOM6's own record of every setting, which also gives its default.",
                  """grep -hE '^(CFL_TRUNCATE|MAXTRUNC) ' "$R"/MOM_input "$R"/docs/MOM_parameter_doc.all"""]
    return "\n".join(lines)


def archive_counts(archive: str) -> str:
    """Output folder count and latest, runs recorded by payu and how many failed, and the model date reached."""
    lines = _note("WHAT THIS DOES: re-counts the 'Archive folder' list: how many output folders there are, which is the "
                  "latest, how many runs payu recorded, how many of those failed, and how far the model got. The four "
                  "lines it prints should match the page.")
    lines += _vars(archive)
    lines += ["", "# Step 1: count the output folders (output000, output001, ...) and name the last one",
              'ls -d "$A"/output[0-9]* | wc -l', 'basename "$(ls -d "$A"/output[0-9]* | tail -n 1)"', "",
              "# Step 2: read payu's run records with a few lines of python, and count them"]
    return "\n".join(lines + _python(
        '# a run failed if payu\'s exit status for it is not 0 (a run with no status yet is still going)\n'
        'failed = [r for r in runs if r.get("payu_run_status") not in (0, None)]\n'
        'print(len(runs), "runs recorded by payu,", len(failed), "failed")\n'
        '# the model date reached is the finish date of the last run that has one\n'
        'finished = [r for r in runs if r.get("model_finish_time")]\n'
        'print("model date reached:", finished[-1]["model_finish_time"][:10] if finished else "unknown")'))


def recent_runs(archive: str) -> str:
    """The Recent runs table: the last ten payu run records, newest first."""
    lines = _note("WHAT THIS DOES: prints the 'Recent runs' table: the 10 newest runs, newest first. 'ok' means payu's exit "
                  "status was 0. SYPD is simulated years per day: the model time the run covered, divided by the wall-clock "
                  "time it took. (If payu did not record a run's start date this shows ?; the page may fill it in from the "
                  "previous run.)")
    lines += _vars(archive) + [""]
    return "\n".join(lines + _python(
        'import time\nfrom datetime import datetime\n\n'
        'print("Run  PBS job             Result     Model period              Hours  SYPD   Record last modified")\n'
        'for r in reversed(runs[-10:]):                     # the last 10 runs, newest first\n'
        '    status = r.get("payu_run_status")\n'
        '    result = "ok" if status == 0 else ("failed" if status is not None else str(r.get("stage")))\n'
        '    start, finish = r.get("model_start_time"), r.get("model_finish_time")\n'
        '    seconds = r.get("timings", {}).get("payu_total_duration_seconds")     # wall-clock seconds the run took\n'
        '    hours = "%.1f" % (seconds / 3600) if seconds else ""\n'
        '    sypd = ""\n'
        '    if start and finish and seconds:\n'
        '        # the model time the run covered, in whole days (dates look like 1958-01-01T00:00:00)\n'
        '        days = (datetime.strptime(finish[:19], "%Y-%m-%dT%H:%M:%S") - datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S")).days\n'
        '        sypd = "%.2f" % (days / 365.25 / (seconds / 86400))     # model years divided by wall-clock days\n'
        '    changed = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(os.path.getmtime(r["file"])))\n'
        '    print("%-4s %-18s %-10s %-10s -> %-10s %6s %6s  %s" % (r.get("payu_current_run"), r.get("scheduler_job_id"), result,\n'
        '          (start or "?")[:10], (finish or "?")[:10], hours, sypd, changed))'))


def throughput(archive: str) -> str:
    """The rate (simulated years per day) that each output's mediator log reports, which the figure plots."""
    return "\n".join(
        _note("WHAT THIS DOES: for each output folder, finds the model's speed in its med.log (the mediator's log) and "
              "averages it. The speed, called the 'rate', is in simulated years per day (ypd). The figure plots these "
              "rates. You get one line per output folder: how many rate lines it had, and their average.")
        + _vars(archive)
        + ["", 'for o in "$A"/output[0-9]*; do',
           '    [ -f "$o/log/med.log" ] || continue            # skip folders that have no med.log',
           "    # A line looks like: Model Date: 1958-09-04T00:00:00 ... rate = 7.09 ypd. awk reads the number just before",
           "    # 'ypd' (the second-last word), adds them up and counts them, then prints the average at the end.",
           """    awk -v name="$(basename "$o")" '/Model Date:/ && /ypd/ {count++; total += $(NF-1)} END {if (count) printf "%s: %d rate lines, mean %.2f ypd\\n", name, count, total/count}' "$o/log/med.log\"""",
           "done"])


SETTINGS_FILES = "config.yaml MOM_input MOM_override nuopc.runconfig ice_in"
SETTINGS_HOW_TO_READ = ("The page shows these as setting values, so edits to comments or formatting do not appear there, and paths, "
                        "layout, output and job settings are under its Housekeeping note. In the list of edits, a line starting "
                        "with '-' was removed and a line starting with '+' was added.")


def _settings_steps(git: str) -> list:
    """Steps 2 to 4 of both settings checks (the run directory's and the branch's); *git* is how to call git there."""
    return ["# Step 2: payu makes a git commit each time it starts a run, with a message ending in 'Run <number>'.",
            "# This counts them. A run that crashed and was redone has two, and the page counts it once.",
            f"{git} log --first-parent --format=%s | grep -c ': Run [0-9]*$'",
            "# These are the redone runs: how many run commits each has, then the run (prints nothing if none).",
            f"{git} log --first-parent --format=%s | grep -o 'Run [0-9]*$' | sort | uniq -c -d",
            "",
            "# Step 3: find the first run commit (the history starts there)",
            f"FIRST=$({git} log --first-parent --reverse --format='%H %s' | grep -m1 ': Run [0-9]*$' | cut -d' ' -f1)",
            "",
            "# Step 4: every edit to the settings files after that, oldest first. Each edit starts with a line like",
            "# '=== <commit> <date> <message>', followed by what changed.",
            f"{git} log -p --first-parent --reverse --format='=== %h %cI %s' \"$FIRST\"..HEAD -- {SETTINGS_FILES}"]


def settings_changes(run_dir: str) -> str:
    """The current build, the run count, and every edit to the tracked settings files since the first run commit."""
    return "\n".join(
        _note("WHAT THIS DOES: checks the 'Settings changed during this run' section. It prints the current build from "
              "config.yaml, counts payu's run commits, and lists every edit made to the settings files since the first run, "
              "using git (the history of changes kept in the control directory). " + SETTINGS_HOW_TO_READ)
        + _vars(run_dir=run_dir)
        + ["", "# Step 1: the current build: the 'model' and 'exe' lines of config.yaml, and its 'modules:' block",
           "# (sed prints the lines from 'modules:' down to the next empty line)",
           "grep -E '^(model|exe):' \"$R/config.yaml\"",
           "sed -n '/^modules:/,/^$/p' \"$R/config.yaml\"",
           ""]
        + _settings_steps('git -C "$R"')
        + ["", "# If git says 'dubious ownership', the folder belongs to someone else: add  -c safe.directory=\"$R\"  after the word git."])


def friends(run_dir: str, sha: str = None) -> str:
    """The closest commit (and tag) the control directory shares with access-om3-configs."""
    return "\n".join(
        _note("WHAT THIS DOES: checks the 'Where are my friends?' section. It makes a temporary copy of "
              "access-om3-configs (the repository of released configurations), adds this run's history to it, and asks "
              "git which commits the two share. The newest shared commit is the run's closest 'friend'; git describe then "
              "names the nearest tag before it.")
        + _vars(run_dir=run_dir)
        + ["", "# Step 1: a temporary copy of access-om3-configs (about 30 MB; deleted at the end)",
           "T=$(mktemp -d)",
           "git clone -q --bare https://github.com/ACCESS-NRI/access-om3-configs.git \"$T/configs.git\"",
           "",
           "# Step 2: add this run's history to the copy (nothing is changed in the run's own folder)",
           "git -c safe.directory='*' --git-dir=\"$T/configs.git\" fetch -q \"$R\" +HEAD:refs/runs/this",
           "",
           "# Step 3: the commits this run shares with any branch or tag (the lines starting with '-'), newest first",
           "git --git-dir=\"$T/configs.git\" rev-list --boundary refs/runs/this --not --branches --tags | grep '^-' \\",
           "  | cut -c2- | xargs -r git --git-dir=\"$T/configs.git\" log --no-walk --format='%h %cI %s'",
           "",
           "# Step 4: which branches contain the newest shared commit, and the nearest tag before it",
           f"S={shlex.quote(sha or '<the first commit printed in step 3>')}",
           "git --git-dir=\"$T/configs.git\" branch --contains \"$S\"",
           "git --git-dir=\"$T/configs.git\" describe --tags --abbrev=0 \"$S\"",
           "",
           "rm -rf \"$T\"   # remove the temporary copy"])


def settings_changes_branch(branch: str) -> str:
    """Clone an experiment's branch and list every edit to the tracked settings files since its first run commit."""
    return "\n".join(
        _note("WHAT THIS DOES: re-checks the settings history of this experiment from its branch in access-om3-experiments, "
              "a copy of the payu control directory kept in git. It downloads the branch, counts payu's run commits, and lists "
              "every edit made to the settings files since the first run, oldest first. " + SETTINGS_HOW_TO_READ
              + " The branch may have new commits since this page was built.")
        + ["", "# Step 1: download this experiment's branch (the history of its control directory) into a folder called exp-check",
           f"git clone --branch {shlex.quote(branch)} --single-branch https://github.com/ACCESS-Community-Hub/access-om3-experiments exp-check",
           "cd exp-check", ""]
        + _settings_steps("git"))