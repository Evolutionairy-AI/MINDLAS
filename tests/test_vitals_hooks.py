from mindlas.vitals.config import ledger_path
from mindlas.vitals.ledger import Ledger
from mindlas.vitals.hooks import dispatch


def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path))


def test_session_start_creates_ledger_and_records_source(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    # real SessionStart payload has `source` but no `model` field
    dispatch("SessionStart", {"session_id": "s1", "source": "startup"})
    evs = Ledger(ledger_path("s1")).events()
    start = [e for e in evs if e.kind == "session_start"]
    assert len(start) == 1
    assert start[0].target == "startup"   # source captured; model isn't available from hooks


def test_user_prompt_appended_with_incrementing_turn(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "Fix the unicode bug now"})
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "no, not the parser"})
    evs = [e for e in Ledger(ledger_path("s1")).events() if e.kind == "user_prompt"]
    assert [e.turn for e in evs] == [1, 2]
    assert "correction" in evs[1].markers


def test_post_tool_use_captures_tool_event(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "work on it please"})
    dispatch("PostToolUse", {"session_id": "s1", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}})
    evs = Ledger(ledger_path("s1")).events()
    tc = [e for e in evs if e.kind == "tool_call"]
    assert tc and tc[0].cls == "edit" and tc[0].turn == 1


def test_stop_captures_done_claim_from_last_assistant_message(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    dispatch("UserPromptSubmit", {"session_id": "s1", "prompt": "do the thing"})
    dispatch("Stop", {"session_id": "s1", "last_assistant_message": "All done — the fix works."})
    asst = [e for e in Ledger(ledger_path("s1")).events() if e.kind == "assistant_msg"]
    assert asst and "done_claim" in asst[-1].markers
    # a Stop with no assistant text is still safe (no marker, no raise)
    dispatch("UserPromptSubmit", {"session_id": "s2", "prompt": "go"})
    dispatch("Stop", {"session_id": "s2"})
    asst2 = [e for e in Ledger(ledger_path("s2")).events() if e.kind == "assistant_msg"]
    assert asst2 and asst2[-1].markers == ()


def test_compact_boundary_recorded(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    dispatch("PreCompact", {"session_id": "s1", "trigger": "auto"})
    assert Ledger(ledger_path("s1")).count_kind("compact_boundary") == 1


def test_dispatch_never_raises_on_garbage(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    assert dispatch("PostToolUse", {}) == {}          # missing fields -> no-op, no raise
    assert dispatch("Nonsense", {"session_id": "s"}) == {}
    assert dispatch("PostToolUse", [1, 2, 3]) == {}   # non-dict payload -> no-op, no raise


# --- The live reseed wire. apply() leaves a pending marker + warm pack in the
# per-PROJECT tree; SessionStart (with a cwd) must inject the pack as additionalContext and
# consume the marker. The /clear keystroke that triggers a fresh SessionStart is human-only
# and cannot be exercised headlessly — this drives the hook directly with a SessionStart
# payload, which is exactly what /clear produces.

def _apply_repair(proj):
    from mindlas.vitals import fixtures
    from mindlas.actions.context_repair import ContextRepair, RepairContext
    events = fixtures.context_rot_alert()
    rc = RepairContext(events=tuple(events), session_id=events[0].session_id,
                       now_turn=max(e.turn for e in events))
    res = ContextRepair().apply(rc, now="20260630T142000")
    assert res.applied is True
    return events


def test_session_start_reseeds_pack_and_consumes_marker(tmp_path, monkeypatch):
    from mindlas.runtime import paths
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))   # vitals ledger isolation
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))        # project tree isolation
    _apply_repair(proj)
    assert paths.pending_resume_path("demo").exists()               # fixture SID
    pack_text = paths.latest_pack_path("demo").read_text(encoding="utf-8")

    # SessionStart carrying a cwd (mirrors what /clear emits). /clear keeps the session id, so the
    # starting session is the SAME "demo" — it pulls its OWN pack by session id.
    out = dispatch("SessionStart", {"session_id": "demo", "source": "clear", "cwd": str(proj)})
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert hso["additionalContext"] == pack_text            # the warm pack was injected
    assert not paths.pending_resume_path("demo").exists()   # the one-shot flag was consumed


def test_session_start_without_marker_returns_empty(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    out = dispatch("SessionStart", {"session_id": "fresh", "source": "startup", "cwd": str(proj)})
    assert out == {}      # no pending repair -> nothing injected, ordinary session start


def test_user_prompt_does_NOT_consume_resume_marker(tmp_path, monkeypatch):
    # The reseed lives ONLY in SessionStart. UserPromptSubmit must NOT consume the marker,
    # or `/mindlas-repair` (which writes the marker mid-turn) would self-consume it before the
    # user can `/clear`. Regression test for a bug found in the live CLI, not by pytest.
    from mindlas.runtime import paths
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    _apply_repair(proj)
    assert paths.pending_resume_path("demo").exists()
    out = dispatch("UserPromptSubmit", {"session_id": "demo", "prompt": "continue", "cwd": str(proj)})
    assert "hookSpecificOutput" not in out                 # no pack injected on a prompt
    assert paths.pending_resume_path("demo").exists()      # flag SURVIVES for the /clear reseed


# --- Live-session pointer: the hooks record the real session uuid (project-local) so the
# repair CLI can target THIS session's ledger + pack tree instead of guessing newest-by-mtime.

def test_session_start_and_user_prompt_stamp_current_session(tmp_path, monkeypatch):
    import json
    from mindlas.runtime import paths
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    dispatch("SessionStart", {"session_id": "uuid-fresh", "source": "startup", "cwd": str(proj)})
    assert json.loads(paths.current_session_path().read_text(encoding="utf-8"))["session_id"] == "uuid-fresh"
    # a later prompt from a different session refreshes the pointer to the live one
    dispatch("UserPromptSubmit", {"session_id": "uuid-live", "prompt": "go", "cwd": str(proj)})
    assert json.loads(paths.current_session_path().read_text(encoding="utf-8"))["session_id"] == "uuid-live"


# --- Cold store: Stop/PreCompact snapshot the full transcript losslessly. ---

def test_stop_snapshots_transcript_to_cold_store(tmp_path, monkeypatch):
    from mindlas.runtime import paths
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)   # fall back to project .mindlas
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"role":"user","content":"hi"}\n', encoding="utf-8")
    dispatch("UserPromptSubmit", {"session_id": "cs1", "prompt": "go", "cwd": str(proj)})
    dispatch("Stop", {"session_id": "cs1", "last_assistant_message": "done",
                      "transcript_path": str(transcript), "cwd": str(proj)})
    snap = paths.cold_store_dir() / "transcripts" / "cs1.jsonl"
    assert snap.exists()
    assert snap.read_text(encoding="utf-8") == transcript.read_text(encoding="utf-8")


def test_cold_store_prefers_plugin_data_dir(tmp_path, monkeypatch):
    from mindlas.runtime import paths
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugindata"))
    assert paths.cold_store_dir() == tmp_path / "plugindata" / ".mindlas"
