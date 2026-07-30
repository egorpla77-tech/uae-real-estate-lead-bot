import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sheets_sync import HEADERS, SheetsSync, is_audi_vw_lead


class SheetsSyncBrandTests(unittest.TestCase):
    def test_manager_columns_are_first(self):
        self.assertEqual(
            HEADERS[:7],
            ["ДАТА", "ПРОЕКТ", "СТАТУС", "ОТВЕТСТВЕННЫЙ", "ТЕКСТ", "ССЫЛКА", "ссылка клиента"],
        )

    def test_audi_and_vw_markers_are_detected(self):
        self.assertTrue(is_audi_vw_lead("Volkswagen Tharu под ключ в Москве"))
        self.assertTrue(is_audi_vw_lead("Ауди Q5 сколько будет стоить?"))
        self.assertTrue(is_audi_vw_lead("Фольцваген Tayron из Китая"))

    def test_other_brands_are_not_brand_tab_leads(self):
        self.assertFalse(is_audi_vw_lead("Toyota Harrier сколько до Омска?"))
        self.assertFalse(is_audi_vw_lead("Nissan Leaf цена?"))

    def test_target_tab_receives_lead_without_default_tab_duplicate(self):
        sync = SheetsSync(
            enabled=True,
            spreadsheet_id="",
            credentials_file="",
            credentials_json="",
            script_url="https://example.test/script",
            script_secret="secret",
            tab="Лиды",
            project="Дома",
            base_dir=Path.cwd(),
        )
        sync._append_extra_lead = Mock(return_value=True)
        with patch.dict(os.environ, {"GOOGLE_SHEETS_TARGET_TAB": "Дома"}, clear=False):
            self.assertTrue(sync.append_lead(lead_id="house-1", created_at=0, source="Строительство домов"))
        sync._append_extra_lead.assert_called_once()
        self.assertEqual("Дома", sync._append_extra_lead.call_args.kwargs["tab"])


if __name__ == "__main__":
    unittest.main()
