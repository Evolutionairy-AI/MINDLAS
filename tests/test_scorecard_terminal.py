"""The styled `mindlas scorecard` card (render_scorecard_terminal): a bordered panel with a gauge
table and the corrections. The markdown/JSON exports remain the proof artifacts (tested elsewhere)."""
from mindlas.runtime.scorecard import Scorecard, render_scorecard_terminal


def _sc(corrections="default"):
    if corrections == "default":
        corrections = (
            {"type": "patch_splitter", "before": 78, "planned_after_blast": 49,
             "bundle_count": 6, "status": "warning"},
            {"type": "verify_gate", "before": 53, "after": 95,
             "status": "fail", "coverage": "partial"},
        )
    return Scorecard(
        session_id="s", task="create me a code to connect to mysql",
        ctx_max=15, ctx_final=95, ctx_alerts=2,
        verify_max=53, verify_final=95, verify_alerts=1,
        verify_last_status="fail", verify_last_coverage="partial",
        corrections=corrections, rails_labels={},
        blast_max=78, blast_final=49, blast_alerts=1,
        blast_last_status="warning", blast_last_bundle_count=6,
        loop_max=0, loop_final=0, loop_alerts=0, loop_last_status="none",
        loop_active_stop=False, loop_failure_signature="")


def test_card_is_a_bordered_panel():
    out = render_scorecard_terminal(_sc(), plain=True)
    assert out.startswith("┌") and out.rstrip().endswith("┘")     # framed
    assert "│" in out and "├" in out and "┤" in out               # sides + section rules
    # every rendered line is the same visual width (right border lines up)
    widths = {len(ln) for ln in out.rstrip().split("\n")}
    assert len(widths) == 1


def test_card_has_badge_task_and_table_header():
    out = render_scorecard_terminal(_sc(), plain=True)
    assert "MINDLAS" in out and "Session Scorecard" in out
    assert "Task   create me a code to connect to mysql" in out
    for col in ("Gauge", "Score", "Progress", "State", "Max", "Alerts", "Fixes"):
        assert col in out


def test_card_gauge_rows_and_states():
    out = render_scorecard_terminal(_sc(), plain=True)
    assert "Rot" in out and "Verify" in out and "Blast" in out and "Loop" in out
    assert "95" in out and "Alert" in out and "Planned" in out    # Title-case states
    assert "—" in out and "--" in out                             # inactive Loop row


def test_card_corrections_section():
    out = render_scorecard_terminal(_sc(), plain=True)
    assert "Corrections" in out
    assert "• Patch Splitter" in out and "Blast 78 → 49 planned" in out
    assert "• Verify Gate" in out and "Verify 53 → 95" in out


def test_card_colors_by_band():
    from mindlas.vitals.render import _RD, _CY
    out = render_scorecard_terminal(_sc(), plain=False)
    assert _RD in out and _CY in out and "\x1b[" in out           # ALERT red + WATCH/brand cyan


def test_card_no_corrections():
    out = render_scorecard_terminal(_sc(corrections=()), plain=True)
    assert "Corrections" in out and "none" in out


def test_card_latest_shows_live_readings():
    # --latest LIVE view: a live score per gauge -> the row shows the current reading + band, like
    # the status line (not '--'). A None live reading still renders '--'.
    sc = Scorecard(
        session_id="s", task="t", ctx_max=23, ctx_final=23, ctx_alerts=0,
        verify_max=0, verify_final=0, verify_alerts=0, verify_last_status="none",
        verify_last_coverage="none", corrections=(), rails_labels={},
        ctx_live=20, verify_live=42, blast_live=0, loop_live=None)
    out = render_scorecard_terminal(sc, plain=True)
    rot_row = next(l for l in out.split("\n") if "Rot" in l)
    verify_row = next(l for l in out.split("\n") if "Verify" in l)
    blast_row = next(l for l in out.split("\n") if "Blast" in l)
    loop_row = next(l for l in out.split("\n") if "Loop" in l)
    assert "20" in rot_row and "Stable" in rot_row               # live Rot 20, not the proof final 23
    assert "42" in verify_row and "Watch" in verify_row          # live 42 -> WATCH band, not '--'
    assert "Stable" in blast_row and "--" not in blast_row        # live 0 -> Stable, not '--'
    assert "--" in loop_row                                       # None live -> still inactive


def test_card_latest_live_wins_over_correction_history():
    # In --latest (a live score is set) the gauge shows the CURRENT reading; the correction's
    # before->after still appears in the Corrections section below (history is not lost).
    sc = Scorecard(
        session_id="s", task="t", ctx_max=15, ctx_final=15, ctx_alerts=0,
        verify_max=0, verify_final=0, verify_alerts=0, verify_last_status="none",
        verify_last_coverage="none",
        corrections=({"type": "patch_splitter", "before": 78, "planned_after_blast": 49,
                      "bundle_count": 3, "status": "pass"},),
        rails_labels={}, blast_max=78, blast_final=49, blast_alerts=1,
        blast_last_status="pass", blast_last_bundle_count=3,
        blast_live=5)                                            # --latest live reading
    out = render_scorecard_terminal(sc, plain=True)
    blast_gauge_row = next(l for l in out.split("\n") if "Blast" in l and "Stable" in l)
    assert "5" in blast_gauge_row and "Planned" not in blast_gauge_row   # live 5, not correction 49
    assert "Blast 78 → 49 planned" in out                        # correction still in the history


def test_card_proof_default_hides_gauges_without_action():
    # Default PROOF view (no live scores): Verify/Blast/Loop with no correction render '--'; a
    # correction shows its after-type word (Planned). This is the documented proof-artifact design.
    out = render_scorecard_terminal(_sc(), plain=True)          # _sc has patch_splitter + verify_gate
    blast_row = next(l for l in out.split("\n") if "Blast" in l and "Planned" in l)
    loop_row = next(l for l in out.split("\n") if "Loop" in l)
    assert "49" in blast_row                                     # correction after in the table
    assert "--" in loop_row                                      # no loop action -> '--'
