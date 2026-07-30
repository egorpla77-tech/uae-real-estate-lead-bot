import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


HEADERS = [
    "ДАТА",
    "ПРОЕКТ",
    "СТАТУС",
    "ОТВЕТСТВЕННЫЙ",
    "ТЕКСТ",
    "ССЫЛКА",
    "ссылка клиента",
    "Lead ID",
    "Телефон",
    "Имя/контакт",
    "Источник",
    "Тип/намерение",
    "Маркер",
    "Направление/город",
    "Объект/запрос",
    "Комментарий менеджера",
    "Дата обновления",
]

REELS_HEADERS = [
    "Дата добавления",
    "Дата публикации",
    "Источник",
    "Ссылка на Reels/пост",
    "Статус",
    "Комментарий менеджера",
    "Последняя проверка",
    "Reels/пост ID",
    "Ссылка на источник",
    "Заголовок/описание",
]

STATUS_VALUES = ["Новый", "В работе", "Связались", "Отказ", "Не лид", "Дубль", "Закрыт"]
REELS_STATUS_VALUES = ["Новый", "Проверить", "Проверили", "Есть лиды", "Нет лидов", "Неактуально"]
AUDI_VW_RE = re.compile(
    r"(?i)(?:\baudi\b|ауди|\bvolkswagen\b|\bfolkswagen\b|\bvw\b|\bvag\b|"
    r"фольксваген|фольцваген|фольсваген|фольцв[а-я]*|"
    r"\bjetta\b|\bvs5\b|\bvs7\b|\bmagotan\b|\bpassat\b|\btharu\b|\btayron\b|\btiguan\b|\btouareg\b|\bgolf\b|"
    r"\bid[\s.-]?[3467]\b|\be-?tron\b|"
    r"\ba3\b|\ba4\b|\ba5\b|\ba6\b|\ba7\b|\ba8\b|\bq2\b|\bq3\b|\bq5\b|\bq7\b|\bq8\b)"
)
STATUS_LABELS = {
    "new": "Новый",
    "work": "В работе",
    "contact": "Связались",
    "reject": "Не лид",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "да", "on"}


def _sheet_range(tab: str, cell_range: str) -> str:
    safe_tab = tab.replace("'", "''")
    return f"'{safe_tab}'!{cell_range}"


def _clean(value: Any, limit: int = 2000) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _format_ts(timestamp: int) -> str:
    if timestamp:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def is_audi_vw_lead(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values)
    return bool(AUDI_VW_RE.search(text))


