"""
Tests for scripts/patch_settings.py — the script is added to sys.path via
pytest.ini (pythonpath = src scripts).
"""

import json
from pathlib import Path

import patch_settings as ps

REPO_DIR = "/Users/someone/dev/claude-code-tts"
LEGACY_DIR = "/Users/kos/.claude/skills/tts"


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_on_empty(tmp_path):
    settings = tmp_path / "settings.json"
    _write(settings, {})
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    data = _load(settings)
    stop = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    sess = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert stop == f"bash {REPO_DIR}/hooks/tts-stop.sh"
    assert sess == f"bash {REPO_DIR}/hooks/tts-status.sh"


def test_install_preserves_unrelated_hooks(tmp_path):
    settings = tmp_path / "settings.json"
    _write(
        settings,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Edit|Write",
                        "hooks": [{"type": "command", "command": "bash /some/other/hook.sh"}],
                    }
                ]
            }
        },
    )
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    data = _load(settings)
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "bash /some/other/hook.sh"
    assert "Stop" in data["hooks"]
    assert "SessionStart" in data["hooks"]


def test_install_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    _write(settings, {})
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    data = _load(settings)
    assert len(data["hooks"]["Stop"]) == 1
    assert len(data["hooks"]["SessionStart"]) == 1


def test_install_migrates_legacy_hook(tmp_path):
    settings = tmp_path / "settings.json"
    _write(
        settings,
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"bash {LEGACY_DIR}/tts.sh",
                                "timeout": 5000,
                            }
                        ]
                    }
                ],
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"bash {LEGACY_DIR}/tts-status.sh",
                                "timeout": 3000,
                            }
                        ]
                    }
                ],
            }
        },
    )
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    data = _load(settings)
    assert len(data["hooks"]["Stop"]) == 1
    assert LEGACY_DIR not in json.dumps(data)


def test_uninstall_removes_only_ours(tmp_path):
    settings = tmp_path / "settings.json"
    _write(
        settings,
        {
            "hooks": {
                "PreToolUse": [{"hooks": [{"type": "command", "command": "bash /other/hook.sh"}]}]
            }
        },
    )
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    ps.main(["uninstall", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    data = _load(settings)
    assert "Stop" not in data.get("hooks", {})
    assert "SessionStart" not in data.get("hooks", {})
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "bash /other/hook.sh"


def test_uninstall_on_empty_is_noop(tmp_path):
    settings = tmp_path / "settings.json"
    _write(settings, {"unrelated": True})
    ps.main(["uninstall", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    assert _load(settings) == {"unrelated": True}


def test_install_creates_file_when_missing(tmp_path):
    settings = tmp_path / "nested" / "settings.json"
    assert not settings.exists()
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings)])
    assert settings.exists()
    data = _load(settings)
    assert "Stop" in data["hooks"]
    assert "SessionStart" in data["hooks"]


def test_backup_flag_creates_backup(tmp_path):
    settings = tmp_path / "settings.json"
    _write(settings, {"hooks": {}})
    ps.main(["install", "--repo-dir", REPO_DIR, "--settings", str(settings), "--backup"])
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1
