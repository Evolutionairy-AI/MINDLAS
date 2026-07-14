"""The measured 'after'. The repair writes a MODELED after instantly; once the user
/clears into the reseeded session, the status line (the only surface with the real window %)
records the MEASURED after into the last correction and re-renders the scorecard."""
import json

from mindlas.runtime import paths
from mindlas.runtime.scorecard import build_scorecard, render_scorecard_md, scorecard_from_corrections
from mindlas.vitals.statusline import _reconcile_measured_after


def _seed_project(tmp_path, monkeypatch, *, after=None):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    paths.reports_dir().mkdir(parents=True, exist_ok=True)
    rec = {"type": "context_repair", "applied": True, "before": 80,
           "modeled_after_ctx": 40, "pack_path": "x"}
    paths.corrections_path().write_text(json.dumps(rec) + "\n", encoding="utf-8")
    paths.scorecard_meta_path().write_text(
        json.dumps({"session_id": "s", "task": "build the todo CLI"}), encoding="utf-8")
    if after is not None:
        ap = paths.after_pending_path("s")
        ap.parent.mkdir(parents=True, exist_ok=True)
        ap.write_text(json.dumps({"state": "AFTER_PENDING"}), encoding="utf-8")


def test_reconcile_records_measured_after_and_rerenders(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch, after=True)
    payload = {"workspace": {"project_dir": str(tmp_path)}, "session_id": "s"}
    _reconcile_measured_after(payload, measured_score=5)   # reseeded session's live CTX

    last = json.loads(paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["measured_after_ctx"] == 5                 # captured into the last correction
    assert last["modeled_after_ctx"] == 40                 # modeled preview preserved alongside
    assert not paths.after_pending_path("s").exists()      # one-shot pointer consumed

    md = paths.scorecard_md_path().read_text(encoding="utf-8")
    assert "80 → 5 (measured)" in md                       # scorecard now shows the measured after
    assert "build the todo CLI" in md                      # task preserved via scorecard_meta
    scj = json.loads(paths.scorecard_json_path().read_text(encoding="utf-8"))
    assert scj["features"]["context_rot"]["final"] == 5    # ctx_final prefers measured


def test_reconcile_is_noop_without_pointer(tmp_path, monkeypatch):
    _seed_project(tmp_path, monkeypatch, after=None)       # no after_pending armed
    _reconcile_measured_after({"workspace": {"project_dir": str(tmp_path)}, "session_id": "s"},
                              measured_score=5)
    last = json.loads(paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "measured_after_ctx" not in last                # nothing armed -> nothing recorded


def test_reconcile_never_raises_on_garbage(tmp_path, monkeypatch):
    # missing corrections + armed pointer must disarm cleanly, never raise (status line is best-effort)
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    paths.reports_dir().mkdir(parents=True, exist_ok=True)
    ap = paths.after_pending_path("s")
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text("{}", encoding="utf-8")
    _reconcile_measured_after({"workspace": {"project_dir": str(tmp_path)}, "session_id": "s"}, 5)
    assert not paths.after_pending_path("s").exists()


def test_scorecard_labels_measured_vs_modeled():
    modeled = build_scorecard(session_id="s", task="t", ctx_max=80, ctx_final=40, ctx_alerts=1,
                              corrections=({"before": 80, "modeled_after_ctx": 40},))
    assert "80 → 40 (modeled)" in render_scorecard_md(modeled)
    measured = build_scorecard(session_id="s", task="t", ctx_max=80, ctx_final=5, ctx_alerts=1,
                               corrections=({"before": 80, "modeled_after_ctx": 40,
                                             "measured_after_ctx": 5},))
    md = render_scorecard_md(measured)
    assert "80 → 5 (measured)" in md
    assert "**measured**" in md                            # footnote flips to the measured wording


def test_scorecard_from_corrections_prefers_measured():
    sc = scorecard_from_corrections("s", "t", (
        {"type": "context_repair", "before": 60,
         "modeled_after_ctx": 30, "measured_after_ctx": 4},))
    assert sc.ctx_final == 4                               # measured beats modeled for Final


def test_scorecard_from_corrections_ctx_row_ignores_other_gauges():
    # Type-partition invariant: a verify_gate row must never perturb the Context Rot aggregates,
    # even when its numbers are larger and it is the newest correction. CTX max/final/alerts are
    # Context-Rot-only. (verify_gate here: before=85, after=10 — both must be invisible to CTX.)
    sc = scorecard_from_corrections("s", "t", (
        {"type": "context_repair", "before": 60, "modeled_after_ctx": 30},
        {"type": "verify_gate", "before": 85, "after": 10, "status": "pass", "coverage": "full"},
    ))
    assert sc.ctx_max == 60        # not 85 — the verify_gate before does not raise the CTX peak
    assert sc.ctx_final == 30      # not 10 — the newest (verify_gate) row is not the CTX final
    assert sc.ctx_alerts == 1      # not 2 — only the one context_repair counts as a CTX alert


def test_reseed_arms_after_pending(tmp_path, monkeypatch):
    from mindlas.vitals.hooks import dispatch
    from mindlas.actions.context_repair import ContextRepair, RepairContext
    from mindlas.vitals import fixtures
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    events = fixtures.context_rot_alert()
    rc = RepairContext(events=tuple(events), session_id=events[0].session_id,
                       now_turn=max(e.turn for e in events))
    assert ContextRepair().apply(rc, now="20260701T120000").applied
    # /clear keeps the id, so the reseeded session is the SAME "demo" and arms its own after-pointer.
    dispatch("SessionStart", {"session_id": "demo", "source": "clear", "cwd": str(proj)})
    assert paths.after_pending_path("demo").exists()       # measured-after capture armed by reseed
