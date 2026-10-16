from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "Windows release builder")
class ReleaseBuildPreflightTests(unittest.TestCase):
    def test_invalid_wheels_fail_before_creating_release_output(self):
        root = Path(__file__).resolve().parents[1]
        output = root / "release" / "OA阅答-v9876.5432.1098-轻量版-免安装版"
        self.assertFalse(output.exists(), "Reserved test version already exists")
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / "office_oxide-0.1.15-cp310-abi3-win_amd64.whl"
            for present in (False, True):
                with self.subTest(tampered=present):
                    if present:
                        wheel.write_bytes(b"invalid wheel")
                    result = subprocess.run(
                        [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", str(root / "desktop" / "build_v1.ps1"),
                         "-SkipLibreOffice", "-Version", "9876.5432.1098",
                         "-PythonRuntime", directory, "-OfficeOxideWheel", str(wheel)],
                        capture_output=True, timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    expected = b"SHA-256 verification failed" if present else b"requires OfficeOxide"
                    self.assertIn(expected, result.stderr)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
