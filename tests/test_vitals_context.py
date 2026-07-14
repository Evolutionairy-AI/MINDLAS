from mindlas.vitals.events import Event
from mindlas.vitals.context import extract_context


def _u(turn, text, correction=False):
    return Event("s", turn, "t", "user_prompt", target=text,
                 markers=("correction",) if correction else ())


def test_objective_is_first_substantive_prompt():
    evs = [_u(1, "go"), _u(2, "Fix the unicode normalization bug in normalize()")]
    ctx = extract_context(evs)
    assert ctx.objective == "Fix the unicode normalization bug in normalize()"


def test_constraints_collect_correction_marked_prompts_verbatim():
    evs = [_u(1, "Fix the bug in normalizer"),
           _u(2, "No, do not modify the parser", correction=True)]
    ctx = extract_context(evs)
    assert "No, do not modify the parser" in ctx.constraints


def test_reframe_replaces_objective():
    evs = [_u(1, "Add a caching layer to the API"),
           _u(2, "Actually, let's scrap that and fix the failing tests instead")]
    ctx = extract_context(evs)
    assert "fix the failing tests" in ctx.objective.lower()


def test_missing_objective_degrades_gracefully():
    ctx = extract_context([_u(1, "ok"), _u(2, "yes")])
    assert ctx.objective is None
    assert ctx.constraints == ()
