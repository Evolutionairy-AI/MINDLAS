from mindlas.vitals import fixtures
from mindlas.actions.context_repair import ContextRepair, RepairContext


def _ctx(events):
    now = max((e.turn for e in events), default=0)
    return RepairContext(events=tuple(events), session_id=events[0].session_id if events else "s",
                         now_turn=now)


def test_preview_builds_validated_pack_without_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    events = fixtures.context_rot_alert()
    pv = ContextRepair().preview(_ctx(events))
    assert "# Mindlas Context Repair Pack" in pv.pack_text
    assert pv.validation.status == "pass"
    assert pv.before >= 80                       # the alert fixture is in ALERT territory
    assert not (tmp_path / ".mindlas").exists()  # preview writes nothing


import json
from mindlas.runtime import paths
from mindlas.actions.context_repair import RepairResult


def test_apply_writes_pack_and_shrinks_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    events = fixtures.context_rot_alert()
    res = ContextRepair().apply(_ctx(events), now="20260628T120000")
    assert isinstance(res, RepairResult)
    assert res.applied is True
    assert res.validation == "pass"
    assert res.modeled_after_ctx < res.before          # CTX bar shrinks
    assert res.evidence_preserved >= 1
    assert paths.latest_pack_path("demo").read_text(encoding="utf-8").startswith("# Mindlas Context Repair Pack")
    assert paths.pack_snapshot_path("demo", "20260628T120000").exists()
    ev = json.loads(paths.evidence_snapshot_path("demo", "20260628T120000").read_text(encoding="utf-8"))
    assert ev["created_at"] == "20260628T120000" and ev["session_id"] == "demo"  # time settled in
    assert isinstance(ev["evidence"], list) and ev["evidence"]
    pend = json.loads(paths.pending_resume_path("demo").read_text(encoding="utf-8"))
    assert pend["state"] == "RESUME_PENDING"
    rec = paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(rec[-1])["type"] == "context_repair"
    # the scorecard is written BY the repair.
    assert paths.scorecard_md_path().exists()
    assert paths.scorecard_json_path().exists()
    scj = json.loads(paths.scorecard_json_path().read_text(encoding="utf-8"))
    assert scj["rails_export_ready"] is False
    assert scj["features"]["context_rot"]["max"] == res.before


def test_apply_draft_on_validation_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    from mindlas.vitals.events import Event
    # a session with no substantive user prompt -> no objective -> V1 fails
    events = [Event(session_id="s", turn=1, ts="t1", kind="tool_call", tool="Edit",
                    target="a.py", cls="edit", lines_added=3)]
    res = ContextRepair().apply(_ctx(events), now="20260628T130000")
    assert res.applied is False
    assert res.validation == "fail"
    assert not paths.latest_pack_path("s").exists()              # not applied
    assert paths.draft_pack_path("s", "20260628T130000").exists()  # draft written


def test_apply_records_outcome_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path))
    events = fixtures.context_rot_alert()
    ds = {"ran": True, "tool": "ruff", "target": ["clean_module.py"],
          "findings": 0, "result": "pass", "note": "0 finding(s)"}
    res = ContextRepair().apply(_ctx(events), now="20260629T120000",
                                downstream=ds, human_decision="accepted")
    rec = json.loads(
        paths.corrections_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["constraints_preserved"] == res.constraints_preserved
    assert rec["downstream"] == ds
    assert rec["human_decision"] == "accepted"
    assert "modeled_after_ctx" in rec


# --- The keystone: apply() must EMIT context_repair_end to the VITALS ledger ---
# (the per-machine event stream the live gauge + CLI actually read), and only on success.
# test_runtime_context_rot_state.py fabricates this event to prove the READER; these prove
# the WRITER, and that the writer lands it where the reader looks.
from mindlas.vitals.config import ledger_path as _vitals_ledger_path
from mindlas.vitals.ledger import Ledger as _VitalsLedger
from mindlas.runtime.state import build_context_rot_signals


def _emitted_repair_ends(sid):
    return [e for e in _VitalsLedger(_vitals_ledger_path(sid)).events()
            if e.kind == "context_repair_end"]


def test_apply_emits_one_context_repair_end_at_now_turn(tmp_path, monkeypatch):
    # isolate BOTH trees: MINDLAS_HOME -> vitals ledger; MINDLAS_PROJECT_ROOT -> project tree.
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path / "proj"))
    events = fixtures.context_rot_alert()
    rc = _ctx(events)
    res = ContextRepair().apply(rc, now="20260630T141500")
    assert res.applied is True
    ends = _emitted_repair_ends(rc.session_id)
    assert len(ends) == 1                       # exactly one, nowhere else
    assert ends[0].turn == rc.now_turn          # turn (int), not the ts string
    assert ends[0].session_id == rc.session_id
    assert ends[0].ts == "20260630T141500"      # ts carries `now`, not swapped with turn


