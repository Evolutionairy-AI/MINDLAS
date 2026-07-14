from mindlas.runtime.repair_pack import PackData, Evidence, validate_pack


def _ok_pack(**kw):
    base = dict(
        session_id="s", objective="Do the thing", constraints=("keep API stable",),
        changed_files=("a.py",), verification_state="unknown", decisions=(),
        open_risks=(), next_action="Continue: Do the thing",
        evidence=(Evidence("E-0001", "task", "transcript", "s", 1, "Do the thing", "sha256:x"),
                  Evidence("E-0002", "user_constraint", "transcript", "s", 2, "keep API stable", "sha256:y")),
    )
    base.update(kw)
    return PackData(**base)


def test_validation_passes_on_complete_pack():
    r = validate_pack(_ok_pack())
    assert r.status == "pass"
    assert r.errors == ()


def test_validation_fails_without_task():
    r = validate_pack(_ok_pack(objective=None))
    assert r.status == "fail"
    assert any("V1" in e for e in r.errors)


def test_validation_fails_when_constraint_lacks_evidence():
    # a constraint with no matching user_constraint evidence -> V8 fails
    r = validate_pack(_ok_pack(constraints=("keep API stable", "no schema change"),
                               evidence=(Evidence("E-0001", "task", "transcript", "s", 1, "Do the thing", "sha256:x"),)))
    assert r.status == "fail"
    assert any("V8" in e for e in r.errors)


def test_validation_allows_explicit_none_for_risks():
    # open_risks=() renders "- none" in the pack, which satisfies V6
    assert validate_pack(_ok_pack(open_risks=())).status == "pass"


def test_validation_fails_without_task_evidence_record():
    # objective present but no task evidence record -> V1 fails (review Point-3)
    r = validate_pack(_ok_pack(evidence=(
        Evidence("E-0002", "user_constraint", "transcript", "s", 2, "keep API stable", "sha256:y"),)))
    assert r.status == "fail"
    assert any("V1" in e for e in r.errors)


def test_validation_fails_on_non_sha256_evidence():
    # an evidence record whose hash is not sha256: -> V8 fails (review Point-3)
    r = validate_pack(_ok_pack(evidence=(
        Evidence("E-0001", "task", "transcript", "s", 1, "Do the thing", "md5:x"),
        Evidence("E-0002", "user_constraint", "transcript", "s", 2, "keep API stable", "sha256:y"))))
    assert r.status == "fail"
    assert any("V8" in e for e in r.errors)
