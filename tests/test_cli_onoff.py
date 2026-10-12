"""
CLI ON/OFF LIFECYCLE

Runs the real `bin/tts` and hooks against stub `launchctl`/`curl`/`PlistBuddy`, so the
suite needs no macOS services (CI runs on Linux).

Business rules covered:
- "on" == launchd agent enabled + loaded; `tts off` stops it and disables it
- launchd refuses to bootstrap a disabled service, so `tts on` must enable first
- config commands never start a stopped server
- hooks speak only while the server answers /health

Dependencies: bash, jq (hooks and CLI use it)
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TTS = REPO / "bin" / "tts"
STOP_HOOK = REPO / "hooks" / "tts-stop.sh"
STATUS_HOOK = REPO / "hooks" / "tts-status.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="needs bash and jq",
)

# State lives in files so every stub invocation (separate process) shares it:
#   loaded   -> agent is bootstrapped and its server answers /health
#   disabled -> `launchctl disable` was called (what `tts off` does)
#   calls    -> ordered log of launchctl/curl/PlistBuddy invocations
FAKE_LAUNCHCTL = """#!/bin/bash
S="$FAKE_STATE"
echo "launchctl $*" >> "$S/calls"
case "$1" in
  print-disabled)
    if [ -f "$S/disabled" ]; then v=disabled; else v=enabled; fi
    printf '{\\n\\t"com.claude-code.tts" => %s\\n}\\n' "$v" ;;
  print)
    if [ -f "$S/loaded" ]; then echo "state = running"; exit 0; fi
    exit 113 ;;
  enable)  rm -f "$S/disabled" ;;
  disable) touch "$S/disabled" ;;
  bootstrap)
    # Real launchd: "Bootstrap failed: 5: Input/output error" for disabled services.
    if [ -f "$S/disabled" ]; then echo "Bootstrap failed: 5" >&2; exit 5; fi
    touch "$S/loaded" ;;
  bootout) rm -f "$S/loaded" ;;
  kickstart) [ -f "$S/loaded" ] ;;
esac
exit 0
"""

FAKE_CURL = """#!/bin/bash
S="$FAKE_STATE"
for a in "$@"; do case "$a" in http://*) url="$a" ;; esac; done
echo "curl $url" >> "$S/calls"
case "$url" in
  */health) if [ -f "$S/loaded" ]; then echo '{"ok": true}'; exit 0; fi; exit 22 ;;
