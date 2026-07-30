import unittest
from pathlib import Path

from regional_sources import load_regional_sources, source_city_map, source_names


class RegionalSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(__file__).resolve().parents[1] / "regional_sources.json"

    def test_catalog_covers_all_sixteen_cities(self) -> None:
        items = load_regional_sources(self.path)
        self.assertEqual(162, len(items))
        self.assertEqual(16, len({item.city for item in items}))
        self.assertIn("Москва", {item.city for item in items})
        self.assertIn("Санкт-Петербург", {item.city for item in items})

    def test_platform_lists_are_separate_and_unique(self) -> None:
        vk = load_regional_sources(self.path, "vk")
        telegram = load_regional_sources(self.path, "telegram")
        self.assertEqual(71, len(vk))
        self.assertEqual(91, len(telegram))
        self.assertEqual(len(vk), len({name.lower() for name in source_names(vk)}))
        self.assertEqual(len(telegram), len({name.lower() for name in source_names(telegram)}))
        self.assertEqual("Москва", source_city_map(telegram)["investmoscowru"])


if __name__ == "__main__":
    unittest.main()
