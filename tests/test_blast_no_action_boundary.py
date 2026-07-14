"""Plan-1 boundary guard: lighting the BLAST gauge must NOT create any split artifact. Patch
Splitter (which writes .mindlas/splits/) is Plan 2. This drives every Plan-1 surface end to end
under a temp project root and asserts no split dir (indeed no .mindlas/ dir) was created."""
from _verify_helpers import init_git_repo, write
from mindlas.features.blast_radius import ChangeBlastRadiusScorer, score_change_blast_radius
from mindlas.runtime.blast_state import build_change_blast_signals
from mindlas.runtime.render import render_blast_gauge, render_ctx_statusline
from mindlas.vitals.reading import RiskReading


def _ctx():
    return RiskReading(risk_id="context_rot", label="Context Rot", short_label="ROT", score=10,
                       state="STABLE", direction="flat", confidence="medium", facts=(),
                       summary="Context is healthy.", correction="", suggested_commands=(),
                       can_verify=False)


def test_plan1_surfaces_create_no_split_artifacts(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/app/a.py", "x = 1\n")
    write(tmp_path, "src/app/b.py", "y = 2\n")
    write(tmp_path, "src/core/c.py", "z = 3\n")
    write(tmp_path, "pyproject.toml", "[tool.x]\n")

    # the full Plan-1 surface chain: signals -> score -> scorer.read -> gauge + statusline render
    sig = build_change_blast_signals([], session_id="s", project_root=tmp_path)
    score = score_change_blast_radius(sig)
    reading = ChangeBlastRadiusScorer().read(sig)
    assert reading.score == score
    _ = render_blast_gauge(reading, plain=True)
    _ = render_ctx_statusline(_ctx(), None, reading)

    # Plan 1 lights the gauge ONLY — it never plans or writes a split (that is Plan 2).
    assert not (tmp_path / ".mindlas" / "splits").exists()
    assert not (tmp_path / ".mindlas").exists()
