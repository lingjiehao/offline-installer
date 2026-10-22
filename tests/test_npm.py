import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import npm  # noqa: E402


class ParseSpecTest(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(npm.parse_spec("pi-subagents"), ("pi-subagents", "latest"))

    def test_pinned(self):
        self.assertEqual(npm.parse_spec("pi-subagents@0.80.0"), ("pi-subagents", "0.80.0"))

    def test_scoped(self):
        self.assertEqual(npm.parse_spec("@scope/tool"), ("@scope/tool", "latest"))
        self.assertEqual(npm.parse_spec("@scope/tool@1.2.3"), ("@scope/tool", "1.2.3"))

    def test_empty_version_means_latest(self):
        self.assertEqual(npm.parse_spec("tool@"), ("tool", "latest"))


class TreeVersionsTest(unittest.TestCase):
    def test_collects_nested_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path, name, version in [
                ("pkg-a/node_modules/jiti/package.json", "jiti", "2.7.0"),
                ("pkg-a/node_modules/yaml/package.json", "yaml", "2.8.3"),
                ("pkg-a/node_modules/undici/node_modules/nested/package.json", "nested", "1.0.0"),
            ]:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f'{{"name": "{name}", "version": "{version}"}}', encoding="utf-8")
            self.assertEqual(npm.tree_versions(root), ["jiti@2.7.0", "nested@1.0.0", "yaml@2.8.3"])

    def test_ignores_regular_package_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")
            self.assertEqual(npm.tree_versions(root), [])


if __name__ == "__main__":
    unittest.main()
