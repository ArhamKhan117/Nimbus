"""Regression tests for opt-in, failure-safe Nimbus diagnostics."""
from __future__ import annotations

import os
import time


def test_debug_session_is_noop_when_diagnostics_disabled(mocker):
    import debug_log
    mocker.patch("debug_log.DIAGNOSTIC_CAPTURE", "off")
    session = debug_log.DebugSession.start("APP.EXE", "Window")
    assert type(session).__name__ == "_NullDebugSession"
    session.log("safe")
    session.close()


def test_debug_session_write_failure_does_not_escape_pipeline(mocker, tmp_path):
    import debug_log
    mocker.patch("debug_log.DIAGNOSTIC_CAPTURE", "on")
    mocker.patch("debug_log._DEBUG_DIR", tmp_path / "debug")
    mocker.patch("debug_log.Path.mkdir", side_effect=OSError("access denied"))
    assert type(debug_log.DebugSession.start("APP.EXE", "Window")).__name__ == "_NullDebugSession"


def test_prune_old_sessions_removes_only_expired_folders(tmp_path):
    from debug_log import _prune_old_sessions
    old = tmp_path / "old"
    recent = tmp_path / "recent"
    old.mkdir(); recent.mkdir()
    old_time = time.time() - 10 * 24 * 60 * 60
    os.utime(old, (old_time, old_time))
    _prune_old_sessions(tmp_path, retention_days=7)
    assert not old.exists()
    assert recent.exists()
