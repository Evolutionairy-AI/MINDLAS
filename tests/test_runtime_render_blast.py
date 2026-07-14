from pathlib import Path
from mindlas.vitals.reading import RiskFact, RiskReading
from mindlas.runtime.render import render_blast_gauge, render_ctx_statusline

_GOLDEN = Path(__file__).parent / "vitals_golden" / "change_blast_radius_alert.txt"


def _alert_reading():
    return RiskReading(
        risk_id="change_blast_radius", label="Change Blast Radius", short_label="BLAST",
        score=82, state="ALERT", direction="up", confidence="high",
        facts=(
            RiskFact("files", 9, "9 files changed"),
            RiskFact("lines", 412, "412 lines changed"),
            RiskFact("directories", 4, "4 directories touched"),
            RiskFact("concerns", 5, "5 concerns detected"),
            RiskFact("config_mixed_with_source", True, "config changed alongside source"),
        ),
        summary=("Change Blast Radius is elevated — the patch has spread across many "
                 "files, directories, or concerns."),
        correction=("The patch is broad enough to raise review and coordination risk. Run the "
                    "Mindlas Patch Splitter to partition it into smaller, coherent, reviewable "
                    "bundles."),
        suggested_commands=("/mindlas-blast-split",),
        can_verify=False,
    )


def _ctx():
    return RiskReading(risk_id="context_rot", label="Context Rot", short_label="ROT", score=12,
                       state="STABLE", direction="flat", confidence="medium", facts=(),
                       summary="Context is healthy.", correction="", suggested_commands=(),
                       can_verify=False)


def test_blast_gauge_renders_label_band_and_correction():
    out = render_blast_gauge(_alert_reading(), plain=True)
    assert out.startswith("BLAST")
    assert "ALERT" in out
    assert "Correction:" in out
    assert "/mindlas-blast-split" in out
    assert "(modeled)" not in out and "evidence-based" not in out   # honesty: BLAST is neither


def test_blast_gauge_matches_golden():
    out = render_blast_gauge(_alert_reading(), plain=True)
    if not _GOLDEN.exists():
        _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN.write_text(out, encoding="utf-8")
    assert out == _GOLDEN.read_text(encoding="utf-8")


def test_statusline_lights_blast_when_supplied():
    blast = _alert_reading()
    out = render_ctx_statusline(_ctx(), None, blast, plain=True)
    lines = out.split("\n")
    # STABLE ctx; the sole ALERT (BLAST) lights its column and drives the status line.
    assert "Rot" in lines[0] and "Stable" in lines[0]
    assert "Blast" in lines[0] and "Alert" in lines[0]
    assert "82" in lines[1]                                   # blast score on row B
    assert lines[2] == f"⚠  Blast Alert — {blast.correction}  → /mindlas-blast-split"


def test_statusline_blast_placeholder_when_absent():
    out = render_ctx_statusline(_ctx(), plain=True)
    lines = out.split("\n")
    assert lines[0].startswith("Rot") and "Stable" in lines[0]
    assert "Blast" in lines[0] and "--" in lines[0]          # absent gauge -> dim placeholder
    assert lines[2] == "◈  All gauges stable — session healthy."
