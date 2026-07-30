import itertools
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from storage import JsonStore


DEFAULT_INSTAGRAM_SOURCES = [
    "ta4ki_ta4ki",
    "svs_cars_72",
    "proavto_23",
    "avtotrade77",
    "kypi_avto__",
    "kvaskov",
]

DEFAULT_INSTAGRAM_HASHTAGS = [
    "автоизкитая",
    "автоизоляпонии",
    "пригонавто",
    "автоизяпонии",
    "автоизкореи",
    "автоподзаказ",
    "автоизкитаяподзаказ",
    "японскийаукцион",
    "правыйруль",
    "китайскиеавто",
]

INSTAGRAM_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?instagram\.com/([^/?#\s]+)", re.IGNORECASE)


@dataclass(frozen=True)
class InstagramCandidate:
    uid: str
    source_title: str
    source_url: str
    text: str
    context_text: str
    author_url: str
    direct_url: str
    created_at: int


def instagram_username(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    match = INSTAGRAM_URL_RE.search(value)
    if match:
        value = match.group(1)
    else:
        parsed = urlparse(value if "://" in value else f"https://instagram.com/{value}")
        if parsed.netloc.endswith("instagram.com") and parsed.path.strip("/"):
            value = parsed.path.strip("/").split("/", 1)[0]
    value = value.strip().strip("@").strip("/")
    if value.lower() in {"p", "reel", "reels", "explore", "stories", "accounts"}:
        return ""
    return value


def parse_instagram_sources(raw: str, defaults: Optional[List[str]] = None) -> List[str]:
    values: List[str] = []
    seen = set()
    items = list(defaults or []) + re.split(r"[\s,]+", raw or "")
    for item in items:
        username = instagram_username(item)
        key = username.lower()
        if username and key not in seen:
            values.append(username)
            seen.add(key)
    return values


def parse_hashtags(raw: str, defaults: Optional[List[str]] = None) -> List[str]:
    values: List[str] = []
    seen = set()
    items = list(defaults or []) + re.split(r"[\s,]+", raw or "")
    for item in items:
        tag = item.strip().strip("#").strip("/")
        if not tag:
            continue
        key = tag.lower()
        if key not in seen:
            values.append(tag)
            seen.add(key)
    return values


def _safe_username(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", username).strip("._-") or "instagram"


def _ts_from_datetime(value: Any) -> int:
    if not value:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def _post_url(post: Any) -> str:
    shortcode = str(getattr(post, "shortcode", "") or "")
    if not shortcode:
        return ""
    path = "reel" if bool(getattr(post, "is_video", False)) else "p"
    return f"https://www.instagram.com/{path}/{shortcode}/"


def _owner_username(post: Any) -> str:
    owner = str(getattr(post, "owner_username", "") or "")
    if owner:
        return owner
    try:
        profile = getattr(post, "owner_profile", None)
        return str(getattr(profile, "username", "") or "")
    except Exception:
        return ""


def _comment_owner_username(comment: Any) -> str:
    owner = getattr(comment, "owner", None)
    return str(getattr(owner, "username", "") or "")


def _safe_login_error(exc: Exception) -> str:
    message = str(exc)
    if "Checkpoint required" in message:
        return "Checkpoint required: подтвердите вход в Instagram и повторите запуск"
    return message


def _candidate_uid(post: Any, comment: Any, author: str, created_at: int, text: str) -> str:
    shortcode = str(getattr(post, "shortcode", "") or "")
    comment_id = str(getattr(comment, "id", "") or "")
    if shortcode and comment_id:
        return f"instagram_comment:{shortcode}:{comment_id}"
    fingerprint = hashlib.sha1(f"{shortcode}:{author}:{created_at}:{text}".encode("utf-8", "ignore")).hexdigest()[:16]
    return f"instagram_comment:{shortcode}:{fingerprint}"


class InstagramCollector:
    def __init__(
        self,
        username: str,
        password: str,
        data_dir: Path,
        source_names: List[str],
        hashtags: List[str],
        lookback_hours: int,
        source_batch_size: int,
        hashtag_batch_size: int,
        media_limit: int,
        comments_limit: int,
        media_lookback_days: int,
        only_video: bool,
        request_delay: float,
        request_timeout: float,
        comment_max_age_days: int = 5,
        proxy_url: str = "",
        allow_graphql_fallback: bool = False,
    ) -> None:
        self.username = username.strip()
        self.password = password
        self.data_dir = data_dir
        self.source_names = source_names
        self.hashtags = hashtags
        self.lookback_hours = lookback_hours
        self.source_batch_size = source_batch_size
        self.hashtag_batch_size = hashtag_batch_size
        self.media_limit = media_limit
        self.comments_limit = comments_limit
        self.media_lookback_days = media_lookback_days
        self.only_video = only_video
        self.request_delay = request_delay
        self.request_timeout = request_timeout
        self.comment_max_age_days = max(0, int(comment_max_age_days or 0))
        self.proxy_url = proxy_url.strip()
        self.allow_graphql_fallback = allow_graphql_fallback
        self.cursor = JsonStore(data_dir / "instagram_cursor.json", {"source_cursor": 0, "hashtag_cursor": 0})
        self.session_file = data_dir / f"instagram_{_safe_username(self.username)}.session"
        self.loader: Optional[Any] = None

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password and (self.source_names or self.hashtags))

    def prepare(self) -> bool:
        if not self.configured:
            return False
        if self.loader is not None:
            return True
        try:
            import instaloader
            from instaloader.exceptions import (
                BadCredentialsException,
                ConnectionException,
                LoginException,
                TwoFactorAuthRequiredException,
            )
        except Exception as exc:
            logging.warning("Instagram: instaloader не установлен или не импортируется: %s", exc)
            return False

        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
            max_connection_attempts=1,
            request_timeout=self.request_timeout,
        )
        self._apply_proxy(loader)
        if self.proxy_url:
            logging.info("Instagram: прокси включён")

        try:
            if self.session_file.exists():
                loader.load_session_from_file(self.username, str(self.session_file))
                self._apply_proxy(loader)
                logging.info("Instagram: использована сохранённая сессия @%s", self.username)
            else:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                try:
                    loader.login(self.username, self.password)
                except TwoFactorAuthRequiredException:
                    code = os.getenv("INSTAGRAM_2FA_CODE", "").strip()
                    if not code:
                        logging.warning("Instagram: нужен 2FA-код для @%s, задайте INSTAGRAM_2FA_CODE и перезапустите", self.username)
                        return False
                    loader.two_factor_login(code)
                self._apply_proxy(loader)
                loader.save_session_to_file(str(self.session_file))
                try:
                    self.session_file.chmod(0o600)
                except OSError:
                    pass
                logging.info("Instagram: новая сессия сохранена для @%s", self.username)
        except (BadCredentialsException, ConnectionException, LoginException) as exc:
            logging.warning("Instagram: не удалось войти @%s: %s", self.username, _safe_login_error(exc))
            return False
        except Exception as exc:
            logging.warning("Instagram: ошибка подготовки @%s: %s", self.username, _safe_login_error(exc))
            return False

        self.loader = loader
        return True

    def _apply_proxy(self, loader: Any) -> None:
        if not self.proxy_url:
            return
        self._apply_proxy_to_session(loader.context._session)

    def _apply_proxy_to_session(self, session: requests.Session) -> None:
        if not self.proxy_url:
            return
        session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})

    def _selected_batches(self) -> Tuple[List[str], List[str]]:
        payload = self.cursor.read()
        payload = payload if isinstance(payload, dict) else {}
        source_cursor = int(payload.get("source_cursor", 0) or 0)
        hashtag_cursor = int(payload.get("hashtag_cursor", 0) or 0)

        sources = self._rotate(self.source_names, source_cursor, self.source_batch_size)
        hashtags = self._rotate(self.hashtags, hashtag_cursor, self.hashtag_batch_size)

        next_source = (source_cursor + len(sources)) % len(self.source_names) if self.source_names else 0
        next_hashtag = (hashtag_cursor + len(hashtags)) % len(self.hashtags) if self.hashtags else 0
        self.cursor.write({"source_cursor": next_source, "hashtag_cursor": next_hashtag})
        return sources, hashtags

    @staticmethod
    def _rotate(items: List[str], cursor: int, count: int) -> List[str]:
        if not items or count <= 0:
            return []
        cursor %= len(items)
        count = min(count, len(items))
        return [items[(cursor + index) % len(items)] for index in range(count)]

    def collect(self) -> Tuple[List[InstagramCandidate], Dict[str, int]]:
        stats: Dict[str, int] = {}
        if not self.prepare():
            stats["instagram_disabled"] = 1
            return [], stats

        import instaloader

        since_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp())
        comment_since_ts = self._comment_since_ts()
        min_media_dt = datetime.now(timezone.utc) - timedelta(days=self.media_lookback_days)
        sources, hashtags = self._selected_batches()
        candidates: List[InstagramCandidate] = []

        logging.info("Instagram: проверяется аккаунтов %s, хэштегов %s", len(sources), len(hashtags))
        for username in sources:
            api_candidates, api_ok = self._collect_profile_api(username, since_ts, comment_since_ts, min_media_dt, stats)
            if api_ok:
                candidates.extend(api_candidates)
                self._sleep()
                continue
            if not self.allow_graphql_fallback:
                continue
            try:
                profile = instaloader.Profile.from_username(self.loader.context, username)
                source_title = f"Instagram @{profile.username}"
                source_url = f"https://www.instagram.com/{profile.username}/"
                candidates.extend(
                    self._collect_from_posts(
                        profile.get_posts(),
                        since_ts,
                        comment_since_ts,
                        min_media_dt,
                        source_title,
                        source_url,
                        stats,
                    )
                )
            except Exception as exc:
                stats["instagram_source_error"] = stats.get("instagram_source_error", 0) + 1
                logging.warning("Instagram: ошибка проверки @%s: %s", username, exc)
            self._sleep()

        for tag in hashtags:
            try:
                hashtag = instaloader.Hashtag.from_name(self.loader.context, tag)
                candidates.extend(
                    self._collect_from_posts(
                        hashtag.get_posts(),
                        since_ts,
                        comment_since_ts,
                        min_media_dt,
                        f"Instagram #{tag}",
                        f"https://www.instagram.com/explore/tags/{tag}/",
                        stats,
                    )
                )
            except Exception as exc:
                stats["instagram_hashtag_error"] = stats.get("instagram_hashtag_error", 0) + 1
                logging.warning("Instagram: ошибка проверки #%s: %s", tag, exc)
            self._sleep()

        unique = {candidate.uid: candidate for candidate in candidates}
        stats["instagram_candidates"] = len(unique)
        return list(unique.values()), stats

    def _comment_since_ts(self) -> int:
        if self.comment_max_age_days <= 0:
            return 0
        return int((datetime.now(timezone.utc) - timedelta(days=self.comment_max_age_days)).timestamp())

    def _api_session(self) -> requests.Session:
        if not self.loader:
            raise RuntimeError("Instagram loader is not prepared")
        session = self.loader.context._session
        cookies = session.cookies.get_dict()
        session.headers.update(
            {
                "Accept": "*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.instagram.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                "X-IG-App-ID": "936619743392459",
            }
        )
        if cookies.get("csrftoken"):
            session.headers.update({"X-CSRFToken": cookies["csrftoken"]})
        self._apply_proxy_to_session(session)
        return session

    def _api_get_json(self, urls: List[str], params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        session = self._api_session()
        last_error = ""
        for url in urls:
            try:
                response = session.get(url, params=params or {}, timeout=self.request_timeout)
                if response.status_code in {400, 401, 403, 404, 429}:
                    last_error = f"{response.status_code} {response.text[:120]}"
                    continue
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else None
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                continue
        if last_error:
            logging.debug("Instagram API fallback failed: %s", last_error)
        return None

    def _collect_profile_api(
        self,
        username: str,
        since_ts: int,
        comment_since_ts: int,
        min_media_dt: datetime,
        stats: Dict[str, int],
    ) -> Tuple[List[InstagramCandidate], bool]:
        profile_data = self._api_get_json(
            ["https://www.instagram.com/api/v1/users/web_profile_info/"],
            {"username": username},
        )
        user = ((profile_data or {}).get("data") or {}).get("user") or {}
        user_id = str(user.get("id") or "")
        if not user_id:
            stats["instagram_api_profile_error"] = stats.get("instagram_api_profile_error", 0) + 1
            return [], False

        feed_data = self._api_get_json(
            [
                f"https://www.instagram.com/api/v1/feed/user/{user_id}/",
                f"https://i.instagram.com/api/v1/feed/user/{user_id}/",
            ],
            {"count": max(1, self.media_limit)},
        )
        items = (feed_data or {}).get("items")
        if not isinstance(items, list):
            stats["instagram_api_feed_error"] = stats.get("instagram_api_feed_error", 0) + 1
            return [], False

        source_title = f"Instagram @{username}"
        source_url = f"https://www.instagram.com/{username}/"
        candidates: List[InstagramCandidate] = []
        checked = 0
        for item in items:
            if checked >= self.media_limit:
                break
            if not isinstance(item, dict):
                continue
            taken_at = int(item.get("taken_at") or item.get("device_timestamp") or 0)
            if taken_at:
                post_dt = datetime.fromtimestamp(taken_at, tz=timezone.utc)
                if post_dt < min_media_dt:
                    continue
            if self.only_video and not self._api_item_is_video(item):
                continue
            checked += 1
            stats["instagram_posts_seen"] = stats.get("instagram_posts_seen", 0) + 1
            candidates.extend(
                self._collect_api_comments(
                    item,
                    since_ts,
                    comment_since_ts,
                    source_title,
                    source_url,
                    stats,
                )
            )
            self._sleep()
        return candidates, True

    @staticmethod
    def _api_item_is_video(item: Dict[str, Any]) -> bool:
        return int(item.get("media_type") or 0) == 2 or str(item.get("product_type") or "").lower() in {"clips", "igtv"}

    @staticmethod
    def _api_caption(item: Dict[str, Any]) -> str:
        caption = item.get("caption")
        if isinstance(caption, dict):
            return str(caption.get("text") or "")
        return ""

    @staticmethod
    def _api_shortcode(item: Dict[str, Any]) -> str:
        return str(item.get("code") or item.get("shortcode") or "")

    def _collect_api_comments(
        self,
        item: Dict[str, Any],
        since_ts: int,
        comment_since_ts: int,
        source_title: str,
        source_url: str,
        stats: Dict[str, int],
    ) -> List[InstagramCandidate]:
        media_id = str(item.get("id") or item.get("pk") or "")
        shortcode = self._api_shortcode(item)
        if not media_id or not shortcode:
            return []
        direct_url = f"https://www.instagram.com/reel/{shortcode}/" if self._api_item_is_video(item) else f"https://www.instagram.com/p/{shortcode}/"
        candidates: List[InstagramCandidate] = []
        next_min_id = ""
        while len(candidates) < self.comments_limit:
            params: Dict[str, Any] = {
                "can_support_threading": "true",
                "permalink_enabled": "false",
            }
            if next_min_id:
                params["min_id"] = next_min_id
            data = self._api_get_json(
                [
                    f"https://www.instagram.com/api/v1/media/{media_id}/comments/",
                    f"https://i.instagram.com/api/v1/media/{media_id}/comments/",
                ],
                params,
            )
            comments = (data or {}).get("comments")
            if not isinstance(comments, list):
                stats["instagram_comments_error"] = stats.get("instagram_comments_error", 0) + 1
                return candidates
            if not comments:
                return candidates
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                stats["instagram_comments_seen"] = stats.get("instagram_comments_seen", 0) + 1
                created_at = int(comment.get("created_at") or 0)
                comment_cutoff_ts = max(since_ts, comment_since_ts)
                if created_at and comment_cutoff_ts and created_at < comment_cutoff_ts:
                    stats["instagram_comments_old_skipped"] = stats.get("instagram_comments_old_skipped", 0) + 1
                    continue
                text = str(comment.get("text") or "").strip()
                if not text:
                    continue
                user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
                author = str(user.get("username") or "")
                comment_id = str(comment.get("pk") or comment.get("id") or "")
                uid = f"instagram_comment:{shortcode}:{comment_id or hashlib.sha1(text.encode('utf-8', 'ignore')).hexdigest()[:12]}"
                candidates.append(
                    InstagramCandidate(
                        uid=uid,
                        source_title=source_title,
                        source_url=source_url,
                        text=text,
                        context_text=self._api_caption(item),
                        author_url=f"https://www.instagram.com/{author}/" if author else "",
                        direct_url=direct_url,
                        created_at=created_at or int(time.time()),
                    )
                )
                if len(candidates) >= self.comments_limit:
                    break
            next_min_id = str((data or {}).get("next_min_id") or "")
            if not next_min_id:
                return candidates
        return candidates

    def _collect_from_posts(
        self,
        posts: Iterable[Any],
        since_ts: int,
        comment_since_ts: int,
        min_media_dt: datetime,
        source_title: str,
        source_url: str,
        stats: Dict[str, int],
    ) -> List[InstagramCandidate]:
        candidates: List[InstagramCandidate] = []
        checked_posts = 0
        max_checked = max(self.media_limit * 4, self.media_limit)
        for post in itertools.islice(posts, max_checked):
            post_dt = getattr(post, "date_utc", None)
            if post_dt and post_dt.tzinfo is None:
                post_dt = post_dt.replace(tzinfo=timezone.utc)
            if post_dt and post_dt < min_media_dt:
                break
            if self.only_video and not bool(getattr(post, "is_video", False)):
                continue
            checked_posts += 1
            if checked_posts > self.media_limit:
                break
            stats["instagram_posts_seen"] = stats.get("instagram_posts_seen", 0) + 1
            candidates.extend(self._collect_comments(post, since_ts, comment_since_ts, source_title, source_url, stats))
            self._sleep()
        return candidates

    def _collect_comments(
        self,
        post: Any,
        since_ts: int,
        comment_since_ts: int,
        source_title: str,
        source_url: str,
        stats: Dict[str, int],
    ) -> List[InstagramCandidate]:
        direct_url = _post_url(post)
        owner = _owner_username(post)
        if owner:
            source_title = f"{source_title} / @{owner}" if source_title.startswith("Instagram #") else source_title
            source_url = f"https://www.instagram.com/{owner}/"
        context_text = str(getattr(post, "caption", "") or "")
        candidates: List[InstagramCandidate] = []
        try:
            comments = post.get_comments()
            for comment in itertools.islice(comments, self.comments_limit):
                stats["instagram_comments_seen"] = stats.get("instagram_comments_seen", 0) + 1
                created_at = _ts_from_datetime(getattr(comment, "created_at_utc", None))
                comment_cutoff_ts = max(since_ts, comment_since_ts)
                if created_at and comment_cutoff_ts and created_at < comment_cutoff_ts:
                    stats["instagram_comments_old_skipped"] = stats.get("instagram_comments_old_skipped", 0) + 1
                    continue
                text = str(getattr(comment, "text", "") or "").strip()
                if not text:
                    continue
                author = _comment_owner_username(comment)
                candidates.append(
                    InstagramCandidate(
                        uid=_candidate_uid(post, comment, author, created_at, text),
                        source_title=source_title,
                        source_url=source_url,
                        text=text,
                        context_text=context_text,
                        author_url=f"https://www.instagram.com/{author}/" if author else "",
                        direct_url=direct_url,
                        created_at=created_at or int(time.time()),
                    )
                )
        except Exception as exc:
            stats["instagram_comments_error"] = stats.get("instagram_comments_error", 0) + 1
            logging.warning("Instagram: ошибка чтения комментариев %s: %s", direct_url, exc)
        return candidates

    def _sleep(self) -> None:
        if self.request_delay > 0:
            time.sleep(self.request_delay)
