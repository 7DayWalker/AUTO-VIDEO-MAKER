"""Zero-credit tests for the proof-based dedicated Chrome lifecycle."""

from types import SimpleNamespace

import pytest

from app.flow import browser


def _ready_evidence(owner_pid=42):
    root = {"pid": 42, "ppid": 1, "command_line": "chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Projects\\AUTO-VIDEO-MAKER\\browser_profile"}
    return {
        "phase": "launch_poll",
        "popen_pid": 99,
        "popen_poll": 0,
        "popen_returncode": 0,
        "dedicated_root": root,
        "dedicated_processes": [root],
        "port": {"port": 9222, "listening": True, "owner_pid": owner_pid},
        "cdp": {"available": True, "version": {"Browser": "Chrome/test"}},
    }


def test_launch_accepts_normal_launcher_exit_when_dedicated_cdp_is_ready(monkeypatch):
    process = SimpleNamespace(pid=99, returncode=0, poll=lambda: 0)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(browser, "_lifecycle_snapshot", lambda *args, **kwargs: _ready_evidence())
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)

    result = browser.launch_chrome_with_debugging(startup_timeout=1)

    assert result is process
    evidence = browser.get_last_lifecycle_diagnostics()
    assert evidence["classification"] == "ready"
    assert evidence["launcher_exited_normally"] is True


def test_launch_zero_exit_without_cdp_is_specific_failure(monkeypatch):
    process = SimpleNamespace(pid=99, returncode=0, poll=lambda: 0)
    monkeypatch.setattr(browser.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(browser, "_lifecycle_snapshot", lambda *args, **kwargs: {
        "phase": "launch_poll", "popen_pid": 99, "popen_poll": 0,
        "popen_returncode": 0, "dedicated_root": None,
        "dedicated_processes": [],
        "port": {"listening": False, "owner_pid": None},
        "cdp": {"available": False},
    })
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)
    monkeypatch.setattr(browser.time, "time", iter([0, 2]).__next__)

    with pytest.raises(browser.ChromeLifecycleError) as error:
        browser.launch_chrome_with_debugging(startup_timeout=1)

    assert error.value.classification == "launch_returned_zero_cdp_unavailable"
    assert "exit code: 0" not in str(error.value)


def test_startup_rejects_explicitly_wrong_port_owner():
    evidence = _ready_evidence(owner_pid=777)
    assert browser._startup_ready(evidence) is False


def test_dedicated_process_tree_does_not_include_normal_chrome():
    dedicated = {
        "pid": 42, "ppid": 1,
        "command_line": "chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Projects\\AUTO-VIDEO-MAKER\\browser_profile",
    }
    normal = {
        "pid": 43, "ppid": 1,
        "command_line": "chrome.exe --user-data-dir=C:\\Users\\Paul\\AppData\\Local\\Google\\Chrome\\User Data",
    }
    selected = browser._dedicated_process_snapshot([dedicated, normal])
    assert [row["pid"] for row in selected] == [42]