class SheetsSync:
    def __init__(
        self,
        enabled: bool,
        spreadsheet_id: str,
        credentials_file: str,
        credentials_json: str,
        script_url: str,
        script_secret: str,
        tab: str,
        project: str,
        base_dir: Path,
    ) -> None:
        self.enabled = enabled
        self.spreadsheet_id = spreadsheet_id.strip()
        self.credentials_file = credentials_file.strip()
        self.credentials_json = credentials_json.strip()
        self.script_url = script_url.strip()
        self.script_secret = script_secret.strip()
        self.tab = tab.strip() or "Лиды"
        self.project = project
        self.base_dir = base_dir
        self._service: Optional[Any] = None
        self._sheet_id: Optional[int] = None
        self._extra_sheet_ids: Dict[str, int] = {}
        self._ready = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, base_dir: Path, project: str) -> "SheetsSync":
        return cls(
            enabled=_env_bool("GOOGLE_SHEETS_ENABLED", False),
            spreadsheet_id=os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", ""),
            credentials_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "data/google_service_account.json"),
            credentials_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
            script_url=os.getenv("GOOGLE_APPS_SCRIPT_URL", ""),
            script_secret=os.getenv("GOOGLE_APPS_SCRIPT_SECRET", ""),
            tab=os.getenv("GOOGLE_SHEETS_TAB", "Лиды"),
            project=os.getenv("GOOGLE_SHEETS_PROJECT", project),
            base_dir=base_dir,
        )

    def append_lead(
        self,
        *,
        lead_id: str,
        created_at: int,
        source: str,
        source_url: str = "",
        category: str = "",
        marker: str = "",
        direction: str = "",
        subject: str = "",
        text: str = "",
        lead_url: str = "",
        client_url: str = "",
        status: str = "Новый",
        extra_tabs: Optional[List[str]] = None,
    ) -> bool:
        if not self.enabled:
            return False
        row = [
            _format_ts(created_at),
            self.project,
            STATUS_LABELS.get(status, status or "Новый"),
            "",
            _clean(text, limit=4000),
            _clean(lead_url, limit=1000),
            _clean(client_url, limit=1000),
            lead_id,
            "",
            "",
            _clean(source),
            _clean(category),
            _clean(marker),
            _clean(direction),
            _clean(subject),
            "",
            "",
        ]
        brand_tab = os.getenv("GOOGLE_SHEETS_AUDI_VW_TAB", "Audi Volkswagen").strip() or "Audi Volkswagen"
        should_duplicate_brand = is_audi_vw_lead(subject, text, marker)
        duplicate_tabs: List[str] = []
        if should_duplicate_brand:
            duplicate_tabs.append(brand_tab)
        for tab in extra_tabs or []:
            tab = str(tab or "").strip()
            if tab:
                duplicate_tabs.append(tab)
        duplicate_tabs = list(dict.fromkeys(tab for tab in duplicate_tabs if tab and tab != self.tab))
        target_tab = os.getenv("GOOGLE_SHEETS_TARGET_TAB", "").strip()
        if target_tab:
            return self._append_extra_lead(lead_id=lead_id, row=row, tab=target_tab)
        if self.script_url:
            ok = self._post_script(
                {
                    "action": "append",
                    "lead_id": lead_id,
                    "row": row,
                }
            )
            if ok:
                for tab in duplicate_tabs:
                    self._append_extra_lead(lead_id=lead_id, row=row, tab=tab)
            return ok
        try:
            if not self._ensure_ready():
                return False
            with self._lock:
                self._service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=_sheet_range(self.tab, "A:Q"),
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                ).execute()
                for tab in duplicate_tabs:
                    self._append_extra_lead_direct(lead_id=lead_id, row=row, tab=tab)
            return True
        except Exception as exc:
            logging.warning("Google Sheets: не удалось добавить лид %s: %s", lead_id, exc)
            return False

    def _append_brand_lead(self, *, lead_id: str, row: List[Any], tab: str) -> bool:
        return self._append_extra_lead(lead_id=lead_id, row=row, tab=tab)

    def _append_extra_lead(self, *, lead_id: str, row: List[Any], tab: str) -> bool:
        if self.script_url:
            ok = self._post_script(
                {
                    "action": "append_brand_lead",
                    "tab": tab,
                    "lead_id": lead_id,
                    "row": row,
                }
            )
            if not ok:
                logging.warning("Google Sheets: не удалось продублировать лид %s во вкладку %s", lead_id, tab)
            return ok
        return self._append_extra_lead_direct(lead_id=lead_id, row=row, tab=tab)

    def _append_brand_lead_direct(self, *, lead_id: str, row: List[Any], tab: str) -> bool:
        return self._append_extra_lead_direct(lead_id=lead_id, row=row, tab=tab)

    def _append_extra_lead_direct(self, *, lead_id: str, row: List[Any], tab: str) -> bool:
        try:
            if not self._ensure_ready():
                return False
            sheet_id = self._ensure_extra_sheet(tab)
            self._ensure_extra_headers(tab, HEADERS)
            existing = self._service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=_sheet_range(tab, "H2:H"),
            ).execute()
            for existing_row in existing.get("values", []):
                if existing_row and str(existing_row[0]) == lead_id:
                    return True
            self._format_sheet(sheet_id=sheet_id, tab=tab)
            self._service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=_sheet_range(tab, "A:Q"),
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            return True
        except Exception as exc:
            logging.warning("Google Sheets: не удалось добавить лид %s во вкладку %s: %s", lead_id, tab, exc)
            return False

    def append_reel(
        self,
        *,
        reel_id: str,
        reel_url: str,
        published_at: int,
        source: str,
        source_url: str = "",
        title: str = "",
        tab: str = "",
    ) -> bool:
        if not self.enabled:
            return False
        reels_tab = (tab or os.getenv("GOOGLE_SHEETS_REELS_TAB", "Свежие Reels")).strip() or "Свежие Reels"
        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            _format_ts(published_at),
            _clean(source),
            _clean(reel_url, limit=1000),
            "Новый",
            "",
            "",
            _clean(reel_id, limit=200),
            _clean(source_url, limit=1000),
            _clean(title, limit=1000),
        ]
        if self.script_url:
            return self._post_script(
                {
                    "action": "append_reel",
                    "tab": reels_tab,
                    "reel_id": reel_id,
                    "reel_url": reel_url,
                    "row": row,
                }
            )
        try:
            if not self._ensure_ready():
                return False
            with self._lock:
                sheet_id = self._ensure_extra_sheet(reels_tab)
                self._ensure_extra_headers(reels_tab, REELS_HEADERS)
                existing = self._service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id,
                    range=_sheet_range(reels_tab, "H2:H"),
                ).execute()
                for existing_row in existing.get("values", []):
                    if existing_row and str(existing_row[0]) == reel_id:
                        return True
                self._format_reels_sheet(sheet_id)
                self._service.spreadsheets().values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range=_sheet_range(reels_tab, "A:J"),
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                ).execute()
            return True
        except Exception as exc:
            logging.warning("Google Sheets: не удалось добавить Reels %s: %s", reel_id, exc)
            return False

    def update_status(self, lead_id: str, status: str) -> bool:
        if not lead_id:
            return False
        label = STATUS_LABELS.get(status, status)
        if self.script_url:
            return self._post_script(
                {
                    "action": "status",
                    "lead_id": lead_id,
                    "status": label,
                    "updated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
                }
            )
        try:
            if not self._ensure_ready():
                return False
            with self._lock:
                result = self._service.spreadsheets().values().get(
                    spreadsheetId=self.spreadsheet_id,
                    range=_sheet_range(self.tab, "H2:H"),
                ).execute()
                values = result.get("values", [])
                row_index = 0
                for index, row in enumerate(values, start=2):
                    if row and str(row[0]) == lead_id:
                        row_index = index
                        break
                if not row_index:
                    return False
                self._service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={
                        "valueInputOption": "USER_ENTERED",
                        "data": [
                            {"range": _sheet_range(self.tab, f"C{row_index}"), "values": [[label]]},
                            {
                                "range": _sheet_range(self.tab, f"Q{row_index}"),
                                "values": [[datetime.now().strftime("%d.%m.%Y %H:%M")]],
                            },
                        ],
                    },
                ).execute()
            return True
        except Exception as exc:
            logging.warning("Google Sheets: не удалось обновить статус %s: %s", lead_id, exc)
            return False

    def _post_script(self, payload: Dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if not self.script_secret:
            logging.warning("Google Sheets: не задан GOOGLE_APPS_SCRIPT_SECRET")
            return False
        body = dict(payload)
        body["secret"] = self.script_secret
        body["project"] = self.project
        try:
            with self._lock:
                response = requests.post(self.script_url, json=body, timeout=20)
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                logging.warning("Google Sheets: Apps Script вернул ошибку: %s", result)
                return False
            return True
        except Exception as exc:
            logging.warning("Google Sheets: не удалось отправить данные в Apps Script: %s", exc)
            return False

    def _ensure_ready(self) -> bool:
        if not self.enabled:
            return False
        if self._ready:
            return True
        if not self.spreadsheet_id:
            logging.warning("Google Sheets: не задан GOOGLE_SHEETS_SPREADSHEET_ID")
            return False
        try:
            with self._lock:
                if self._ready:
                    return True
                self._service = self._build_service()
                self._sheet_id = self._ensure_sheet()
                self._ensure_headers()
                self._format_sheet()
                self._ready = True
            logging.info("Google Sheets: подключена таблица %s / %s", self.spreadsheet_id, self.tab)
            return True
        except Exception as exc:
            logging.warning("Google Sheets: интеграция недоступна: %s", exc)
            return False

    def _build_service(self) -> Any:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        if self.credentials_json:
            info = json.loads(self.credentials_json)
            credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        else:
            credential_path = Path(self.credentials_file)
            if not credential_path.is_absolute():
                credential_path = self.base_dir / credential_path
            credentials = service_account.Credentials.from_service_account_file(str(credential_path), scopes=scopes)
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _ensure_sheet(self) -> int:
        meta = self._service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == self.tab:
                return int(props.get("sheetId"))
        response = self._service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": self.tab}}}]},
        ).execute()
        return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])

    def _ensure_extra_sheet(self, tab: str) -> int:
        if tab in self._extra_sheet_ids:
            return self._extra_sheet_ids[tab]
        meta = self._service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("title") == tab:
                sheet_id = int(props.get("sheetId"))
                self._extra_sheet_ids[tab] = sheet_id
                return sheet_id
        response = self._service.spreadsheets().batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        sheet_id = int(response["replies"][0]["addSheet"]["properties"]["sheetId"])
        self._extra_sheet_ids[tab] = sheet_id
        return sheet_id

    def _ensure_extra_headers(self, tab: str, headers: List[str]) -> None:
        end_column = chr(ord("A") + len(headers) - 1)
        response = self._service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=_sheet_range(tab, f"A1:{end_column}1"),
        ).execute()
        existing = response.get("values", [[]])[0] if response.get("values") else []
        if existing == headers:
            return
        self._service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=_sheet_range(tab, f"A1:{end_column}1"),
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

    def _ensure_headers(self) -> None:
        response = self._service.spreadsheets().values().get(
            spreadsheetId=self.spreadsheet_id,
            range=_sheet_range(self.tab, "A1:Q1"),
        ).execute()
        existing = response.get("values", [[]])[0] if response.get("values") else []
        if existing == HEADERS:
            return
        self._service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=_sheet_range(self.tab, "A1:Q1"),
            valueInputOption="USER_ENTERED",
            body={"values": [HEADERS]},
        ).execute()

    def _format_sheet(self, sheet_id: Optional[int] = None, tab: str = "") -> None:
        target_sheet_id = sheet_id if sheet_id is not None else self._sheet_id
        if target_sheet_id is None:
            return
        requests: List[Dict[str, Any]] = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": target_sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": target_sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.12, "green": 0.32, "blue": 0.56},
                            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": target_sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 10000,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": value} for value in STATUS_VALUES],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            },
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": target_sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 10000,
                        "startColumnIndex": 3,
                        "endColumnIndex": 4,
                    },
                    "rule": None,
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": target_sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 10000,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5,
                    },
                    "cell": {"userEnteredFormat": {"wrapStrategy": "CLIP"}},
                    "fields": "userEnteredFormat.wrapStrategy",
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": target_sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": len(HEADERS),
                    }
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": target_sheet_id, "dimension": "COLUMNS", "startIndex": 4, "endIndex": 5},
                    "properties": {"pixelSize": 240},
                    "fields": "pixelSize",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": target_sheet_id, "dimension": "COLUMNS", "startIndex": 5, "endIndex": 6},
                    "properties": {"pixelSize": 190},
                    "fields": "pixelSize",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": target_sheet_id, "dimension": "COLUMNS", "startIndex": 6, "endIndex": 7},
                    "properties": {"pixelSize": 190},
                    "fields": "pixelSize",
                }
            },
        ]
        try:
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()
        except Exception as exc:
            logging.debug("Google Sheets: оформление таблицы пропущено: %s", exc)

    def _format_reels_sheet(self, sheet_id: int) -> None:
        requests: List[Dict[str, Any]] = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.22, "green": 0.21, "blue": 0.45},
                            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 10000,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": value} for value in REELS_STATUS_VALUES],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": len(REELS_HEADERS),
                    }
                }
            },
        ]
        try:
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            ).execute()
        except Exception as exc:
            logging.debug("Google Sheets: оформление вкладки Reels пропущено: %s", exc)
