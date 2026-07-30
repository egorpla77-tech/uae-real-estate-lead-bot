import json
import hashlib
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ALIAS_KEY = "_aliases"
INSTAGRAM_BASE_URL_RE = re.compile(r"(https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[^/?#]+)", re.IGNORECASE)


def canonical_lead_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").lower()).strip()
    return text.replace("ё", "е")


def canonical_lead_url(value: str) -> str:
    url = str(value or "").strip()
    match = INSTAGRAM_BASE_URL_RE.search(url)
    if match:
        return match.group(1).rstrip("/") + "/"
    return url.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def lead_dedupe_keys(uid: str, text: str = "", lead_url: str = "", client_url: str = "") -> List[str]:
    keys: List[str] = []
    for raw in (uid,):
        value = str(raw or "").strip()
        if value and value not in keys:
            keys.append(value)

    normalized_text = canonical_lead_text(text)
    normalized_url = canonical_lead_url(lead_url)
    normalized_client = canonical_lead_url(client_url)
    if normalized_text and normalized_url:
        keys.append("url_text:" + hashlib.sha1(f"{normalized_url}|{normalized_text}".encode("utf-8", "ignore")).hexdigest())
    if normalized_text and normalized_client:
        keys.append("client_text:" + hashlib.sha1(f"{normalized_client}|{normalized_text}".encode("utf-8", "ignore")).hexdigest())
    if len(normalized_text) >= 20 and len(normalized_text.split()) >= 3:
        keys.append("text:" + hashlib.sha1(normalized_text.encode("utf-8", "ignore")).hexdigest())
    return list(dict.fromkeys(keys))


class JsonStore:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = path
        self.default = default
        self.lock = threading.Lock()

    def read(self) -> Any:
        if not self.path.exists():
            return self.default
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.default

    def write(self, value: Any) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)


class AccessStore:
    """Неизменяемая очередь первых пользователей, нажавших /start."""

    def __init__(self, path: Path, limit: int = 2) -> None:
        self.store = JsonStore(path, {"users": []})
        payload = self.store.read()
        users = payload.get("users", []) if isinstance(payload, dict) else []
        self.users: List[Dict[str, Any]] = [item for item in users if isinstance(item, dict)]
        self.limit = limit
        self.lock = threading.Lock()

    def claim(
        self,
        user_id: int,
        chat_id: str,
        username: str = "",
        display_name: str = "",
    ) -> Tuple[str, int]:
        if user_id <= 0 or not chat_id:
            return "invalid", 0
        with self.lock:
            for user in self.users:
                if int(user.get("user_id", 0) or 0) == user_id:
                    user["chat_id"] = str(chat_id)
                    user["username"] = username[:100]
                    user["display_name"] = display_name[:160]
                    user["last_start_at"] = int(time.time())
                    self.store.write({"users": self.users[: self.limit]})
                    return "existing", self.users.index(user) + 1
            if len(self.users) >= self.limit:
                return "full", 0
            self.users.append(
                {
                    "user_id": user_id,
                    "chat_id": str(chat_id),
                    "username": username[:100],
                    "display_name": display_name[:160],
                    "claimed_at": int(time.time()),
                    "last_start_at": int(time.time()),
                }
            )
            self.store.write({"users": self.users})
            return "claimed", len(self.users)

    def is_authorized(self, user_id: int, chat_id: str) -> bool:
        with self.lock:
            return any(
                int(item.get("user_id", 0) or 0) == user_id
                and str(item.get("chat_id", "")) == str(chat_id)
                for item in self.users
            )

    def recipients(self) -> List[str]:
        with self.lock:
            return [str(item.get("chat_id", "")) for item in self.users if item.get("chat_id")]

    def count(self) -> int:
        with self.lock:
            return len(self.users)

    def slot_for(self, user_id: int, chat_id: str) -> int:
        with self.lock:
            for index, item in enumerate(self.users, start=1):
                if int(item.get("user_id", 0) or 0) == user_id and str(item.get("chat_id", "")) == str(chat_id):
                    return index
        return 0


class LeadStore:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, {})
        payload = self.store.read()
        raw_items = payload if isinstance(payload, dict) else {}
        self.aliases: Dict[str, str] = raw_items.get(ALIAS_KEY, {}) if isinstance(raw_items.get(ALIAS_KEY), dict) else {}
        self.items: Dict[str, Dict[str, Any]] = {
            key: value
            for key, value in raw_items.items()
            if not key.startswith("_") and isinstance(value, dict)
        }
        self._bootstrap_aliases()
        self.lock = threading.Lock()

    def _bootstrap_aliases(self) -> None:
        for uid, item in self.items.items():
            text = str(item.get("text", ""))
            lead_url = str(item.get("url", ""))
            client_url = str(item.get("client_url", item.get("author_url", "")))
            for key in lead_dedupe_keys(uid, text=text, lead_url=lead_url, client_url=client_url):
                self.aliases.setdefault(key, uid)

    def _payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(self.items)
        payload[ALIAS_KEY] = dict(self.aliases)
        return payload

    def is_seen(self, uid: str) -> bool:
        with self.lock:
            return uid in self.items or uid in self.aliases

    def is_seen_any(self, keys: List[str]) -> bool:
        with self.lock:
            return any(key in self.items or key in self.aliases for key in keys if key)

    def mark_seen(self, uid: str, record: Dict[str, Any], aliases: Optional[List[str]] = None) -> None:
        with self.lock:
            self.items[uid] = dict(record)
            for key in aliases or []:
                if key:
                    self.aliases[key] = uid
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())
            self.items = {
                key: value
                for key, value in self.items.items()
                if int(value.get("created_at", value.get("notified_at", 0)) or 0) >= cutoff
            }
            self.aliases = {
                key: value
                for key, value in self.aliases.items()
                if value in self.items
            }
            self.store.write(self._payload())

    def update_status(self, lead_id: str, status: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            for item in self.items.values():
                if item.get("lead_id") == lead_id:
                    item["status"] = status
                    item["status_updated_at"] = int(time.time())
                    self.store.write(self._payload())
                    return dict(item)
        return None

    def pipeline_counts(self, days: int = 30) -> Dict[str, int]:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        counts = {"new": 0, "work": 0, "contact": 0, "reject": 0, "total": 0}
        with self.lock:
            for item in self.items.values():
                if int(item.get("notified_at", 0) or 0) < cutoff:
                    continue
                status = str(item.get("status") or "new")
                counts[status] = counts.get(status, 0) + 1
                counts["total"] += 1
        return counts
