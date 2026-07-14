"""Reliability-bar rendering: the CTX-first status line and the Context
Rot gauge block. Reuses the pure bar/arrow helpers from vitals/render so the two surfaces
never disagree. Adds a distinct WARNING (amber) so the four
bands form a clear escalation: green -> cyan -> amber -> red."""
from __future__ import annotations

from ..vitals.reading import RiskReading
from ..vitals.render import (_AMB, _BOLD, _CY, _DIM, _GRN, _GREEN, _RD, _RESET, _TRK,
                             _UNDER, _WHITE, _arrow, _bar, _sqbar, _wrap)

_ORANGE = _AMB               # WARNING stays a distinct amber, between the cyan WATCH and red ALERT
# Band -> hue. WATCH is the brand cyan (matching the live instrument), STABLE green, ALERT red.
_BAND_COLOR = {"STABLE": _GRN, "WATCH": _CY, "WARNING": _AMB, "ALERT": _RD}

# Each gauge is a two-row mini-indicator: row A = label (white, left) + state (band color, right);
# row B = score (band color) + progress bar. Four of them sit side by side in one band.
_GAUGE_LABELS = ("Rot", "Verify", "Blast", "Loop")
_DISPLAY = {"ROT": "Rot", "VERIFY": "Verify", "BLAST": "Blast", "LOOP": "Loop"}
_COL_W = 20                  # visible width of one gauge column (both rows align to this)
_BAR_W = 16                  # progress cells on row B (score takes the leading 4 columns)
_COL_SEP = "  "              # gap between the four columns
_STATUS_ICON = {"ALERT": "⚠", "WARNING": "▲", "WATCH": "◆", "STABLE": "◈"}


def _row_a(label: str, r: RiskReading | None, plain: bool) -> str:
    """Row A of a gauge column: label floated left (white), state floated right (band color).
    The state reads Title-case (Stable/Watch/Alert), never ALL CAPS."""
    state = r.state.title() if r is not None else "--"
    pad = max(1, _COL_W - len(label) - len(state))
    if plain:
        return f"{label}{' ' * pad}{state}"
    color = _BAND_COLOR.get(r.state, _GRN) if r is not None else _TRK
    return f"{_wrap(label, _WHITE, plain)}{' ' * pad}{_wrap(state, color, plain)}"


def _row_b(r: RiskReading | None, plain: bool) -> str:
    """Row B of a gauge column: colored score LEFT-aligned (its left edge sits under the label)
    followed by a colored progress bar whose right edge lines up with the row-A state."""
    if r is None:
        score = "--"
        bar = _sqbar(0, _TRK, plain, _BAR_W)
        return f"{score:<3} {bar}" if plain else f"{_wrap(f'{score:<3}', _TRK, plain)} {bar}"
    color = _BAND_COLOR.get(r.state, _GRN)
    score = f"{r.score:<3}"
    bar = _sqbar(r.score, color, plain, _BAR_W)
    return f"{score} {bar}" if plain else f"{_wrap(score, color, plain)} {bar}"


def _status_line(readings: tuple[RiskReading | None, ...], *, plain: bool = False) -> str:
    """The trailing status sentence, surfacing the single most urgent gauge (highest-scoring ALERT,
    else highest elevated WARNING/WATCH, else a healthy note).

    Four visual roles so the eye lands on what's wrong and what to run: the icon stays brand cyan;
    the gauge name + state ("Blast Alert") is bold in the band color; the message reads in the
    terminal's default foreground (theme-agnostic); the suggested command is underlined cyan. The
    `plain=True` output is byte-identical to before, so golden/plain tests are unaffected."""
    live = [r for r in readings if r is not None]
    alerts = [r for r in live if r.state == "ALERT"]
    watch = [r for r in live if r.state in ("WARNING", "WATCH")]
    cmd = ""
    if alerts:
        top = max(alerts, key=lambda r: r.score)
        body = (top.correction or top.summary).strip()
        cmd = top.suggested_commands[0] if top.suggested_commands else ""
    elif watch:
        top = max(watch, key=lambda r: r.score)
        body = (top.summary or top.correction).strip()
    else:
        top, head, body = None, "All gauges stable", "session healthy."
    if top is not None:
        head = f"{_DISPLAY.get(top.short_label, top.short_label.title())} {top.state.title()}"
    icon = _STATUS_ICON.get(top.state if top else "STABLE", "◈")

    if plain:
        return f"{icon}  {head} — {body}" + (f"  → {cmd}" if cmd else "")

    color = _BAND_COLOR.get(top.state, _GRN) if top else _GRN
    line = (f"{_CY}{icon}{_RESET}  "                  # icon: brand cyan (unchanged)
            f"{_BOLD}{color}{head}{_RESET} "          # "<Gauge> <State>": bold, band color
            f"{_DIM}—{_RESET} "                       # separator: dim
            f"{body}")                                # message: terminal default foreground
    if cmd:
        line += f"  {_DIM}→{_RESET} {_CY}{_UNDER}{cmd}{_RESET}"   # command: cyan + underline
    return line