esac
exit 0
"""

FAKE_PLISTBUDDY = """#!/bin/bash
echo "plistbuddy $*" >> "$FAKE_STATE/calls"
exit 0
"""


class Env:
    def __init__(self, tmp_path: Path):
        self.state = tmp_path / "state"
        self.state.mkdir()
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        for name, body in (
            ("launchctl", FAKE_LAUNCHCTL),
            ("curl", FAKE_CURL),
            ("plistbuddy", FAKE_PLISTBUDDY),
        ):
            path = fakebin / name
            path.write_text(body)
            path.chmod(0o755)
        home = tmp_path / "home"
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.claude-code.tts.plist").write_text("<plist/>")
        self.env = {
            "HOME": str(home),
            "PATH": f"{fakebin}:/usr/bin:/bin",
            "FAKE_STATE": str(self.state),
            "PLISTBUDDY": str(fakebin / "plistbuddy"),
            "TTS_PORT": "7852",
        }

    def set_on(self) -> None:
        (self.state / "disabled").unlink(missing_ok=True)
        (self.state / "loaded").touch()

    def set_off(self) -> None:
        (self.state / "disabled").touch()
        (self.state / "loaded").unlink(missing_ok=True)

    @property
    def loaded(self) -> bool:
        return (self.state / "loaded").exists()

    @property
    def disabled(self) -> bool:
        return (self.state / "disabled").exists()

    def calls(self) -> list[str]:
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def clear_calls(self) -> None:
        (self.state / "calls").unlink(missing_ok=True)

    def run(self, script: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=30,
        )

    def tts(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(TTS, *args)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def index_of(calls: list[str], needle: str) -> int:
    return next(i for i, c in enumerate(calls) if needle in c)


def test_on_enables_before_bootstrap_when_disabled(env: Env) -> None:
    env.set_off()

    result = env.tts("on")

    assert result.returncode == 0, result.stderr
    assert env.loaded and not env.disabled
    calls = env.calls()
    assert index_of(calls, "launchctl enable") < index_of(calls, "launchctl bootstrap")
    assert "TTS: on" in result.stdout


def test_off_stops_playback_then_disables_and_unloads(env: Env) -> None:
    env.set_on()

    result = env.tts("off")

    assert result.returncode == 0, result.stderr
    assert env.disabled and not env.loaded
    calls = env.calls()
    # Playback is stopped while the server can still answer; disable comes before
    # bootout so KeepAlive cannot respawn it.
    assert index_of(calls, "/stop") < index_of(calls, "launchctl disable")
    assert index_of(calls, "launchctl disable") < index_of(calls, "launchctl bootout")


def test_off_survives_when_server_already_down(env: Env) -> None:
    env.set_off()

    result = env.tts("off")

    assert result.returncode == 0, result.stderr
    assert env.disabled and not env.loaded


def test_toggle_flips_state_both_ways(env: Env) -> None:
    env.set_on()

    env.tts("toggle")
    assert env.disabled and not env.loaded

    env.tts("toggle")
    assert env.loaded and not env.disabled


@pytest.mark.parametrize(
    ("setup", "expected"), [("set_on", "state:   on"), ("set_off", "state:   off")]
)
def test_status_reports_launchd_state(env: Env, setup: str, expected: str) -> None:
    getattr(env, setup)()

    result = env.tts("status")

    assert expected in result.stdout


@pytest.mark.parametrize("command", [["test"], ["restart"]])
def test_commands_needing_a_server_refuse_while_off(env: Env, command: list[str]) -> None:
    env.set_off()

    result = env.tts(*command)

    assert result.returncode == 1
    assert "TTS is off" in result.stderr
    assert not env.loaded
    assert not any("bootstrap" in c or "/speak" in c for c in env.calls())


def test_config_command_while_off_only_saves(env: Env) -> None:
    env.set_off()

    result = env.tts("voice", "ru-RU-DmitryNeural")

    assert result.returncode == 0, result.stderr
    assert "saved; applies on next 'tts on'" in result.stdout
    assert not env.loaded
    calls = env.calls()
    assert any("plistbuddy" in c and "TTS_VOICE" in c for c in calls)
    assert not any("bootstrap" in c or "kickstart" in c for c in calls)


def test_config_command_while_on_reloads_server(env: Env) -> None:
    env.set_on()

    result = env.tts("voice", "ru-RU-DmitryNeural")

    assert result.returncode == 0, result.stderr
    assert "server restarted" in result.stdout
    assert env.loaded
    assert any("launchctl bootstrap" in c for c in env.calls())


def wait_for_call(env: Env, needle: str, timeout: float = 3.0) -> bool:
    # The Stop hook backgrounds its POST, so the call lands slightly after exit.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in c for c in env.calls()):
            return True
        time.sleep(0.05)
    return False


def test_stop_hook_speaks_only_while_server_is_up(env: Env) -> None:
    payload = '{"last_assistant_message": "hello"}'

    env.set_off()
    env.run(STOP_HOOK, stdin=payload)
    assert not wait_for_call(env, "/speak", timeout=0.5)

    env.clear_calls()
    env.set_on()
    env.run(STOP_HOOK, stdin=payload)
    assert wait_for_call(env, "/speak")


def test_status_hook_is_silent_when_off_and_reports_when_on(env: Env) -> None:
    env.set_off()
    assert env.run(STATUS_HOOK).stdout.strip() == ""

    env.set_on()
    assert "TTS: on" in env.run(STATUS_HOOK).stdout
