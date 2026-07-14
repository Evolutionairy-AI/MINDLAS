"""Pure rendering primitives (ANSI bars, arrows, colors) and the status-line header.

The four gauge renders in runtime/render.py are built on these primitives.
Golden-file testable. `plain=True` strips color so the terminal output is stable.
"""
from __future__ import annotations

import os

_GREEN, _AMBER, _RED, _DIM, _RESET = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
_BAR_W = 24

# --- Truecolor (24-bit) brand palette. The inline status line renders in truecolor so the
# gauge/band hues are exact; the older 16-color codes above stay for the detailed gauge blocks
# and their color-stripped goldens. #25B0CF is the Mindlas cyan. ---
_CY = "\033[38;2;37;176;207m"     # cyan  #25B0CF  (brand + WATCH band + context bar)
_CY_BG = "\033[48;2;37;176;207m"  # cyan background (the MINDLAS badge fill)
_INK = "\033[38;2;0;0;0m"        # black - badge text on the cyan fill
_GRN = "\033[38;2;63;185;80m"     # STABLE  #3FB950
_RD = "\033[38;2;233;70;70m"      # ALERT   #E94646
_AMB = "\033[38;2;224;150;60m"    # WARNING #E0963C (inferred, between cyan and red)
_TRK = "\033[38;2;64;64;64m"      # #404040 - the unfilled square track
_BOLD = "\033[1m"
_UNDER = "\033[4m"                   # underline - marks the runnable command on the status line
_WHITE = "\033[38;2;222;226;230m"  # near-white for the gauge labels (float-left, uncolored by band)

# --- Progress-bar glyphs. The default is the segmented parallelograms (▰/▱); they render crisply
# on macOS, Linux, and any Windows console using a TrueType font (Consolas, Cascadia, Windows
# Terminal, VS Code...). The only place they break is a Windows console stuck on a RASTER font
# (often the elevated "Administrator" cmd), whose bitmap fonts lack U+25B0/U+25B1 and show boxes.
#
# We deliberately do NOT auto-detect that: the status line is printed to a pipe and drawn by the
# host (Claude Code), so this process has no handle to the real console or its font, and elevated
# vs normal conhost are indistinguishable from the environment. Auto-guessing only mis-fires and
# downgrades working terminals. Instead, `MINDLAS_BAR=segments|blocks|ascii` sets the style
# explicitly for the rare console that needs it (blocks/ascii live in every OEM/ASCII font). ---
_BAR_STYLES = {"segments": ("▰", "▱"), "blocks": ("█", "░"), "ascii": ("#", "-")}


def _bar_glyphs() -> tuple[str, str]:
    """(fill, empty) glyphs for the square bars. Segmented by default; `MINDLAS_BAR` overrides it
    (segments | blocks | ascii) for a console whose font can't render the parallelograms."""
    override = os.environ.get("MINDLAS_BAR", "").strip().lower()
    return _BAR_STYLES.get(override, _BAR_STYLES["segments"])


def _wrap(s: str, code: str, plain: bool) -> str:
    return s if plain else f"{code}{s}{_RESET}"


def _bar(index: int, color: str, plain: bool, width: int = _BAR_W) -> str:
    filled = round(index / 100 * width)
    return _wrap("█" * filled + "░" * (width - filled), color, plain)


def _sqbar(index: int, color: str, plain: bool, width: int = 6) -> str:
    """A compact square-cell progress bar: `filled` colored cells over a dim track. The cell glyphs
    auto-adapt per terminal (see `_bar_glyphs`). In plain mode (color stripped) filled/empty stay
    distinguishable via the two distinct glyphs."""
    fill, empty = _bar_glyphs()
    filled = max(0, min(width, round(index / 100 * width)))
    if plain:
        return fill * filled + empty * (width - filled)
    return f"{color}{fill * filled}{_RESET}{_TRK}{fill * (width - filled)}{_RESET}"


def _badge(text: str, plain: bool) -> str:
    """The MINDLAS wordmark: #444 text on a cyan fill, padded so the background reads as a chip."""
    return text if plain else f"{_CY_BG}{_INK}{_BOLD} {text} {_RESET}"


def _arrow(direction: int, color: str, plain: bool) -> str:
    sym = {1: "▴", -1: "▾", 0: "▬"}[direction]
    return _wrap(sym, _DIM if direction == 0 else color, plain)


_STATUS_BAR_W = 10   # width of the context-usage square bar in the header


def _status_header(header: dict, plain: bool) -> str:
    """Line 1: the MINDLAS badge, model / project, and the cyan context-usage square bar. The
    context bar is always cyan - it tracks window usage, not a risk band."""
    model = header.get("model_label") or ""
    project = header.get("project") or ""
    pct = header.get("pct")
    seg = " │ ".join(p for p in (model, project) if p)   # model | project
    if pct is None:
        ctx = "··%"
    else:
        p = int(round(pct))
        ctx = f"{_sqbar(p, _CY, plain, width=_STATUS_BAR_W)} {_wrap(f'{p}%', _CY, plain)}"
    parts = [_badge("MINDLAS", plain)]
    if seg:
        parts.append(seg)
    parts.append(ctx)
    return "  ".join(parts).strip()


def render_statusline(header: dict, *, plain: bool = False) -> str:
    """Pure: header dict -> line 1 (the MINDLAS badge, model / project, and the cyan context bar)."""
    return _status_header(header, plain)
