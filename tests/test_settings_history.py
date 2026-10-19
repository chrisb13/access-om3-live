"""Settings history: edits are dated by the last run that finished (issue #3), and redone runs count once."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "gadi"), str(ROOT / "scripts")]
import run_status  # noqa: E402


def _commit(repo: Path, message: str, dt=None) -> None:
    if dt is not None:
        (repo / "MOM_input").write_text(f"DT = {dt}\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", message], check=True)


def test_an_edit_after_a_crashed_attempt_is_after_the_run_before_it(tmp_path):
    repo = tmp_path / "control"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "config.yaml").write_text("model: access-om3\n")
    _commit(repo, "2026-09-01 10:00:00: Run 11", dt=900)
    _commit(repo, "2026-09-02 10:00:00: Run 12")          # this attempt crashes at 1970-03-22
    _commit(repo, "Reduce DT after the crash", dt=720)     # popup 1 in issue #3
    _commit(repo, "2026-09-03 10:00:00: Run 12")          # the redo
    _commit(repo, "A later edit", dt=600)                  # popup 2
    _commit(repo, "2026-09-04 10:00:00: Run 13")
    hist = run_status.settings_history(str(repo))
    assert [(c["key"], c["new"], c["counter"]) for c in hist["changes"]] == [("DT", "720", 11), ("DT", "600", 12)]
    assert hist["completed"] == 3 and hist["redone"] == [12]
    assert [n for _, n in hist["runs"]] == [11, 12, 12, 13]  # every run commit is still drawn
