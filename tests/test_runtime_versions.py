"""The UI runtime and Python Agent must ship the same MaaFramework release."""
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RuntimeVersionTest(unittest.TestCase):
    def test_agent_lock_and_packaged_framework_share_one_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
        dependencies = project["project"]["dependencies"]
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        dependency = next(dependency for dependency in dependencies if dependency.startswith("maafw"))
        spec = re.fullmatch(r"maafw(==|>=)(\d+\.\d+\.\d+)", dependency)
        self.assertIsNotNone(spec)
        version = next(package["version"] for package in lock["package"] if package["name"] == "maafw")
        if spec.group(1) == "==":
            self.assertEqual(version, spec.group(2))
        else:
            self.assertGreaterEqual(tuple(map(int, version.split('.'))), tuple(map(int, spec.group(2).split('.'))))
        self.assertIn("maafw==" + version, requirements)
        workflow = (ROOT / ".github/workflows/install.yml").read_text(encoding="utf-8")
        match = re.search(r'^\s+MAAFW_VERSION: "v([^\"]+)"', workflow, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), version)


if __name__ == "__main__":
    unittest.main()
