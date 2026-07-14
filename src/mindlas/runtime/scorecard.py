"""Session scorecard: a markdown report + JSON export. Context Rot AND
Verification Debt are live; corrections.jsonl records are PARTITIONED by `type` so a verify_gate
row never perturbs the Context Rot aggregates. write_scorecard is the single disk writer
both ContextRepair and VerifyGate delegate to. Pure functions otherwise."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import paths
from ..vitals.render import (_AMB, _BOLD, _CY, _CY_BG, _DIM, _GRN, _INK, _RD, _RESET, _TRK,
                             _WHITE, _bar_glyphs)


@dataclass(frozen=True)
class Scorecard:
    session_id: str
    task: str
    ctx_max: int
    ctx_final: int
    ctx_alerts: int
    verify_max: int
    verify_final: int
    verify_alerts: int
    verify_last_status: str
    verify_last_coverage: str
    corrections: tuple[dict, ...]
    rails_labels: dict
    blast_max: int = 0
    blast_final: int = 0
    blast_alerts: int = 0
    blast_last_status: str = "none"
    blast_last_bundle_count: int = 0
    loop_max: int = 0
    loop_final: int = 0
    loop_alerts: int = 0
    loop_last_status: str = "none"
    loop_active_stop: bool = False
    loop_failure_signature: str = ""
    # Live gauge scores (0..100) for the terminal card only, so Verify/Blast/Loop show their
    # current reading — like the status line — instead of '--' when no action ran this session.
    # None = no live reading available (errored/absent) -> the row stays '--'. The markdown/JSON
    # proof artifacts stay correction-only and ignore these.
    ctx_live: int | None = None
    verify_live: int | None = None
    blast_live: int | None = None
    loop_live: int | None = None


def _rails_labels(corrections: tuple[dict, ...]) -> dict:
    """MERGE the rails_labels payloads from every correction (any type) into one
    dict, later corrections overriding earlier on shared keys. Keying off label PRESENCE (never on
    type) is the sole basis for rails_export_ready: "ready" means non-empty machine labels suitable
    for RAILS export exist — NOT merely that a scorecard JSON was written. A Context-Repair-only
    session emits no label payload, so it stays rails_export_ready=False. Merging (vs latest-only)
    means a Patch Splitter correction after a Verify Gate correction keeps BOTH label sets."""
    merged: dict = {}
    for c in corrections:
        labels = c.get("rails_labels")
        if labels:
            merged.update(labels)
    return merged


def build_scorecard(*, session_id: str, task: str, ctx_max: int, ctx_final: int,
                    ctx_alerts: int, corrections, ctx_live: int | None = None,
                    verify_live: int | None = None, blast_live: int | None = None,
                    loop_live: int | None = None) -> Scorecard:
    """CTX aggregates are supplied by the caller (which partitions them by type);
    VERIFY aggregates + rails_labels are computed here from the verify_gate records. The
    ctx/verify/blast/loop_live scores are the current live gauge readings; the terminal card shows
    them ONLY in `--latest` (live) mode. Left None (the default), the card is the corrections-only
    proof artifact. The markdown/JSON proof exports ignore the live scores entirely."""
    corrections = tuple(corrections)
    vfy = [c for c in corrections if c.get("type") == "verify_gate"]
    vfy_befores = [c["before"] for c in vfy if "before" in c]
    vfy_afters = [c["after"] for c in vfy if "after" in c]
    last_vfy = vfy[-1] if vfy else {}
    blast = [c for c in corrections if c.get("type") == "patch_splitter"]
    blast_befores = [c["before"] for c in blast if "before" in c]
    blast_afters = [c["planned_after_blast"] for c in blast if "planned_after_blast" in c]
    last_blast = blast[-1] if blast else {}
    loop = [c for c in corrections if c.get("type") == "loop_stop"]
    loop_befores = [c["before"] for c in loop if "before" in c]
    loop_afters = [c["controlled_after_loop"] for c in loop if "controlled_after_loop" in c]
    last_loop = loop[-1] if loop else {}
    return Scorecard(
        session_id=session_id, task=task,
        ctx_max=ctx_max, ctx_final=ctx_final, ctx_alerts=ctx_alerts,
        verify_max=max(vfy_befores, default=0),
        verify_final=(vfy_afters[-1] if vfy_afters else 0),
        verify_alerts=len(vfy),
        verify_last_status=last_vfy.get("status", "none"),
        verify_last_coverage=last_vfy.get("coverage", "none"),
        corrections=corrections, rails_labels=_rails_labels(corrections),
        blast_max=max(blast_befores, default=0),
        blast_final=(blast_afters[-1] if blast_afters else 0),
        blast_alerts=len(blast),
        blast_last_status=last_blast.get("status", "none"),
        blast_last_bundle_count=last_blast.get("bundle_count", 0),
        loop_max=max(loop_befores, default=0),
        loop_final=(loop_afters[-1] if loop_afters else 0),
        loop_alerts=len(loop),
        loop_last_status=last_loop.get("status", "none"),
        loop_active_stop=(last_loop.get("status") == "controlled"),
        loop_failure_signature=last_loop.get("failure_signature", ""),
        ctx_live=ctx_live, verify_live=verify_live, blast_live=blast_live, loop_live=loop_live)


def correction_after(c: dict):
    """The post-repair CTX to display for a correction: prefer the MEASURED after (the
    live CTX of the reseeded session, captured by the status line) over the modeled projection,
    over the legacy `after` key."""
    if "measured_after_ctx" in c:
        return c["measured_after_ctx"]
    return c.get("modeled_after_ctx", c.get("after"))


def _after_label(c: dict) -> str:
    return "measured" if "measured_after_ctx" in c else "modeled"


def scorecard_from_corrections(session_id: str, task: str,
                               corrections: tuple[dict, ...]) -> Scorecard:
    """Derive a Scorecard from the accumulated corrections (the single source of truth), using
    the measured-preferring `correction_after`. Shared by the repair writer, the CLI, and the
    status-line measured-after reconcile so all three agree on ctx_final."""
    corrections = tuple(corrections)
    # The CTX aggregates are Context-Rot-only: a verify_gate/patch_splitter/loop_stop row must
    # never perturb the Context Rot max/final/alert count (the type-partition invariant). The
    # other gauge rows are partitioned by build_scorecard, so it still receives the full set.
    ctx_corr = [c for c in corrections if c.get("type") == "context_repair"]
    befores = [c["before"] for c in ctx_corr if "before" in c]
    afters = [correction_after(c) for c in ctx_corr if correction_after(c) is not None]
    return build_scorecard(session_id=session_id, task=task,
                           ctx_max=max(befores) if befores else 0,
                           ctx_final=afters[-1] if afters else 0,
                           ctx_alerts=len(ctx_corr), corrections=corrections)


def _correction_line(c: dict) -> str:
    if c.get("type") == "loop_stop":
        # CONTROLLED after-score: never "(modeled)", "planned", or
        # "evidence-based". Glyph "→" matches the sibling verify_gate/patch_splitter lines.
        return (f"- Stop: LOOP {c['before']} → {c.get('controlled_after_loop')} controlled, "
                f"signature {c.get('failure_signature')}, stop active")
    if c.get("type") == "patch_splitter":
        # PLANNED after-score: never "(modeled)", never "(evidence-based)".
        return (f"- Patch Splitter: BLAST {c['before']} → {c.get('planned_after_blast')} planned, "
                f"bundles {c.get('bundle_count')}, validation {c.get('status')}")
    if c.get("type") == "verify_gate":
        # Evidence-based: NO "(modeled)" suffix.
        return (f"- Verify Gate: VERIFY {c['before']} → {c.get('after')}, "
                f"result {c.get('status')}, coverage {c.get('coverage')}")
    after = c.get("modeled_after_ctx", c.get("after"))
    after = correction_after(c)
    # Presence-guarded (`in c`) extras render a real 0 (e.g. "constraints 0"); human_decision
    # uses truthiness so a null/absent decision is omitted rather than printed as "human None".
    extras = []
    if "validation" in c:
        extras.append(f"validation {c['validation']}")
    if "evidence_preserved" in c:
        extras.append(f"evidence {c['evidence_preserved']}")
    if "constraints_preserved" in c:
        extras.append(f"constraints {c['constraints_preserved']}")
    if "downstream" in c:
        ds = c["downstream"]
        extras.append(f"downstream {ds['result']}" if ds else "downstream not run")
    if c.get("human_decision"):
        extras.append(f"human {c['human_decision']}")
    base = f"- Context Repair: Rot {c['before']} → {after} ({_after_label(c)})"
    return base + ("; " + "; ".join(extras) if extras else "")


def _verify_row(sc: Scorecard) -> str:
    if sc.verify_alerts == 0:
        return "| Verification Debt | -- | -- | -- | -- |"
    return (f"| Verification Debt | {sc.verify_max} | {sc.verify_final} | "
            f"{sc.verify_alerts} | {sc.verify_alerts} |")


def _blast_row(sc: Scorecard) -> str:
    if sc.blast_alerts == 0:
        return "| Change Blast Radius | -- | -- | -- | -- |"
    return (f"| Change Blast Radius | {sc.blast_max} | {sc.blast_final} planned | "
            f"{sc.blast_alerts} | {sc.blast_alerts} |")


def _loop_row(sc: Scorecard) -> str:
    if sc.loop_alerts == 0:
        return "| Tool Failure Loop | -- | -- | -- | -- |"
    return (f"| Tool Failure Loop | {sc.loop_max} | {sc.loop_final} controlled | "
            f"{sc.loop_alerts} | {sc.loop_alerts} |")


def render_scorecard_md(sc: Scorecard) -> str:
    L = ["# Mindlas Session Scorecard", "", "## Task", sc.task, "", "## Risk Summary",
         "| Feature | Max | Final | Alerts | Corrections |",
         "|---|---:|---:|---:|---:|",
         f"| Context Rot | {sc.ctx_max} | {sc.ctx_final} | {sc.ctx_alerts} | {sc.ctx_alerts} |",
         _verify_row(sc),
         _blast_row(sc),
         _loop_row(sc),
         "", "## Corrections Applied"]
    L += [_correction_line(c) for c in sc.corrections] if sc.corrections else ["- none"]
    # The modeled/measured note scopes to Context Rot only — emit it for a Context-Repair-style
    # correction (typed "context_repair" OR the default typeless correction that _correction_line
    # renders as "Context Repair"), never for a verify_gate/blast/loop-only card. The wording flips
    # once any such correction carries a measured_after_ctx.
    ctx_corr = [c for c in sc.corrections
                if c.get("type") not in ("verify_gate", "patch_splitter", "loop_stop")]
    if ctx_corr and any("measured_after_ctx" in c for c in ctx_corr):
        L += ["", "_Final/after is the **measured** post-resume Rot of the reseeded session where "
              "labeled `measured`; entries labeled `modeled` are the instant projection (a fresh "
              "continuation holding only the compact pack), shown until a `/clear` reseed measures "
              "the real after._"]
    elif ctx_corr:
        L += ["", "_`modeled_after_ctx` / Final is a **modeled** post-repair Rot — the score of a "
              "fresh continuation holding only the compact pack. It is a projection; the "
              "**measured** after is captured once you `/clear` into the reseeded session._"]
    return "\n".join(L) + "\n"


def scorecard_to_json(sc: Scorecard) -> str:
    return json.dumps({
        "session_id": sc.session_id,
        "features": {
            "context_rot": {"max": sc.ctx_max, "final": sc.ctx_final, "alerts": sc.ctx_alerts},
            "verification_debt": {"max": sc.verify_max, "final": sc.verify_final,
                                  "alerts": sc.verify_alerts, "last_result": sc.verify_last_status,
                                  "coverage": sc.verify_last_coverage},
            "change_blast_radius": {"max": sc.blast_max, "final": sc.blast_final,
                                    "final_status": "planned", "alerts": sc.blast_alerts,
                                    "last_result": sc.blast_last_status,
                                    "bundle_count": sc.blast_last_bundle_count},
            "tool_failure_loop": {"max": sc.loop_max, "final": sc.loop_final,
                                  "final_status": "controlled", "alerts": sc.loop_alerts,
                                  "last_result": sc.loop_last_status,
                                  "active_stop": sc.loop_active_stop,
                                  "failure_signature": sc.loop_failure_signature},
        },
        "corrections": list(sc.corrections),
        "rails_labels": sc.rails_labels,
        # "ready" == non-empty RAILS labels exist (not "a JSON file was written").
        "rails_export_ready": bool(sc.rails_labels),
    }, indent=2)


def write_scorecard(*, session_id: str, task: str, root: Path | None = None) -> None:
    """The single scorecard disk writer. Reads corrections.jsonl, partitions CTX
    aggregates to type=="context_repair", and writes latest_scorecard.{md,json}. Both
    ContextRepair._write_scorecard and VerifyGate.apply delegate here. An explicit `root`
    (passed by PatchSplitter) makes corrections + scorecard land under the SAME root as the split
    artifacts; default None resolves to project_root() (env/cwd), unchanged for existing callers."""
    cp = paths.corrections_path(root)
    corrections: tuple[dict, ...] = ()
    if cp.exists():
        corrections = tuple(json.loads(ln) for ln in cp.read_text(encoding="utf-8").splitlines()
                            if ln.strip())
    ctx = [c for c in corrections if c.get("type") == "context_repair"]
    ctx_befores = [c["before"] for c in ctx if "before" in c]
    ctx_afters = [c.get("modeled_after_ctx", c.get("after")) for c in ctx
                  if ("modeled_after_ctx" in c or "after" in c)]
    sc = build_scorecard(session_id=session_id, task=task,
                         ctx_max=max(ctx_befores, default=0),
                         ctx_final=(ctx_afters[-1] if ctx_afters else 0),
                         ctx_alerts=len(ctx), corrections=corrections)
    paths.reports_dir(root).mkdir(parents=True, exist_ok=True)
    paths.scorecard_md_path(root).write_text(render_scorecard_md(sc), encoding="utf-8")
    paths.scorecard_json_path(root).write_text(scorecard_to_json(sc), encoding="utf-8")


# --- Styled terminal scorecard (the `mindlas scorecard` default view). Same design language as the
# live status line: MINDLAS badge, band colors (green/cyan/amber/red), parallelogram bars, dim
# meta. The markdown/JSON exports above are untouched — they stay the proof artifacts. ---
_SC_COLOR = {"STABLE": _GRN, "WATCH": _CY, "WARNING": _AMB, "ALERT": _RD}
_SC_WORD = {"STABLE": "Stable", "WATCH": "Watch", "WARNING": "Warning", "ALERT": "Alert"}


def _sc_band(score: int, is_ctx: bool) -> str:
    """Band for a final score. Context Rot elevates earlier (its own thresholds); the other three
    gauges share the tighter STABLE<25 / WATCH<50 / WARNING<70 / ALERT ladder."""
    if is_ctx:
        return "ALERT" if score >= 80 else "WARNING" if score >= 65 else "WATCH" if score >= 40 else "STABLE"
    return "ALERT" if score >= 70 else "WARNING" if score >= 50 else "WATCH" if score >= 25 else "STABLE"


def _sc_bar_segs(final, color, plain, width):
    """The progress bar as colored segments (fill glyphs in `color`, remainder as a dim track)."""
    fill, empty = _bar_glyphs()
    n = max(0, min(width, round(final / 100 * width)))
    if plain:
        return [(fill * n + empty * (width - n), "")]
    return [(fill * n, color), (fill * (width - n), _TRK)]


def _sc_gauge_segs(label, *, active, final, max_, alerts, is_ctx, plain, bar_w, word=None):
    """One table row as (text, ansi-prefix) segments: label · score · bar · state · max/alerts/fixes.
    In the proof view a correction supplies its after-type `word` (Planned/Controlled); otherwise
    (and in the `--latest` live view) the State is the current band word (Stable/Watch/...)."""
    if not active:
        d = "--"
        segs = [(f"{label:<8}", _WHITE), ("  ", ""), (f"{d:>5}", _DIM), ("  ", "")]
        segs += _sc_bar_segs(0, _TRK, plain, bar_w)
        segs += [("  ", ""), (f"{chr(0x2014):<9}", _DIM), ("  ", ""),
                 (f"{d:>5}", _DIM), ("  ", ""), (f"{d:>7}", _DIM), ("  ", ""), (f"{d:>6}", _DIM)]
        return segs
    band = _sc_band(final, is_ctx)
    color = _SC_COLOR[band]
    strong = color if plain else _BOLD + color
    wd = word or _SC_WORD[band]
    segs = [(f"{label:<8}", _WHITE), ("  ", ""), (f"{final:>5}", strong), ("  ", "")]
    segs += _sc_bar_segs(final, color, plain, bar_w)
    segs += [("  ", ""), (f"{wd:<9}", strong), ("  ", ""),
             (f"{max_:>5}", _DIM), ("  ", ""), (f"{alerts:>7}", _DIM), ("  ", ""), (f"{alerts:>6}", _DIM)]
    return segs


def _sc_correction_segs(c: dict, plain: bool):
    """One correction row: cyan bullet, bold action name, transition with a dimmed arrow, dim meta."""
    t = c.get("type")
    if t == "patch_splitter":
        name, a, b = "Patch Splitter", f"Blast {c.get('before')} ", f" {c.get('planned_after_blast')} planned"
        meta = f"bundles {c.get('bundle_count')} · validation {c.get('status')}"
    elif t == "verify_gate":
        name, a, b = "Verify Gate", f"Verify {c.get('before')} ", f" {c.get('after')}"
        meta = f"result {c.get('status')} · coverage {c.get('coverage')}"
    elif t == "loop_stop":
        name, a, b = "Loop Stop", f"Loop {c.get('before')} ", f" {c.get('controlled_after_loop')} controlled"
        meta = f"signature {c.get('failure_signature')} · stop active"
    else:
        name, a, b = "Context Repair", f"Rot {c.get('before')} ", f" {correction_after(c)} ({_after_label(c)})"
        meta = ""
    nm = _CY if plain else _BOLD + _CY
    segs = [("  ", ""), ("•", _CY), (" ", ""), (f"{name:<15}", nm), (" ", ""),
            (a, ""), ("→", _DIM), (b, "")]
    if meta:
        segs += [("   ", ""), (meta, _DIM)]
    return segs


def render_scorecard_terminal(sc: Scorecard, *, plain: bool = False) -> str:
    """The `mindlas scorecard` card: a bordered panel (MINDLAS badge + title, a gauge table, and the
    corrections), in the live status line's design language — band colors, parallelogram bars, dim
    meta. Box-drawing borders are CP437-safe, so the frame renders even on a legacy console."""
    bar_w = 14
    border = "" if plain else _DIM

    def styled(text, pfx):
        return text if (plain or not pfx) else f"{pfx}{text}{_RESET}"

    def render(segs):                                    # -> (visible_width, colored_string)
        vis = sum(len(t) for t, _ in segs)
        return vis, "".join(styled(t, p) for t, p in segs)

    badge_pfx = "" if plain else _CY_BG + _INK + _BOLD
    title_pfx = "" if plain else _BOLD + _WHITE
    header = [(f"{'Gauge':<8}", _DIM), ("  ", ""), (f"{'Score':>5}", _DIM), ("  ", ""),
              (f"{'Progress':<14}", _DIM), ("  ", ""), (f"{'State':<9}", _DIM), ("  ", ""),
              (f"{'Max':>5}", _DIM), ("  ", ""), (f"{'Alerts':>7}", _DIM), ("  ", ""),
              (f"{'Fixes':>6}", _DIM)]

    def g(label, active, final, mx, al, is_ctx, word=None):
        return _sc_gauge_segs(label, active=active, final=final, max_=mx, alerts=al,
                              is_ctx=is_ctx, plain=plain, bar_w=bar_w, word=word)

    def row(live, corr_final, corr_max, alerts, corr_word):
        """A gauge's (active, final, max, word). `--latest` live view (a live score is set): the
        current reading + its band word. Proof view (no live score): a correction shows its
        before->after + after-type word (Planned/Controlled); a gauge with no action renders '--'."""
        if live is not None:                                 # --latest: current reading
            return True, live, max(corr_max, live), None
        if alerts > 0:                                       # proof: an action ran
            return True, corr_final, corr_max, corr_word
        return False, 0, 0, None                             # proof: nothing happened -> '--'

    # Rot is the headline gauge: the proof view always renders it (its final is the last repair's
    # after, or the live reading when no repair ran); --latest shows the current reading.
    if sc.ctx_live is not None:
        rot_a, rot_f, rot_m = True, sc.ctx_live, max(sc.ctx_max, sc.ctx_live)
    else:
        rot_a = sc.ctx_alerts > 0 or sc.ctx_max > 0 or sc.ctx_final > 0
        rot_f, rot_m = sc.ctx_final, sc.ctx_max
    vfy_a, vfy_f, vfy_m, vfy_w = row(sc.verify_live, sc.verify_final, sc.verify_max,
                                     sc.verify_alerts, None)
    bl_a, bl_f, bl_m, bl_w = row(sc.blast_live, sc.blast_final, sc.blast_max,
                                 sc.blast_alerts, "Planned")
    lp_a, lp_f, lp_m, lp_w = row(sc.loop_live, sc.loop_final, sc.loop_max,
                                 sc.loop_alerts, "Controlled")
    items = [
        [(" MINDLAS ", badge_pfx), ("  ", ""), ("Session Scorecard", title_pfx)],
        "RULE",
        [("Task", _DIM), ("   ", ""), (sc.task, "")],
        [("", "")],
        header,
        g("Rot", rot_a, rot_f, rot_m, sc.ctx_alerts, True),
        g("Verify", vfy_a, vfy_f, vfy_m, sc.verify_alerts, False, vfy_w),
        g("Blast", bl_a, bl_f, bl_m, sc.blast_alerts, False, bl_w),
        g("Loop", lp_a, lp_f, lp_m, sc.loop_alerts, False, lp_w),
        "RULE",
        [("Corrections", _CY)],
    ]
    items += [_sc_correction_segs(c, plain) for c in sc.corrections] if sc.corrections \
        else [[("  none", _DIM)]]

    rendered = [("RULE", 0, "") if it == "RULE" else ("LINE", *render(it)) for it in items]
    inner = max(v for _, v, _ in rendered)
    w = inner + 2                                         # one space of padding on each side
    out = [styled("┌" + "─" * w + "┐", border)]
    for kind, vis, col in rendered:
        if kind == "RULE":
            out.append(styled("├" + "─" * w + "┤", border))
        else:
            out.append(f"{styled(chr(0x2502), border)} {col}{' ' * (inner - vis)} {styled(chr(0x2502), border)}")
    out.append(styled("└" + "─" * w + "┘", border))
    return "\n".join(out) + "\n"
