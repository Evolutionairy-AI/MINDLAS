"""Bar-glyph selection. Default is the segmented parallelograms (▰/▱) everywhere; the only knob is
the explicit MINDLAS_BAR override, because a subprocess printing to a pipe cannot reliably detect
the host terminal's font (see the note in vitals/render)."""
import mindlas.vitals.render as R


def test_default_is_segments_on_posix(monkeypatch):
    monkeypatch.delenv("MINDLAS_BAR", raising=False)
    monkeypatch.setattr(R.os, "name", "posix")
    assert R._bar_glyphs() == ("▰", "▱")


def test_default_is_segments_on_windows_too(monkeypatch):
    # No auto-downgrade: a working console must not lose its parallelograms just because it's Windows.
    monkeypatch.delenv("MINDLAS_BAR", raising=False)
    monkeypatch.setattr(R.os, "name", "nt")
    assert R._bar_glyphs() == ("▰", "▱")


def test_override_blocks(monkeypatch):
    monkeypatch.setenv("MINDLAS_BAR", "blocks")
    assert R._bar_glyphs() == ("█", "░")


def test_override_ascii(monkeypatch):
    monkeypatch.setenv("MINDLAS_BAR", "ascii")
    assert R._bar_glyphs() == ("#", "-")


def test_override_segments_forces_parallelograms(monkeypatch):
    monkeypatch.setattr(R.os, "name", "nt")
    monkeypatch.setenv("MINDLAS_BAR", "segments")
    assert R._bar_glyphs() == ("▰", "▱")


def test_unknown_override_is_ignored(monkeypatch):
    monkeypatch.setenv("MINDLAS_BAR", "wat")
    assert R._bar_glyphs() == ("▰", "▱")


def test_sqbar_uses_resolved_glyphs(monkeypatch):
    monkeypatch.setenv("MINDLAS_BAR", "blocks")
    assert R._sqbar(50, R._CY, plain=True, width=4) == "██░░"
    monkeypatch.setenv("MINDLAS_BAR", "segments")
    assert R._sqbar(50, R._CY, plain=True, width=4) == "▰▰▱▱"
