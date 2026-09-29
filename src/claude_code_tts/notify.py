"""
NOTIFY

Fire-and-forget macOS notification via `osascript`.

Business rules:
  - Never raises and never blocks the caller: runs in a daemon thread, errors go to stderr.
    (On Linux CI osascript is missing; the FileNotFoundError is swallowed the same way.)
  - Message and title travel as argv items, never interpolated into the AppleScript source,
    so quotes in the text cannot break out of the script.

⚠️ WARNING: UNVERIFIED from a launchd LaunchAgent. Notifications may show up under
"Script Editor" and need a one-time allow in System Settings > Notifications.
"""

import subprocess
import sys
import threading

_APPLESCRIPT = [
    "on run argv",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "end run",
]


def _run(title: str, message: str) -> None:
    try:
        cmd = ["osascript"]
        for line in _APPLESCRIPT:
            cmd += ["-e", line]
        subprocess.run(cmd + [message, title], timeout=5, capture_output=True)
    except Exception as e:
        print(f"[tts] notification failed: {e}", file=sys.stderr, flush=True)


def send(title: str, message: str) -> None:
    threading.Thread(target=_run, args=(title, message), daemon=True).start()
