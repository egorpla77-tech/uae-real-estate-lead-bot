import asyncio
import hashlib
import html as html_lib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from storage import JsonStore


DEFAULT_TELEGRAM_SOURCES = [
    "secondary_dubai",
    "kvartiryy_v_dubae",
    "realestate_dxb_uae",
    "dubairealtyinvest",
    "real_estate_property_dubai",
    "distress_deals_dubai",
    "dubai_propertyexpo",
    "nedvigadubai",
    "dubai_estatetoday",
    "dubai_propertyy",
    "dubai_nedvijka",
    "nedvizhimosti_dubai",
    "partner_palladiumrealestate",
    "dubai_realty2",
    "parshikov_dubai",
    "ilia_mira",
    "investments_emirates",
    "thecapitalae",
    "SmotriDubai",
    "insiderealtynews",
    "hotdealsuae_channel",
    "invest_housing",
    "ageevarealestate",
    "institutrieltora",
    "dubaiunrealestate",
]

TELEGRAM_URL_RE = re.compile(r"(?:https?://)?t\.me/(?:s/)?([^/?#\s]+)", re.IGNORECASE)
TELEGRAM_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL)
TELEGRAM_TIME_RE = re.compile(r'<time datetime="([^"]+)"', re.IGNORECASE)


@dataclass(frozen=True)
class TelegramCandidate:
    uid: str
    source_title: str
    source_url: str
    text: str
    context_text: str
    author_url: str
    direct_url: str
    created_at: int


def telegram_source_name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = TELEGRAM_URL_RE.search(value)
    if match:
        value = match.group(1)
    value = value.strip().strip("@").strip("/")
    if not value or value.lower() in {"c", "s", "joinchat", "addstickers", "share"}:
        return ""
    if value.startswith("+"):
        return ""
    return value


def parse_telegram_sources(raw: str, defaults: Optional[List[str]] = None) -> List[str]:
    values: List[str] = []
    seen = set()
    items = list(defaults or []) + re.split(r"[\s,]+", raw or "")
    for item in items:
        source = telegram_source_name(item)
        key = source.lower()
        if source and key not in seen:
            values.append(source)
            seen.add(key)
    return values


def telegram_entity_url(username: str) -> str:
    username = telegram_source_name(username)
    return f"https://t.me/{username}" if username else ""


def telegram_message_url(username: str, message_id: int, comment_id: int = 0) -> str:
    username = telegram_source_name(username)
    if not username or message_id <= 0:
        return ""
    base = f"https://t.me/{username}/{message_id}"
    if comment_id > 0:
        return f"{base}?comment={comment_id}"
    return base


def _timestamp(value: Any) -> int:
    if not value:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "да", "on"}


