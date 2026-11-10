"""WSL2 detection and the usbipd-win steps shown by doctor."""

from __future__ import annotations

import unittest

from src.console import wsl


class WslDetectionTests(unittest.TestCase):
    def test_distro_name_is_wsl(self):
        self.assertTrue(wsl.is_wsl({"WSL_DISTRO_NAME": "Ubuntu-24.04"}, "6.6.0"))

    def test_interop_variable_is_wsl(self):
        self.assertTrue(wsl.is_wsl({"WSL_INTEROP": "/run/WSL/8_interop"}, "6.6.0"))

    def test_microsoft_kernel_is_wsl(self):
        self.assertTrue(
            wsl.is_wsl({}, "5.15.153.1-microsoft-standard-WSL2")
        )

    def test_native_linux_is_not_wsl(self):
        self.assertFalse(wsl.is_wsl({}, "6.8.0-45-generic"))
        self.assertFalse(wsl.is_wsl({}, "14.7.0"))

    def test_report_has_no_steps_off_wsl(self):
        result = wsl.report({}, "6.8.0-45-generic")
        self.assertFalse(result["wsl"])
        self.assertEqual(result["wsl_steps"], [])

    def test_report_names_attach_and_mtkclient(self):
        result = wsl.report({"WSL_DISTRO_NAME": "Debian"}, "6.6.0")
        self.assertTrue(result["wsl"])
        self.assertEqual(result["wsl_distro"], "Debian")
        text = "\n".join(result["wsl_steps"])
        self.assertIn("usbipd attach --wsl", text)
        self.assertIn("usbipd bind", text)
        self.assertIn("download mode", text)
        self.assertIn("attach again", text)

    def test_startup_note_names_distro_and_doctor(self):
        note = wsl.startup_note()
        self.assertIn("Windows Subsystem for Linux", note)
        self.assertIn("doctor", note)
        self.assertIn("usbipd-win", note)


if __name__ == "__main__":
    unittest.main()