def test_apply_validation_fail_emits_no_context_repair_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path / "proj"))
    from mindlas.vitals.events import Event
    # no substantive prompt -> no objective -> validation fails -> draft, not applied
    events = [Event(session_id="s", turn=1, ts="t1", kind="tool_call", tool="Edit",
                    target="a.py", cls="edit", lines_added=3)]
    rc = _ctx(events)
    res = ContextRepair().apply(rc, now="20260630T141600")
    assert res.applied is False
    assert _emitted_repair_ends(rc.session_id) == []   # a failed repair must NOT reset the clock


def test_apply_archival_snapshot_failure_still_arms_reseed(tmp_path, monkeypatch):
    """Hardening: the loop-critical pack + one-shot marker are written BEFORE the archival
    snapshots, and the snapshots are best-effort — so a snapshot-write failure (e.g. MAX_PATH,
    disk full, file lock) must NOT abort apply nor leave the reseed half-armed. The SessionStart
    reseed needs exactly latest.md + pending_resume.json, and the clock-reset event must land."""
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path / "proj"))

    def _boom(*a, **k):
        raise OSError("simulated snapshot write failure (e.g. MAX_PATH / disk full)")
    monkeypatch.setattr(paths, "pack_snapshot_path", _boom)   # archival history write blows up

    events = fixtures.context_rot_alert()
    rc = _ctx(events)
    res = ContextRepair().apply(rc, now="20260701T101010")
    assert res.applied is True                                # apply still succeeds
    # both loop-critical artifacts exist despite the archival failure
    assert paths.latest_pack_path(rc.session_id).read_text(
        encoding="utf-8").startswith("# Mindlas Context Repair Pack")
    pend = json.loads(paths.pending_resume_path(rc.session_id).read_text(encoding="utf-8"))
    assert pend["state"] == "RESUME_PENDING"
    # the clock-reset event still landed exactly once (loop closes)
    assert len(_emitted_repair_ends(rc.session_id)) == 1
    # and the correction/scorecard path still ran (reports dir was created)
    assert paths.corrections_path().exists()


def test_apply_resets_live_signals_via_emitted_event(tmp_path, monkeypatch):
    """Integration: recompute build_context_rot_signals over the SAME vitals stream the live
    gauge reads, now including the event apply() emitted — both live signals must fall to 0."""
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(tmp_path / "proj"))
    events = fixtures.context_rot_alert()
    rc = _ctx(events)
    # before the repair the clock is pinned and every correction still counts
    before = build_context_rot_signals(list(events), now_turn=rc.now_turn)
    assert before.last_repair_age_turns == rc.now_turn
    assert before.unresolved_assumptions > 0
    res = ContextRepair().apply(rc, now="20260630T141700")
    assert res.applied is True
    # reload the emitted event from the vitals ledger and recompute the live signals
    combined = list(events) + _VitalsLedger(_vitals_ledger_path(rc.session_id)).events()
    after = build_context_rot_signals(combined, now_turn=rc.now_turn)
    assert after.last_repair_age_turns == 0      # clock reset (loop closes)
    assert after.unresolved_assumptions == 0     # corrections cleared
