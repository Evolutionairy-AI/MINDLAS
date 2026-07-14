# Mindlas

![CI](https://github.com/Evolutionairy-AI/MINDLAS/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)

**A coding agent rarely fails at turn one. It fails at turn forty.**

The agent said done. The tests disagree. Mindlas (Mind-Atlas) reads the session live, catches the drift as it builds, and hands the agent a correction with the effect measured before and after.

Open and local. Nothing leaves your machine.

![Demo: Context Rot climbs to ALERT, Context Repair lands, the gauge falls](https://github.com/Evolutionairy-AI/MINDLAS/blob/main/docs/assets/mindlas-demo.gif)

Long sessions deteriorate silently. Context fills with tool-output noise. The original task scrolls away and stops steering the agent. Edits spread across files. The same tool fails in a loop. None of this throws an error; the agent keeps producing confident output while its working state rots.

Mindlas catches that moment live: deterministic gauges under your Claude Code prompt that read the session's own signals, alert before a failure compounds, and pair every alert with a concrete correction whose effect is measured before and after.

The whole product runs on one loop, end to end for every gauge:

```text
measure → trigger → explain → correct → remeasure → report
```

Scores are pure functions of the session's event ledger. Same ledger in, same score out. There is no model and no network call anywhere in the scoring path, and that is permanent: a model grading another model's session is non-reproducible, expensive, and circular. Corrections are deterministic mechanisms, not prompt nudges: an earlier advisory design told the agent to fix itself and failed empirically, so the corrector changes state directly and records a before/after to prove it did something.

**What Mindlas does not claim:** it does not guarantee correctness, prevent hallucinations, know the model's mental state, or replace tests, reviews, or human judgment. Its claim is narrow and verifiable: detect known deterioration causes from deterministic local signals, apply non-native corrections, record before and after effects, and produce an exportable scorecard.

## Sixty seconds to a catch

Requirements: Python >= 3.11, git, Claude Code.

```bash
git clone https://github.com/Evolutionairy-AI/MINDLAS.git
cd MINDLAS
python -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows
pip install -e .

mindlas status --demo context_rot_alert                    # a real ALERT, right now
mindlas context repair --apply --demo context_rot_alert    # watch the correction land: 100 -> 4
```

The demo runs the full loop offline against a bundled fixture. Every frame is labeled demo; live sessions earn their own numbers.

## Wire it into your live sessions

Two pieces: the **hooks**, which record session events into the ledger the gauges read, and the **status line**, the always-on gauge readout under your prompt.

```bash
claude --plugin-dir <path-to-MINDLAS-clone>   # hooks + slash commands, loaded as a plugin
mindlas install-statusline                    # the status line (restart Claude Code after)
```

Launch `claude` from the shell where the venv is active, since the hooks call the `mindlas` CLI and inherit that shell's PATH; without it they silently do nothing.

The plugin also ships five slash commands that drive the corrections on the live session: `/mindlas:mindlas-repair`, `/mindlas:mindlas-verify`, `/mindlas:mindlas-blast-split`, `/mindlas:mindlas-loop-stop`, `/mindlas:mindlas-loop-release`. Each is a thin wrapper over its CLI equivalent (see the [CLI reference](#cli-reference)). The gauges surface them in short form: `/mindlas-repair` is `/mindlas:mindlas-repair`.

If your setup can't load plugins, `mindlas install-hooks` wires the same eight hooks directly into Claude Code instead. Pick **one** path: plugin *or* `install-hooks`, never both, or every event is recorded twice and the gauges inflate. Everything reverses independently: launch without `--plugin-dir`, or `mindlas install-hooks --uninstall` / `mindlas install-statusline --uninstall`.

### The eight hooks

Either path registers the same eight hooks, the write side of the instrument, recording every session event into the ledger the gauges read. Every handler is best-effort and always exits 0: a hook failure can never block or break the session.

| Hook | What it does |
|---|---|
| `SessionStart` | Logs the session, stamps the live-session pointer, reseeds a pending repair pack. |
| `UserPromptSubmit` | Advances the turn counter, refreshes the live-session pointer. |
| `PreToolUse` | Pre-edit baselines; the trust gate at commit/push boundaries (`MINDLAS_GATE`); the loop retry guard. |
| `PostToolUse` | Records each tool result and its output volume; launches the debounced background test tier after edits. |
| `PostToolUseFailure` | Records the failed call (the Tool Failure Loop gauge's telemetry source); alerts on a detected blind-retry loop. |
| `Stop` | Captures the final assistant message (completion claims feed Verification Debt), snapshots the transcript, refreshes tests. |
| `PreCompact` | Snapshots the transcript first, then recommends Context Repair. Never blocks native compact. |
| `PostCompact` | Logs the compaction boundary. |

## Four causes, four gauges, four actions

Each known deterioration cause has exactly one gauge and one paired correction:

| Deterioration cause | Gauge | Corrective action | After-score type |
|---|---|---|---|
| Context deterioration | **Context Rot** (`ROT`) | **Context Repair** | modeled, upgraded to measured |
| Unverified code change | **Verification Debt** (`VERIFY`) | **Verify Gate** | evidence-based |
| Patch spread | **Change Blast Radius** (`BLAST`) | **Patch Splitter** | planned |
| Repeated failed tools | **Tool Failure Loop** (`LOOP`) | **Loop Stop** | controlled |

Bands are action thresholds, not severity grades: WATCH surfaces the correction hint, WARNING formally recommends it, ALERT means correct before it compounds. Context Rot's score accumulates with normal session activity (turns and context mass alone earn points), so its bands sit higher (WATCH at 40, ALERT at 80) than the other three (25/70), which score 0 on a clean session. Each gauge triggers independently, with deliberately no cross-gauge rules.

### Context Rot (`ROT`)

Deterioration of the working context, not window fullness; fullness is only one of six signals and is capped so a full window alone can never reach WARNING. The others are counts and ages read off the event ledger: turns, large tool outputs, task-contract age, unresolved user corrections, and turns since the last repair. That last signal resets only when Context Repair runs. Native `/compact` never resets it: compaction shortens the conversation but does not rebuild the agent's working state, so the gauge deliberately keeps counting. When elevated, the gauge suggests `/mindlas-repair`.

### Verification Debt (`VERIFY`)

Code changed without fresh evidence it still works: changed files and lines, staleness of the last verifier result, production changed without tests, completion claimed without evidence, minus a coverage credit only when evidence is fresh. A truly clean tree scores 0. When elevated, the gauge suggests `/mindlas-verify`.

### Change Blast Radius (`BLAST`)

How broadly a patch has spread across files, directories, and concerns: review and coordination risk, not proof the code is wrong. A small coherent patch earns a cohesion credit and never fires, even when Verification Debt is high; that is Verify Gate's job. When elevated, the gauge suggests `/mindlas-blast-split`.

### Tool Failure Loop (`LOOP`)

The agent stuck retrying the same failing tool call without new evidence. Repetition is scored over the active evidence segment: a success or user prompt breaks the loop, and broken loops never trigger. An active stop boundary caps the score low, and the gauge reads *controlled* rather than healed. When elevated, the gauge suggests `/mindlas-loop-stop`.

The full signal weights and trigger math live in `src/mindlas/features/`; the signal builders in `src/mindlas/runtime/`.

## The four corrections

Every correction is a pair: a dry-run preview that writes nothing, and an apply that records a before and an after.

**Context Repair (modeled → measured).** Rebuilds the task-critical working state from the event ledger into a compact nine-section pack: task contract, constraints, repo and diff state, next action, and an evidence index where every claim carries a turn reference and a sha256 of its source span. Eight validation gates must pass before a pack goes live; a failed validation writes an inert draft and resets nothing. The loop: repair writes the pack and a one-shot resume marker → you press `/clear` (the one step a hook cannot perform, by design) → the new session's `SessionStart` hook injects the pack automatically → the first status-line render records the *measured* post-repair score, upgrading the modeled one. It never calls native `/compact`, and there is no LLM anywhere in the pack builder.

**Verify Gate (evidence-based).** Plans deterministic checks for the files changed this session (AST syntax, ruff, targeted pytest, config-gated), runs them, and re-derives VERIFY from the evidence: a fail floors the score, a fresh pass is capped by what was actually covered. The gate refuses to certify what it didn't check: a skipped run preserves the debt rather than falsely clearing it.

**Patch Splitter (planned).** Partitions a sprawling diff into coherent, reviewable bundles under `.mindlas/splits/` (source files untouched, by contract). The after-score is what BLAST would read if the plan were followed; a no-reduction preview prints the honest reason, never a fabricated improvement.

**Loop Stop (controlled).** Records a controlled stop boundary on a detected blind-retry loop: the gauge falls to *controlled*, a stop card is written, and while the boundary is active a retry of the exact stopped command signature is caught at `PreToolUse` (warn by default, deny with `MINDLAS_LOOP_GUARD=block`). `mindlas loop release` re-arms when the plan has genuinely changed. Detection is also live: the failure hook alerts your terminal and injects a stop directive in-session, de-duplicated per loop.

## The scorecard: proof, not vibes

Every applied correction is recorded; every session ends with an exportable artifact. `mindlas scorecard --json` from the demo above:

```json
{
  "session_id": "demo",
  "features": {
    "context_rot":         { "max": 100, "final": 4, "alerts": 1 },
    "verification_debt":   { "max": 0, "final": 0, "alerts": 0, "last_result": "none", "coverage": "none" },
    "change_blast_radius": { "max": 0, "final": 0, "final_status": "planned", "alerts": 0,
                             "last_result": "none", "bundle_count": 0 },
    "tool_failure_loop":   { "max": 0, "final": 0, "final_status": "controlled", "alerts": 0,
                             "last_result": "none", "active_stop": false, "failure_signature": "" }
  },
  "corrections": [
    {
      "type": "context_repair",
      "applied": true,
      "validation": "pass",
      "before": 100,
      "modeled_after_ctx": 4,
      "evidence_preserved": 6,
      "constraints_preserved": 5,
      "pack_path": "<project>/.mindlas/context/sessions/demo/latest.md"
    }
  ],
  "rails_export_ready": false
}
```

Every after-score is labeled for what it actually is (`modeled`/`measured` for Context Repair, evidence-based for Verify Gate, `planned` for Patch Splitter, `controlled` for Loop Stop) and never borrows a stronger label. A gate that checked two files never certifies ten. (`rails_export_ready` flags whether the session has produced machine-readable evidence labels for export into an external reliability score; no such integration ships today.) `mindlas scorecard` renders the styled terminal view; `--latest` shows a live snapshot of every gauge; `--json` is the machine export.

## Architecture

Each layer has one job and depends only downward:

```text
actions/    context_repair · verify_gate · patch_splitter · loop_stop     non-native corrections
features/   context_rot · verification_debt · blast_radius               detectors (pure scorers)
            · tool_failure_loop
runtime/    signal builders · paths · render · repair_pack · scorecard   shared plumbing
vitals/     events · ledger · capture · hooks · statusline · install     core engine: wire protocol
                                                                          with Claude Code + event log
```

Every gauge is the same triad: a signal builder in `runtime/` folds ledger events into a frozen signals dataclass, a pure scorer in `features/` turns it into a reading, a renderer draws it, with its paired action in `actions/`. Learn one gauge and you know all four.

State lives in two trees, kept apart on purpose: per-machine session telemetry under `~/.mindlas/` (the append-only event ledger the gauges read; override with `MINDLAS_HOME`), and per-project reliability artifacts under `<project>/.mindlas/` (repair packs, verifier results, splits, stops, and `reports/` with `corrections.jsonl` and the scorecard). On Windows without long-path support these session-keyed paths can exceed `MAX_PATH` and fail silently; keep the clone near the drive root or enable `LongPathsEnabled`.

## CLI reference

Read commands default to the latest live session; `--session <uuid>` targets an exact one.

| Command | Flags | Notes |
|---|---|---|
| `status` | `--demo` `--from-ledger` `--plain` | One-line reliability status, all four gauges. |
| `context status\|repair\|resume` | `--preview` `--apply` `--session` `--verify` `--accept`/`--reject` | Context Repair; default `--preview`. `resume` prints the pack for a manual handoff. |
| `scorecard` | `--latest` `--json` `--demo` | The proof artifact; `--latest` = live snapshot, `--json` = machine export. |
| `verify [status\|gate\|latest]` | `--session` `--preview` `--apply` `--full` | VERIFY gauge + Verify Gate. |
| `blast [status\|split\|latest]` | `--session` `--preview` `--apply` | BLAST gauge + Patch Splitter. |
| `loop [status\|stop\|latest\|release]` | `--session` `--preview` `--apply` | LOOP gauge + the stop/guard/release lifecycle. |
| `install-hooks` / `install-statusline` | `--uninstall` | Wire/remove the hooks and status line. |
| `hook <Event>` / `statusline` | (none) | Internal: hook dispatch and status-line rendering. |

Behavior toggles (all optional, defaults apply): `MINDLAS_HOME`, `MINDLAS_PROJECT_ROOT`, `MINDLAS_GATE`, `MINDLAS_LOOP_GUARD`, `MINDLAS_INTERCEPT_COMPACT`, `MINDLAS_TESTTIER`, `MINDLAS_BAR`: defaults, modes, and effects in [docs/configuration.md](docs/configuration.md).

## Proven offline

The entire validation path runs with zero API spend and zero network:

```bash
pip install -e ".[dev]"
pytest                      # 476 passed, 8 skipped
ruff check src/ tests/      # All checks passed
```

Activate the venv first, since several tests shell out to `ruff` and `pytest`, and without the venv on PATH you get environment artifacts, not code failures. Four acceptance tests exercise the full loop per gauge against real ALERT fixtures; golden files pin the after-score labels; the `--demo` fixtures replay the Context Rot loop deterministically offline.

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