def render_ctx_statusline(ctx: RiskReading | None, verify: RiskReading | None = None,
                          blast: RiskReading | None = None,
                          loop: RiskReading | None = None, *, plain: bool = False) -> str:
    """The reliability band: four two-row gauge indicators side by side, then a status line.

    Row A carries each gauge's label (white, left) and state (band color, right); row B carries the
    band-colored score and a contiguous progress bar. Bands are green->cyan->amber->red
    (STABLE->WATCH->WARNING->ALERT). ANY gauge whose reading is None — unsupplied, or degraded to
    None because its scorer errored (Rot included) — shows a dim `--` placeholder, so one bad gauge
    never blanks the others. The trailing status line names the most urgent gauge."""
    readings = (ctx, verify, blast, loop)
    row_a = _COL_SEP.join(_row_a(lbl, r, plain) for lbl, r in zip(_GAUGE_LABELS, readings))
    row_b = _COL_SEP.join(_row_b(r, plain) for r in readings)
    return "\n".join([row_a, row_b, _status_line(readings, plain=plain)])


def render_ctx_gauge(ctx: RiskReading, *, plain: bool = False) -> str:
    """The Context Rot gauge block: bar line, contributing facts, and (when elevated) the
    recommended Context Repair action."""
    color = _BAND_COLOR.get(ctx.state, _GREEN)
    dir_int = {"up": 1, "down": -1, "flat": 0}.get(ctx.direction, 0)
    state_txt = f"{ctx.state:<7}"
    head = (f"{ctx.short_label:<5}{ctx.score:>3} {_wrap(state_txt, color, plain)} "
            f"{_arrow(dir_int, color, plain)} {_bar(ctx.score, color, plain, width=20)}  {ctx.summary}")
    lines = [head]
    lines += [f"    - {f.text}" for f in ctx.facts]
    if ctx.correction and ctx.state != "STABLE":
        lines.append(f"  Correction: {ctx.correction}")
        lines += [f"  Suggested: {c}" for c in ctx.suggested_commands]
    return "\n".join(lines)


def render_verify_gauge(verify: RiskReading, *, plain: bool = False) -> str:
    """The Verification Debt gauge block: bar line, contributing facts, and (when elevated)
    the recommended Verify Gate action. Mirrors render_ctx_gauge."""
    color = _BAND_COLOR.get(verify.state, _GREEN)
    dir_int = {"up": 1, "down": -1, "flat": 0}.get(verify.direction, 0)
    state_txt = f"{verify.state:<7}"
    head = (f"{verify.short_label:<5}{verify.score:>3} {_wrap(state_txt, color, plain)} "
            f"{_arrow(dir_int, color, plain)} {_bar(verify.score, color, plain, width=20)}  {verify.summary}")
    lines = [head]
    lines += [f"    - {f.text}" for f in verify.facts]
    if verify.correction and verify.state != "STABLE":
        lines.append(f"  Correction: {verify.correction}")
        lines += [f"  Suggested: {c}" for c in verify.suggested_commands]
    return "\n".join(lines)


def render_blast_gauge(blast: RiskReading, *, plain: bool = False) -> str:
    """The Change Blast Radius gauge block: bar line, contributing facts, and (when elevated)
    the recommended Patch Splitter action. Mirrors render_verify_gauge."""
    color = _BAND_COLOR.get(blast.state, _GREEN)
    dir_int = {"up": 1, "down": -1, "flat": 0}.get(blast.direction, 0)
    state_txt = f"{blast.state:<7}"
    head = (f"{blast.short_label:<5}{blast.score:>3} {_wrap(state_txt, color, plain)} "
            f"{_arrow(dir_int, color, plain)} {_bar(blast.score, color, plain, width=20)}  {blast.summary}")
    lines = [head]
    lines += [f"    - {f.text}" for f in blast.facts]
    if blast.correction and blast.state != "STABLE":
        lines.append(f"  Correction: {blast.correction}")
        lines += [f"  Suggested: {c}" for c in blast.suggested_commands]
    return "\n".join(lines)


def render_loop_gauge(loop: RiskReading, *, plain: bool = False) -> str:
    """The Tool Failure Loop gauge block: bar line, contributing facts, and (when elevated) the
    recommended Stop action. Mirrors render_blast_gauge."""
    color = _BAND_COLOR.get(loop.state, _GREEN)
    dir_int = {"up": 1, "down": -1, "flat": 0}.get(loop.direction, 0)
    state_txt = f"{loop.state:<7}"
    head = (f"{loop.short_label:<5}{loop.score:>3} {_wrap(state_txt, color, plain)} "
            f"{_arrow(dir_int, color, plain)} {_bar(loop.score, color, plain, width=20)}  {loop.summary}")
    lines = [head]
    lines += [f"    - {f.text}" for f in loop.facts]
    if loop.correction and loop.state != "STABLE":
        lines.append(f"  Correction: {loop.correction}")
        lines += [f"  Suggested: {c}" for c in loop.suggested_commands]
    return "\n".join(lines)
