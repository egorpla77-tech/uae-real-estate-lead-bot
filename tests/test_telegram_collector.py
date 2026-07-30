import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram_collector import TelegramCollector, _parse_datetime


class _Replies:
    replies = 2


class _Sender:
    username = "buyer"


class _Message:
    def __init__(self, message_id: int, raw_text: str, date: datetime, replies: object | None = None) -> None:
        self.id = message_id
        self.raw_text = raw_text
        self.date = date
        self.replies = replies

    async def get_sender(self) -> _Sender:
        return _Sender()


class _Entity:
    id = 123
    username = "cars_chat"
    title = "Cars Chat"
    broadcast = True


class _ChatEntity:
    id = 456
    username = "russian_dubai_chat"
    title = "Russian Dubai Chat"
    broadcast = False


class _Client:
    def __init__(self, post: _Message, replies: list[_Message]) -> None:
        self.post = post
        self.replies = replies
        self.requested_limits: list[int] = []

    async def iter_messages(self, entity: object, limit: int, reply_to: int | None = None):
        self.requested_limits.append(limit)
        items = self.replies if reply_to else [self.post]
        for item in items[:limit]:
            yield item


class TelegramCollectorTest(unittest.TestCase):
    def test_parse_datetime(self) -> None:
        timestamp = _parse_datetime("2026-07-01T12:00:00+00:00")
        self.assertGreater(timestamp, 0)

    def test_comments_older_than_max_age_are_skipped(self) -> None:
        now = datetime.now(timezone.utc)
        post = _Message(100, "Обсуждение авто", now, replies=_Replies())
        fresh_reply = _Message(201, "Цена до Москвы?", now - timedelta(days=4))
        old_reply = _Message(202, "Сколько стоит?", now - timedelta(days=6))

        with TemporaryDirectory() as tmp:
            collector = TelegramCollector(
                api_id="",
                api_hash="",
                data_dir=Path(tmp),
                source_names=["cars_chat"],
                lookback_hours=24 * 10,
                source_batch_size=1,
                post_limit=10,
                comments_limit=10,
                scan_comments=True,
                request_delay=0,
                public_web_fallback=False,
                request_timeout=30,
                comment_max_age_days=5,
            )
            stats: dict[str, int] = {}
            since_ts = int((now - timedelta(days=10)).timestamp())
            comment_since_ts = int((now - timedelta(days=5)).timestamp())
            candidates = asyncio.run(
                collector._collect_source(_Client(post, [fresh_reply, old_reply]), _Entity(), "cars_chat", since_ts, comment_since_ts, stats)
            )

        self.assertEqual([candidate.text for candidate in candidates], ["Цена до Москвы?"])
        self.assertEqual(stats.get("telegram_comments_old_skipped"), 1)


    def test_busy_chat_uses_deeper_message_limit(self) -> None:
        now = datetime.now(timezone.utc)
        post = _Message(100, "Можно ли купить квартиру в ипотеку?", now)
        client = _Client(post, [])

        with TemporaryDirectory() as tmp:
            collector = TelegramCollector(
                api_id="",
                api_hash="",
                data_dir=Path(tmp),
                source_names=["russian_dubai_chat"],
                lookback_hours=24,
                source_batch_size=1,
                post_limit=30,
                comments_limit=10,
                scan_comments=True,
                request_delay=0,
                public_web_fallback=False,
                request_timeout=30,
                chat_message_limit=500,
            )
            stats: dict[str, int] = {}
            candidates = asyncio.run(
                collector._collect_source(client, _ChatEntity(), "russian_dubai_chat", 0, 0, stats)
            )

        self.assertEqual(client.requested_limits, [500])
        self.assertEqual([candidate.text for candidate in candidates], ["Можно ли купить квартиру в ипотеку?"])


if __name__ == "__main__":
    unittest.main()
