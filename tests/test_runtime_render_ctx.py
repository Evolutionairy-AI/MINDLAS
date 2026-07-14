from pathlib import Path

from mindlas.vitals.reading import RiskFact, RiskReading
from mindlas.runtime.render import render_ctx_statusline, render_ctx_gauge, _BAND_COLOR

_GOLDEN = Path(__file__).parent / "vitals_golden" / "context_rot_alert.txt"


def _alert_reading():
    return RiskReading(
        risk_id="context_rot", label="Context Rot", short_label="ROT", score=84,
        state="ALERT", direction="up", confidence="high",
        facts=(
            RiskFact("turn_count", 40, "40 turns in the session"),
            RiskFact("task_contract_age_turns", 39, "39 turns since the task was last restated"),
            RiskFact("large_tool_outputs", 24, "24 large tool outputs accumulated"),
            RiskFact("session_mass_pct", 100, "context/session mass ~100%"),
        ),
        summary="Context Rot is elevated.",
        correction=("The session has accumulated stale context, buried constraints, or "
                    "tool-output mass. Run Mindlas Context Repair to compact and restate "
                    "the task contract."),
        suggested_commands=("/mindlas-repair",),
        can_verify=False,
    )


def test_four_band_escalation_colors_are_distinct():
    # green / cyan / amber / red — WATCH (cyan) must not equal ALERT
    assert len({_BAND_COLOR["STABLE"], _BAND_COLOR["WATCH"],
                _BAND_COLOR["WARNING"], _BAND_COLOR["ALERT"]}) == 4


def test_statusline_shows_rot_score_and_band():
    # The band is a two-row indicator: row A = label + state, row B = score + bar; then a status row.
    r = _alert_reading()
    out = render_ctx_statusline(r, plain=True)
    lines = out.split("\n")
    assert len(lines) == 3                                    # labels row, scores row, status line
    assert lines[0].startswith("Rot") and "Alert" in lines[0]   # label left, Title-case state right
    assert "84" in lines[1]                                   # score on row B
    assert "Verify" in lines[0] and "--" in lines[0]          # unwired gauges show a dim placeholder
    assert lines[2] == f"⚠  Rot Alert — {r.correction}  → /mindlas-repair"


def test_statusline_alert_shows_single_most_urgent_message():
    # Multiple gauges at ALERT -> ONE consolidated status line naming the highest-scoring gauge.
    ctx = _alert_reading()
    blast = RiskReading(
        risk_id="change_blast_radius", label="Change Blast Radius", short_label="BLAST",
        score=82, state="ALERT", direction="up", confidence="high", facts=(),
        summary="", correction="Patch is broad.", suggested_commands=("/mindlas-blast-split",),
        can_verify=False)
    out = render_ctx_statusline(ctx, None, blast, plain=True)
    lines = out.split("\n")
    assert len(lines) == 3
    assert "Rot" in lines[0] and "Blast" in lines[0] and lines[0].count("Alert") == 2
    assert "84" in lines[1] and "82" in lines[1]              # both scores on row B
    # the single status line names the most urgent gauge (highest score -> CTX at 84)
    assert lines[2] == f"⚠  Rot Alert — {ctx.correction}  → /mindlas-repair"


def test_statusline_stable_shows_rot_score_in_green():
    # healthy: label + STABLE state on row A, the score on row B, then the healthy status line.
    r = RiskReading(risk_id="context_rot", label="Context Rot", short_label="ROT", score=12,
                    state="STABLE", direction="flat", confidence="high", facts=(),
                    summary="Context is healthy.", correction="", suggested_commands=(),
                    can_verify=False)
    out = render_ctx_statusline(r, plain=True)
    lines = out.split("\n")
    assert lines[0].startswith("Rot") and "Stable" in lines[0]
    assert "12" in lines[1]
    assert lines[2] == "◈  All gauges stable — session healthy."


def test_statusline_rot_none_degrades_to_placeholder():
    # A None Rot reading (its scorer errored) must show '--' like any other gauge — never blank
    # the band or crash. Rot is the anchor gauge, so this is the fail-silent-asymmetry regression.
    blast = RiskReading(
        risk_id="change_blast_radius", label="Change Blast Radius", short_label="BLAST",
        score=40, state="WATCH", direction="up", confidence="high", facts=(),
        summary="", correction="", suggested_commands=(), can_verify=False)
    out = render_ctx_statusline(None, None, blast, plain=True)
    lines = out.split("\n")
    assert len(lines) == 3
    assert lines[0].startswith("Rot") and "--" in lines[0]     # Rot label kept, state placeholder
    assert "Blast" in lines[0] and "Watch" in lines[0]         # siblings still render normally


def test_gauge_block_matches_golden():
    assert render_ctx_gauge(_alert_reading(), plain=True) == _GOLDEN.read_text(encoding="utf-8")


def test_gauge_block_has_correction_lines():
    out = render_ctx_gauge(_alert_reading(), plain=True)
    assert "Correction:" in out
    assert "/mindlas-repair" in out


def test_statusline_lights_verify_when_supplied():
    ctx = _alert_reading()
    verify = RiskReading(
        risk_id="verification_debt", label="Verification Debt", short_label="VERIFY",
        score=82, state="ALERT", direction="up", confidence="high", facts=(),
        summary="", correction="x", suggested_commands=(), can_verify=True,
    )
    out = render_ctx_statusline(ctx, verify, plain=True)
    lines = out.split("\n")
    # Both ALERT gauges light their own column; the status line names the most urgent (CTX at 84).
    assert "Verify" in lines[0] and lines[0].count("Alert") == 2
    assert "84" in lines[1] and "82" in lines[1]
    assert lines[2] == f"⚠  Rot Alert — {ctx.correction}  → /mindlas-repair"
    assert len(lines) == 3


def test_live_reliability_line_lights_all_gauges(tmp_path, monkeypatch):
    from mindlas.vitals.statusline import _ctx_reliability_line
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))   # non-git -> git fallback, clean
    line = _ctx_reliability_line({}, "")
    # VERIFY/BLAST/LOOP are wired live off the (clean) tree -> all read 0 STABLE, not placeholders.
    from mindlas.runtime.render import _BAND_COLOR
    green = _BAND_COLOR["STABLE"]
    labels_row = line.split("\n")[0]
    assert labels_row.count(f"{green}Stable") == 4   # every gauge's Stable state wrapped green
