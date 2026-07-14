from mindlas.vitals.events import Event
from mindlas.vitals.context import extract_context
from mindlas.runtime.repair_pack import build_evidence_index


def _prompt(turn, text, correction=False):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="user_prompt",
                 target=text, markers=("correction",) if correction else ())


def test_evidence_index_ids_task_and_constraints():
    events = [_prompt(1, "Build a CSV exporter for the reports module."),
              _prompt(2, "no, do not change the database schema", correction=True)]
    ctx = extract_context(events)
    ev = build_evidence_index(events, ctx, "s")
    assert ev[0].id == "E-0001"
    assert ev[0].type == "task"
    assert ev[1].id == "E-0002"
    assert ev[1].type == "user_constraint"
    assert all(e.hash.startswith("sha256:") for e in ev)
    assert all(e.session_id == "s" for e in ev)


from pathlib import Path
from mindlas.runtime.repair_pack import PackData, build_pack_data, render_repair_pack

_GOLDEN = Path(__file__).parent / "vitals_golden" / "repair_pack.md"


def _edit(turn, path):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="tool_call",
                 tool="Edit", target=path, cls="edit", lines_added=5)


def test_build_pack_data_extracts_task_constraints_files():
    events = [_prompt(1, "Build a CSV exporter for the reports module."),
              _prompt(2, "no, do not change the database schema", correction=True),
              _edit(3, "reports/export.py")]
    pd = build_pack_data(events, session_id="s")
    assert pd.objective == "Build a CSV exporter for the reports module."
    assert "no, do not change the database schema" in pd.constraints
    assert "reports/export.py" in pd.changed_files
    assert len(pd.evidence) == 2


from mindlas.vitals.context import SessionContext

_STDLIB = "standard library only, no third-party packages"


def test_inherit_objective_when_fresh_is_a_resume_prompt():
    # After a /clear the reset ledger's first prompt is the bare resume line — must NOT become the
    # objective; inherit the prior pack's instead (and keep its constraints).
    events = [_prompt(1, "resume from the context repair package.")]
    inherited = SessionContext(objective="Build a small CLI expense tracker.",
                               constraints=(_STDLIB,))
    pd = build_pack_data(events, session_id="s", inherited=inherited)
    assert pd.objective == "Build a small CLI expense tracker."
    assert _STDLIB in pd.constraints


def test_inherit_constraints_when_fresh_has_none():
    events = [_prompt(1, "Add a colored table to the list command.")]   # real objective, no constraint
    inherited = SessionContext(objective="old objective", constraints=(_STDLIB,))
    pd = build_pack_data(events, session_id="s", inherited=inherited)
    assert pd.objective == "Add a colored table to the list command."   # real fresh objective kept
    assert _STDLIB in pd.constraints                                     # inherited constraint survives


def test_real_fresh_objective_is_not_overridden():
    events = [_prompt(1, "Migrate the storage layer to SQLite.")]
    inherited = SessionContext(objective="Build a CLI expense tracker.", constraints=())
    pd = build_pack_data(events, session_id="s", inherited=inherited)
    assert pd.objective == "Migrate the storage layer to SQLite."        # a genuine new task replaces


def test_objective_about_the_repair_pack_is_not_flagged_meta():
    # In THIS repo a legitimate objective can mention the repair pack; only a resume/continue-from
    # instruction is "meta". The topic word must not trigger inheritance.
    events = [_prompt(1, "Improve the context repair package validation logic.")]
    inherited = SessionContext(objective="something else", constraints=())
    pd = build_pack_data(events, session_id="s", inherited=inherited)
    assert pd.objective == "Improve the context repair package validation logic."


def test_no_inherited_contract_leaves_pack_unchanged():
    events = [_prompt(1, "Build a CSV exporter for the reports module.")]
    assert (build_pack_data(events, session_id="s").objective
            == build_pack_data(events, session_id="s", inherited=None).objective
            == "Build a CSV exporter for the reports module.")


def test_inherited_and_fresh_constraints_are_unioned_deduped():
    events = [_prompt(1, "Add a budget command."),
              _prompt(2, "no, keep it stdlib only", correction=True)]
    inherited = SessionContext(objective="old",
                               constraints=("no, keep it stdlib only", "another rule"))
    pd = build_pack_data(events, session_id="s", inherited=inherited)
    assert pd.constraints.count("no, keep it stdlib only") == 1          # deduped across both sources
    assert "another rule" in pd.constraints


def test_reseed_copies_prior_contract_for_inheritance(tmp_path, monkeypatch):
    """The /clear reseed must copy the prior session's contract into the reseeded session so a
    SECOND repair there inherits the objective/constraints (fixes b2b amnesia)."""
    import json
    from mindlas.vitals.hooks import dispatch
    from mindlas.actions.context_repair import ContextRepair, RepairContext
    from mindlas.vitals import fixtures
    from mindlas.runtime import paths
    proj = tmp_path / "proj"
    monkeypatch.setenv("MINDLAS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MINDLAS_PROJECT_ROOT", str(proj))

    events = fixtures.context_rot_alert()
    a = events[0].session_id
    rc = RepairContext(events=tuple(events), session_id=a, now_turn=max(e.turn for e in events))
    assert ContextRepair().apply(rc, now="20260701T120000").applied
    assert paths.pack_contract_path(a, proj).exists()                    # repair #1 persisted A's contract

    # /clear mints a NEW id B; A was the live session (pointer), so the reseed resolves A's flag.
    csp = paths.current_session_path(proj)
    csp.parent.mkdir(parents=True, exist_ok=True)
    csp.write_text(json.dumps({"session_id": a}), encoding="utf-8")
    dispatch("SessionStart", {"session_id": "session-B", "source": "clear", "cwd": str(proj)})

    inh = paths.inherited_contract_path("session-B", proj)
    assert inh.exists()                                                  # A's contract carried into B
    assert json.loads(inh.read_text(encoding="utf-8")).get("objective")  # objective is there to inherit


def test_render_repair_pack_matches_golden():
    pd = PackData(
        session_id="s",
        objective="Build a CSV exporter for the reports module.",
        constraints=("do not change the database schema",),
        changed_files=("reports/export.py",),
        verification_state="unknown (no verifier run recorded)",
        decisions=(),
        open_risks=(),
        next_action="Continue: Build a CSV exporter for the reports module.",
        evidence=build_evidence_index(
            [_prompt(1, "Build a CSV exporter for the reports module."),
             _prompt(2, "do not change the database schema", correction=True)],
            extract_context([_prompt(1, "Build a CSV exporter for the reports module."),
                             _prompt(2, "do not change the database schema", correction=True)]),
            "s"),
    )
    assert render_repair_pack(pd) == _GOLDEN.read_text(encoding="utf-8")
