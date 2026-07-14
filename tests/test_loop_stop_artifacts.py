import hashlib
import json
from mindlas.runtime import git_helpers
from mindlas.runtime.loop_stop_artifacts import StopArtifactPayload, write_stop_artifacts
from _verify_helpers import init_git_repo, write


def _payload():
    return StopArtifactPayload(
        stop_id="20260701T120000_8f12", session_id="s", created_at="20260701T120000",
        stop_turn=42, before=86, controlled_after_loop=15, status="controlled",
        failure_signature="timeout:Bash:8f12abcd", active_tool_name="Bash",
        active_command_fingerprint="Bash:12ab34cd", active_failure_category="timeout",
        consecutive_failure_count=4, same_signature_failure_count=3, same_command_retry_count=3,
        retry_without_new_evidence_count=2,
        recommended_next_actions=("Stop retrying the same tool command.",))


def test_writes_manifest_card_latest_active(tmp_path):
    manifest_path, active_path, manifest = write_stop_artifacts(_payload(), project_root=tmp_path)
    ssess = tmp_path / ".mindlas" / "stops" / "sessions" / "s"        # payload session_id="s"
    run = ssess / "20260701T120000_8f12"
    assert (run / "stop_manifest.json").exists()
    assert (run / "stop_card.md").exists()
    assert (ssess / "latest_stop.json").exists()
    assert (ssess / "active_stop.json").exists()
    assert manifest["type"] == "loop_stop" and manifest["active"] is True
    assert manifest["controlled_after_loop"] == 15 and manifest["stop_turn"] == 42
    # H5-adjacent honesty text present in card + manifest
    card = (run / "stop_card.md").read_text(encoding="utf-8")
    assert "controlled stop boundary" in card and "Do not retry the same command unchanged" in card
    assert "LOOP: 86 -> 15 controlled" in card
    # active_stop.json carries the stop_turn tool_loop_state reads back
    active = json.loads((ssess / "active_stop.json").read_text())
    assert active["stop_turn"] == 42 and active["active"] is True


def test_source_and_diff_hash_unchanged_after_write(tmp_path):
    init_git_repo(tmp_path)
    write(tmp_path, "src/a.py", "a = 1\n")

    def _src_hashes():
        return {p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in tmp_path.rglob("*")
                if p.is_file() and ".mindlas" not in p.relative_to(tmp_path).parts
                and ".git" not in p.relative_to(tmp_path).parts}

    before_src = _src_hashes()
    before_diff = git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path))
    write_stop_artifacts(_payload(), project_root=tmp_path)
    assert _src_hashes() == before_src                       # source byte-for-byte identical
    assert git_helpers.diff_hash(tmp_path, git_helpers.untracked(tmp_path)) == before_diff  # C6