def _clean_html(value: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    return value.strip()


def _parse_datetime(value: str) -> int:
    value = (value or "").strip()
    if not value:
        return 0
    try:
        return _timestamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return 0


def parse_telegram_proxy(value: str) -> Optional[Tuple[Any, ...]]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        import socks
    except Exception:
        logging.warning("Telegram proxy задан, но PySocks не установлен")
        return None

    if "://" not in value:
        value = "socks5://" + value
    parsed = urlparse(value)
    scheme = (parsed.scheme or "socks5").lower()
    proxy_type = socks.SOCKS5
    if scheme in {"http", "https"}:
        proxy_type = socks.HTTP
    elif scheme in {"socks4", "socks4a"}:
        proxy_type = socks.SOCKS4
    host = parsed.hostname or ""
    port = parsed.port or 0
    if not host or not port:
        return None
    username = parsed.username or None
    password = parsed.password or None
    if username or password:
        return (proxy_type, host, port, True, username, password)
    return (proxy_type, host, port, True)


class TelegramCollector:
    def __init__(
        self,
        *,
        api_id: str,
        api_hash: str,
        data_dir: Path,
        source_names: List[str],
        lookback_hours: int,
        source_batch_size: int,
        post_limit: int,
        comments_limit: int,
        scan_comments: bool,
        request_delay: float,
        public_web_fallback: bool,
        request_timeout: float,
        comment_max_age_days: int = 5,
        proxy_url: str = "",
        session_name: str = "telegram_uae_real_estate_leads",
    ) -> None:
        self.api_id = str(api_id or "").strip()
        self.api_hash = str(api_hash or "").strip()
        self.data_dir = data_dir
        self.source_names = source_names
        self.lookback_hours = lookback_hours
        self.source_batch_size = source_batch_size
        self.post_limit = post_limit
        self.comments_limit = comments_limit
        self.scan_comments = scan_comments
        self.request_delay = request_delay
        self.public_web_fallback = public_web_fallback
        self.request_timeout = request_timeout
        self.comment_max_age_days = max(0, int(comment_max_age_days or 0))
        self.proxy_url = proxy_url
        self.session_name = session_name.strip() or "telegram_uae_real_estate_leads"
        self.cursor = JsonStore(data_dir / "telegram_cursor.json", {"source_cursor": 0})
        self.session_file = data_dir / self.session_name
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                )
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.source_names and ((self.api_id and self.api_hash) or self.public_web_fallback))

    @property
    def has_user_api(self) -> bool:
        return bool(self.api_id and self.api_hash)

    @staticmethod
    def _rotate(items: List[str], cursor: int, count: int) -> List[str]:
        if not items or count <= 0:
            return []
        cursor %= len(items)
        count = min(count, len(items))
        return [items[(cursor + index) % len(items)] for index in range(count)]

    def _selected_sources(self) -> List[str]:
        if not self.source_names:
            return []
        payload = self.cursor.read()
        payload = payload if isinstance(payload, dict) else {}
        cursor = int(payload.get("source_cursor", 0) or 0)
        batch_size = self.source_batch_size
        if batch_size <= 0 or batch_size >= len(self.source_names):
            selected = list(self.source_names)
            next_cursor = 0
        else:
            selected = self._rotate(self.source_names, cursor, batch_size)
            next_cursor = (cursor + len(selected)) % len(self.source_names)
        self.cursor.write({"source_cursor": next_cursor})
        return selected

    def collect(self) -> Tuple[List[TelegramCandidate], Dict[str, int]]:
        if not self.configured:
            return [], {"telegram_disabled": 1}
        if not self.has_user_api:
            return self._collect_public_web()
        try:
            return asyncio.run(self._collect_async())
        except RuntimeError as exc:
            if "asyncio.run()" in str(exc):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(self._collect_async())
                finally:
                    loop.close()
            raise

    def _collect_public_web(self) -> Tuple[List[TelegramCandidate], Dict[str, int]]:
        """Best-effort Telegram fallback without api_id/api_hash.

        It reads public t.me/s pages. This works for public channel posts and public
        groups, but Telegram does not expose channel comment threads there.
        """
        stats: Dict[str, int] = {"telegram_public_fallback": 1}
        candidates: List[TelegramCandidate] = []
        since_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp())
        selected_sources = self._selected_sources()
        logging.info("Telegram public web: проверяется источников %s", len(selected_sources))

        for source_name in selected_sources:
            source = telegram_source_name(source_name)
            if not source:
                continue
            try:
                response = self.http.get(f"https://t.me/s/{source}", timeout=self.request_timeout)
                response.raise_for_status()
                html = response.text
                source_candidates = self._parse_public_page(source, html, since_ts, stats)
                candidates.extend(source_candidates)
                if self.request_delay:
                    time.sleep(self.request_delay)
            except Exception as exc:
                stats["telegram_public_source_error"] = stats.get("telegram_public_source_error", 0) + 1
                logging.warning("Telegram public web: ошибка %s: %s", source, exc)

        unique = {candidate.uid: candidate for candidate in candidates}
        stats["telegram_candidates"] = len(unique)
        return list(unique.values()), stats

    def _parse_public_page(
        self,
        source: str,
        html: str,
        since_ts: int,
        stats: Dict[str, int],
    ) -> List[TelegramCandidate]:
        candidates: List[TelegramCandidate] = []
        parts = re.split(r'data-post="', html or "")
        source_url = telegram_entity_url(source)
        for part in parts[1:]:
            data_post, _, block = part.partition('"')
            if "/" not in data_post:
                continue
            post_source, message_id_text = data_post.rsplit("/", 1)
            try:
                message_id = int(message_id_text)
            except ValueError:
                continue
            text_match = TELEGRAM_TEXT_RE.search(block)
            if not text_match:
                continue
            text = _clean_html(text_match.group(1))
            if not text:
                continue
            time_match = TELEGRAM_TIME_RE.search(block)
            created_at = _parse_datetime(time_match.group(1) if time_match else "")
            if created_at and created_at < since_ts:
                continue
            stats["telegram_public_posts_seen"] = stats.get("telegram_public_posts_seen", 0) + 1
            direct_url = telegram_message_url(post_source or source, message_id)
            uid = f"telegram_public:{post_source or source}:{message_id}"
            candidates.append(
                TelegramCandidate(
                    uid=uid,
                    source_title=f"Telegram @{source}",
                    source_url=source_url,
                    text=text,
                    context_text="",
                    author_url="",
                    direct_url=direct_url or source_url,
                    created_at=created_at or int(time.time()),
                )
            )
            if len(candidates) >= self.post_limit:
                break
        stats["telegram_public_candidates"] = stats.get("telegram_public_candidates", 0) + len(candidates)
        return candidates

    async def _collect_async(self) -> Tuple[List[TelegramCandidate], Dict[str, int]]:
        try:
            from telethon import TelegramClient as TelethonClient
        except Exception as exc:
            logging.warning("Telegram user parser: telethon не установлен: %s", exc)
            return [], {"telegram_telethon_missing": 1}

        stats: Dict[str, int] = {}
        candidates: List[TelegramCandidate] = []
        since_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp())
        comment_since_ts = (
            int((datetime.now(timezone.utc) - timedelta(days=self.comment_max_age_days)).timestamp())
            if self.comment_max_age_days > 0
            else 0
        )
        selected_sources = self._selected_sources()
        logging.info("Telegram user parser: проверяется источников %s", len(selected_sources))

        self.data_dir.mkdir(parents=True, exist_ok=True)
        client = TelethonClient(
            str(self.session_file),
            int(self.api_id),
            self.api_hash,
            proxy=parse_telegram_proxy(self.proxy_url),
        )
        await client.connect()
        try:
            if not await client.is_user_authorized():
                logging.warning("Telegram user parser: сессия не авторизована, запустите setup_telegram_session.py")
                return [], {"telegram_not_authorized": 1}

            for source_name in selected_sources:
                try:
                    entity = await client.get_entity(source_name)
                    source_candidates = await self._collect_source(client, entity, source_name, since_ts, comment_since_ts, stats)
                    candidates.extend(source_candidates)
                    if self.request_delay:
                        await asyncio.sleep(self.request_delay)
                except Exception as exc:
                    stats["telegram_source_error"] = stats.get("telegram_source_error", 0) + 1
                    logging.warning("Telegram user parser: ошибка %s: %s", source_name, exc)
        finally:
            await client.disconnect()

        unique = {candidate.uid: candidate for candidate in candidates}
        stats["telegram_candidates"] = len(unique)
        return list(unique.values()), stats

    async def _collect_source(
        self,
        client: Any,
        entity: Any,
        source_name: str,
        since_ts: int,
        comment_since_ts: int,
        stats: Dict[str, int],
    ) -> List[TelegramCandidate]:
        candidates: List[TelegramCandidate] = []
        entity_id = int(getattr(entity, "id", 0) or 0)
        username = str(getattr(entity, "username", "") or source_name)
        title = str(getattr(entity, "title", "") or getattr(entity, "first_name", "") or username)
        source_url = telegram_entity_url(username)
        is_broadcast = bool(getattr(entity, "broadcast", False))

        async for message in client.iter_messages(entity, limit=self.post_limit):
            message_id = int(getattr(message, "id", 0) or 0)
            created_at = _timestamp(getattr(message, "date", None))
            if created_at and created_at < since_ts:
                break

            text = str(getattr(message, "raw_text", "") or "").strip()
            stats["telegram_posts_seen"] = stats.get("telegram_posts_seen", 0) + 1
            if text and not is_broadcast:
                candidates.append(
                    await self._candidate_from_message(
                        message,
                        uid=f"telegram_message:{entity_id}:{message_id}",
                        source_title=f"Telegram {title}",
                        source_url=source_url,
                        context_text="",
                        direct_url=telegram_message_url(username, message_id),
                        created_at=created_at,
                    )
                )

            if not self.scan_comments:
                continue
            replies = getattr(message, "replies", None)
            replies_count = int(getattr(replies, "replies", 0) or 0) if replies else 0
            if replies_count <= 0:
                continue

            try:
                async for reply in client.iter_messages(entity, reply_to=message_id, limit=self.comments_limit):
                    reply_id = int(getattr(reply, "id", 0) or 0)
                    reply_created = _timestamp(getattr(reply, "date", None))
                    comment_cutoff_ts = max(since_ts, comment_since_ts)
                    if reply_created and comment_cutoff_ts and reply_created < comment_cutoff_ts:
                        stats["telegram_comments_old_skipped"] = stats.get("telegram_comments_old_skipped", 0) + 1
                        break
                    if not str(getattr(reply, "raw_text", "") or "").strip():
                        continue
                    stats["telegram_comments_seen"] = stats.get("telegram_comments_seen", 0) + 1
                    candidates.append(
                        await self._candidate_from_message(
                            reply,
                            uid=f"telegram_comment:{entity_id}:{message_id}:{reply_id}",
                            source_title=f"Telegram {title}",
                            source_url=source_url,
                            context_text=text,
                            direct_url=telegram_message_url(username, message_id, reply_id),
                            created_at=reply_created,
                        )
                    )
                    if self.request_delay:
                        await asyncio.sleep(min(self.request_delay, 0.4))
            except Exception as exc:
                stats["telegram_comments_error"] = stats.get("telegram_comments_error", 0) + 1
                logging.debug("Telegram user parser: comments failed %s/%s: %s", username, message_id, exc)

        return candidates

    async def _candidate_from_message(
        self,
        message: Any,
        *,
        uid: str,
        source_title: str,
        source_url: str,
        context_text: str,
        direct_url: str,
        created_at: int,
    ) -> TelegramCandidate:
        text = str(getattr(message, "raw_text", "") or "").strip()
        author_url = ""
        try:
            sender = await message.get_sender()
            username = str(getattr(sender, "username", "") or "")
            if username:
                author_url = telegram_entity_url(username)
        except Exception:
            pass
        if not uid:
            fingerprint = hashlib.sha1(f"{source_url}:{direct_url}:{text}".encode("utf-8", "ignore")).hexdigest()[:16]
            uid = f"telegram_message:{fingerprint}"
        return TelegramCandidate(
            uid=uid,
            source_title=source_title,
            source_url=source_url,
            text=text,
            context_text=context_text,
            author_url=author_url,
            direct_url=direct_url or source_url,
            created_at=created_at or int(time.time()),
        )


