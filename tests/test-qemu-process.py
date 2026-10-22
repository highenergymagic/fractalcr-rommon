#!/usr/bin/env python3
"""Exercise shutdown against real PTY children, including delayed termination."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pexpect
from qemu_process import finish_qemu, stop_qemu


class ShutdownTests(unittest.TestCase):
    def spawn(self, handler):
        process = pexpect.spawn(sys.executable, ["-u", "-c",
            "import signal,time,sys\n" + handler +
            "\nprint('ready', flush=True)\nwhile True: time.sleep(1)\n"],
            encoding="utf-8", timeout=5)
        self.addCleanup(lambda: stop_qemu(process))
        process.expect_exact("ready")
        return process

    def test_delayed_term_is_waited_for(self):
        process = self.spawn(
            "def quit_later(*args):\n time.sleep(0.5)\n sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, quit_later)")
        stop_qemu(process, timeout=2)
        self.assertTrue(process.closed)
        self.assertEqual(process.exitstatus, 0)

    def test_ignored_term_escalates_and_reaps(self):
        process = self.spawn("signal.signal(signal.SIGTERM, signal.SIG_IGN)")
        stop_qemu(process, timeout=0.3)
        self.assertTrue(process.closed)
        self.assertEqual(process.signalstatus, 9)
        stop_qemu(process)  # Already closed is safe.

    def test_cleanup_error_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "result.json"
            result = {"passed": True}
            with patch("qemu_process.stop_qemu", side_effect=RuntimeError("still alive")):
                with self.assertRaisesRegex(RuntimeError, "still alive"):
                    finish_qemu(None, result, report)
            saved = json.loads(report.read_text())
            self.assertFalse(saved["passed"])
            self.assertEqual(saved["cleanup_error"], "still alive")


if __name__ == "__main__":
    unittest.main()
