import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lib import model  # noqa: E402


def make_pkg(**overrides) -> model.Package:
    data = {
        "id": "demo",
        "type": "github",
        "repo": "owner/repo",
        "asset": "demo-{version}-linux.tar.gz",
        "bins": ("demo",),
        "description": "",
        "note": "",
        "install": "tar -xzf {asset} && install -m755 demo-{version}/demo ~/.local/bin/",
        "version_regex": "",
    }
    data.update(overrides)
    return model.Package(**data)


class VersionTest(unittest.TestCase):
    def test_strip_v_prefix(self):
        self.assertEqual(model.version_from_tag("v10.5.0"), "10.5.0")

    def test_plain_tag(self):
        self.assertEqual(model.version_from_tag("15.2.0"), "15.2.0")

    def test_custom_regex(self):
        self.assertEqual(model.version_from_tag("jq-1.8.2", r"^jq-(.+)$"), "1.8.2")

    def test_regex_mismatch(self):
        with self.assertRaises(ValueError):
            model.version_from_tag("jq-1.8.2", r"^v(.+)$")


class AssetTest(unittest.TestCase):
    def test_asset_name(self):
        pkg = make_pkg()
        self.assertEqual(model.asset_name(pkg, "1.2.3", "v1.2.3"), "demo-1.2.3-linux.tar.gz")

    def test_asset_name_with_tag(self):
        pkg = make_pkg(asset="demo-{tag}.zip")
        self.assertEqual(model.asset_name(pkg, "1.2.3", "v1.2.3"), "demo-v1.2.3.zip")

    def test_pick_asset(self):
        assets = [{"name": "a"}, {"name": "b"}]
        self.assertEqual(model.pick_asset(assets, "b")["name"], "b")

    def test_pick_asset_missing(self):
        with self.assertRaises(LookupError):
            model.pick_asset([{"name": "a"}], "b")

    def test_install_command(self):
        pkg = make_pkg()
        self.assertEqual(
            model.install_command(pkg, "demo-1.2.3-linux.tar.gz", "1.2.3"),
            "tar -xzf demo-1.2.3-linux.tar.gz && install -m755 demo-1.2.3/demo ~/.local/bin/",
        )


class RetentionTest(unittest.TestCase):
    def test_keep_newest(self):
        entries = [
            {"name": "a", "built_at": "2026-01-01T00:00:00Z"},
            {"name": "b", "built_at": "2026-03-01T00:00:00Z"},
            {"name": "c", "built_at": "2026-02-01T00:00:00Z"},
        ]
        keep, drop = model.split_retained(entries, 2)
        self.assertEqual([e["name"] for e in keep], ["b", "c"])
        self.assertEqual([e["name"] for e in drop], ["a"])

    def test_keep_all_when_below_limit(self):
        entries = [{"name": "a", "built_at": "2026-01-01T00:00:00Z"}]
        keep, drop = model.split_retained(entries, 3)
        self.assertEqual(len(keep), 1)
        self.assertEqual(drop, [])


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.cfg = model.Config(
            release_tag="bundles",
            release_name="Bundles",
            retention=3,
            platform=model.Platform(),
        )
        self.state = {
            "schema": 1,
            "packages": {
                "fd": {
                    "version": "10.5.0",
                    "upstream": "https://example.invalid/fd",
                    "description": "fd desc",
                    "note": "",
                    "bins": ["fd"],
                    "assets": [
                        {"name": "fd-10.4.2.tar.gz", "sha256": "old", "size": 1,
                         "built_at": "2026-01-01T00:00:00Z", "install": "old"},
                        {"name": "fd-10.5.0.tar.gz", "sha256": "new", "size": 2,
                         "built_at": "2026-02-01T00:00:00Z", "install": "new"},
                    ],
                }
            },
        }

    def test_latest_only(self):
        index = model.make_index(self.cfg, self.state, "o/r", "2026-02-02T00:00:00Z")
        self.assertEqual(len(index["packages"]), 1)
        item = index["packages"][0]
        self.assertEqual(item["asset"], "fd-10.5.0.tar.gz")
        self.assertEqual(item["sha256"], "new")
        self.assertEqual(item["url"], "https://github.com/o/r/releases/download/bundles/fd-10.5.0.tar.gz")

    def test_body_contains_package(self):
        body = model.release_body(self.cfg, self.state, "o/r")
        self.assertIn("`fd`", body)
        self.assertIn("fd-10.5.0.tar.gz", body)


class LoadPackagesTest(unittest.TestCase):
    def test_repository_packages_load(self):
        packages = model.load_packages(ROOT)
        ids = {p.id for p in packages}
        self.assertTrue({"ripgrep", "fd", "clangd"} <= ids)

    def test_filter(self):
        packages = model.load_packages(ROOT, ["fd"])
        self.assertEqual([p.id for p in packages], ["fd"])

    def test_missing_name_rejected(self):
        with self.assertRaises(SystemExit):
            model.load_packages(ROOT, ["does-not-exist"])


if __name__ == "__main__":
    unittest.main()