def collector_from_env(base_dir: Path, lookback_hours: int) -> TelegramCollector:
    data_dir = base_dir / "data"
    sources = parse_telegram_sources(os.getenv("TELEGRAM_SOURCES", ""), DEFAULT_TELEGRAM_SOURCES)
    return TelegramCollector(
        api_id=os.getenv("TELEGRAM_API_ID", ""),
        api_hash=os.getenv("TELEGRAM_API_HASH", ""),
        data_dir=data_dir,
        source_names=sources,
        lookback_hours=max(1, int(os.getenv("TELEGRAM_LOOKBACK_HOURS", str(lookback_hours)) or lookback_hours)),
        source_batch_size=max(0, int(os.getenv("TELEGRAM_SOURCE_BATCH_SIZE", "10") or 10)),
        post_limit=max(1, int(os.getenv("TELEGRAM_POST_LIMIT", "30") or 30)),
        comments_limit=max(1, int(os.getenv("TELEGRAM_COMMENTS_LIMIT", "80") or 80)),
        scan_comments=str(os.getenv("TELEGRAM_SCAN_COMMENTS", "true")).strip().lower() in {"1", "true", "yes", "да", "on"},
        request_delay=max(0.0, float(os.getenv("TELEGRAM_REQUEST_DELAY_SECONDS", "0.7") or 0.7)),
        public_web_fallback=_env_bool("TELEGRAM_PUBLIC_WEB_FALLBACK", True),
        request_timeout=max(5.0, float(os.getenv("TELEGRAM_REQUEST_TIMEOUT_SECONDS", "30") or 30)),
        comment_max_age_days=max(0, int(os.getenv("TELEGRAM_COMMENT_MAX_AGE_DAYS", "5") or 5)),
        proxy_url=os.getenv("TELEGRAM_PROXY", ""),
        session_name=os.getenv("TELEGRAM_SESSION_NAME", "telegram_uae_real_estate_leads"),
    )
