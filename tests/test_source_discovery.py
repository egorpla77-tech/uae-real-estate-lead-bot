import json
import tempfile
import unittest
from pathlib import Path

from source_discovery import SourceDiscovery, is_uae_real_estate_candidate, load_catalog
from storage import JsonStore


class SourceDiscoveryTests(unittest.TestCase):
    def test_catalog_keeps_only_uae_real_estate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {"screen_name": "dubai_property", "name": "Недвижимость Дубая", "region": "dubai"},
                            {"screen_name": "dubai_tours", "name": "Туры в Дубай", "region": "dubai"},
                            {"screen_name": "moscow_property", "name": "Недвижимость Москвы", "region": "msk"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            candidates = load_catalog(path)
        self.assertEqual(["dubai_property"], [item.screen_name for item in candidates])
        self.assertTrue(is_uae_real_estate_candidate("Dubai Real Estate"))
        self.assertFalse(is_uae_real_estate_candidate("Туры и отели Дубая"))

    def test_candidate_can_be_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = JsonStore(Path(directory) / "state.json", {"cursor": 0, "candidates": {}})
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(
                json.dumps(
                    {"candidates": [{"screen_name": "dubai_property", "name": "Dubai Property", "region": "uae"}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            catalog = load_catalog(catalog_path)
            discovery = SourceDiscovery(catalog, state, retry_after_seconds=10, reject_after_seconds=20)
            selected = discovery.select(1, [])
            self.assertEqual(["dubai_property"], [item.screen_name for item in selected])
            status = discovery.record(selected[0], 1, 2, 3, 1)
            self.assertEqual("accepted", status)
            self.assertEqual(["dubai_property"], discovery.accepted_names())


if __name__ == "__main__":
    unittest.main()
