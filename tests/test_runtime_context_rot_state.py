from mindlas.vitals.events import Event, event_from_json, event_to_json


def test_runtime_and_features_packages_import():
    import mindlas.runtime  # noqa: F401
    import mindlas.features  # noqa: F401


def test_event_output_chars_defaults_none_and_round_trips():
    e = Event(session_id="s", turn=1, ts="t1", kind="tool_call", tool="Read",
              target="f.py", cls="read", output_chars=4096)
    assert e.output_chars == 4096
    assert event_from_json(event_to_json(e)) == e


def test_event_output_chars_absent_in_old_json_is_none():
    # an old ledger line without the field must still load (backward compat)
    line = '{"session_id":"s","turn":1,"ts":"t1","kind":"tool_call","tool":"Read",' \
           '"target":"f.py","cls":"read"}'
    assert event_from_json(line).output_chars is None


from mindlas.runtime.state import (build_context_rot_signals, MASS_BUDGET_CHARS, LARGE_TOOL_OUTPUT_CHARS)


def _prompt(turn, text):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="user_prompt", target=text)


def test_turn_count_is_user_prompt_count():
    evs = [_prompt(1, "do the thing properly"), _prompt(2, "continue"), _prompt(3, "continue")]
    sig = build_context_rot_signals(evs)
    assert sig.turn_count == 3
    assert sig.session_id == "s"


def test_mass_measured_when_context_pct_given():
    sig = build_context_rot_signals([_prompt(1, "x")], context_pct=73.4)
    assert sig.mass_is_measured is True
    assert sig.session_mass_pct == 73.4


def test_mass_proxied_from_text_volume_when_no_pct():
    sig = build_context_rot_signals([_prompt(1, "a" * MASS_BUDGET_CHARS)])
    assert sig.mass_is_measured is False
    assert sig.session_mass_pct == 100.0


def test_mass_proxy_includes_tool_output_chars():
    e = Event(session_id="s", turn=1, ts="t1", kind="tool_call", tool="Read",
              target="f.py", cls="read", output_chars=MASS_BUDGET_CHARS)
    assert build_context_rot_signals([e]).session_mass_pct == 100.0


def _tool(turn, output_chars, cls="read"):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="tool_call",
                 tool="Read", target="f.py", cls=cls, output_chars=output_chars)


def _correction(turn):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="user_prompt",
                 target="no", markers=("correction",))


def _native_compact(turn):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="compact_boundary")


def _mindlas_repair(turn):
    return Event(session_id="s", turn=turn, ts=f"t{turn}", kind="context_repair_end")


def test_large_tool_outputs_thresholds_on_size_not_call_count():
    evs = [_tool(1, 5000), _tool(1, 100), _tool(2, LARGE_TOOL_OUTPUT_CHARS), _tool(2, 1999)]
    sig = build_context_rot_signals(evs, now_turn=2)
    assert sig.large_tool_outputs == 2     # 5000 and 2000 (>= threshold); 100 and 1999 excluded


def test_contract_age_grows_when_later_prompts_are_trivial():
    evs = [_prompt(1, "implement the parser with full coverage")]
    evs += [_prompt(t, "continue") for t in range(2, 11)]   # trivial -> no restatement
    sig = build_context_rot_signals(evs, now_turn=10)
    assert sig.task_contract_age_turns == 9                 # 10 - turn 1


def test_contract_age_resets_on_substantive_restatement():
    evs = [_prompt(1, "implement the parser"), _prompt(5, "actually, switch to a tokenizer")]
    sig = build_context_rot_signals(evs, now_turn=5)
    assert sig.task_contract_age_turns == 0


def test_native_compact_does_NOT_reset_repair_age():
    evs = [_prompt(1, "x"), _native_compact(3)]
    sig = build_context_rot_signals(evs, now_turn=7)
    assert sig.last_repair_age_turns == 7      # native /compact ignored, not 4


def test_mindlas_repair_resets_repair_age():
    evs = [_prompt(1, "x"), _mindlas_repair(3)]
    sig = build_context_rot_signals(evs, now_turn=7)
    assert sig.last_repair_age_turns == 4      # 7 - 3


def test_unresolved_counts_corrections_after_mindlas_repair_only():
    evs = [_correction(1), _mindlas_repair(2), _correction(3), _correction(4)]
    sig = build_context_rot_signals(evs, now_turn=4)
    assert sig.unresolved_assumptions == 2     # only the two after the Mindlas repair


def test_native_compact_does_NOT_clear_unresolved_corrections():
    evs = [_correction(1), _native_compact(2), _correction(3)]
    sig = build_context_rot_signals(evs, now_turn=3)
    assert sig.unresolved_assumptions == 2     # native compact doesn't reset the count
