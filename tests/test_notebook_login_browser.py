import tempfile
import unittest
import sqlite3
from pathlib import Path

from bridge.notebooklm.cdp import BrowserSessionCapture


class NotebookLoginBrowserSecurityTests(unittest.TestCase):
    def test_interactive_google_login_does_not_enable_remote_debugging(self) -> None:
        """Google sign-in must happen in a normal, supported local browser."""
        with tempfile.TemporaryDirectory() as directory:
            capture = BrowserSessionCapture(Path("chrome.exe"), Path(directory))
            command = capture.interactive_login_command()

        joined = " ".join(command)
        self.assertNotIn("--remote-debugging", joined)
        self.assertNotIn("--remote-allow-origins", joined)
        self.assertIn("--user-data-dir=", joined)
        self.assertTrue(joined.endswith("https://notebook.google.com/"))

    def test_login_completion_is_detected_without_reading_cookie_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            database = profile / "Default" / "Network" / "Cookies"
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE cookies (name TEXT, host_key TEXT, encrypted_value BLOB)")
            connection.executemany(
                "INSERT INTO cookies VALUES (?, ?, ?)",
                [(name, ".google.com", b"encrypted") for name in BrowserSessionCapture.REQUIRED_COOKIE_NAMES],
            )
            connection.commit()
            connection.close()

            capture = BrowserSessionCapture(Path("chrome.exe"), profile)
            self.assertTrue(capture._required_login_cookies_present())


if __name__ == "__main__":
    unittest.main()
