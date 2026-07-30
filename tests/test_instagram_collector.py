import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from instagram_collector import InstagramCollector, instagram_username, parse_hashtags, parse_instagram_sources


class _Owner:
    username = "buyer"


class _Comment:
    def __init__(self, comment_id: str, text: str, created_at_utc: datetime) -> None:
        self.id = comment_id
        self.text = text
        self.created_at_utc = created_at_utc
        self.owner = _Owner()


class _Post:
    shortcode = "ABC123"
    is_video = True
    owner_username = "seller"
    caption = "Авто из Китая"

    def __init__(self, comments: list[_Comment]) -> None:
        self._comments = comments

    def get_comments(self):
        return iter(self._comments)


class InstagramCollectorConfigTests(unittest.TestCase):
    def test_instagram_username_from_url(self):
        self.assertEqual("ta4ki_ta4ki", instagram_username("https://instagram.com/ta4ki_ta4ki"))
        self.assertEqual("svs_cars_72", instagram_username("https://www.instagram.com/svs_cars_72/?hl=ru"))

    def test_instagram_username_rejects_post_urls(self):
        self.assertEqual("", instagram_username("https://instagram.com/reel/ABC123/"))
        self.assertEqual("", instagram_username("https://instagram.com/p/ABC123/"))

    def test_parse_sources_deduplicates(self):
        result = parse_instagram_sources(
            "https://instagram.com/ta4ki_ta4ki, @TA4KI_TA4KI svs_cars_72",
            ["svs_cars_72"],
        )
        self.assertEqual(["svs_cars_72", "ta4ki_ta4ki"], result)

    def test_parse_hashtags(self):
        self.assertEqual(
            ["автоизкитая", "пригонавто"],
            parse_hashtags("#автоизкитая, пригонавто #АВТОИЗКИТАЯ"),
        )

    def test_comments_older_than_max_age_are_skipped(self):
        now = datetime.now(timezone.utc)
        collector = InstagramCollector(
            username="user",
            password="password",
            data_dir=Path("."),
            source_names=[],
            hashtags=[],
            lookback_hours=24 * 10,
            source_batch_size=1,
            hashtag_batch_size=0,
            media_limit=1,
            comments_limit=10,
            media_lookback_days=21,
            only_video=False,
            request_delay=0,
            request_timeout=30,
            comment_max_age_days=5,
        )
        post = _Post(
            [
                _Comment("1", "Цена до Москвы?", now - timedelta(days=4)),
                _Comment("2", "Сколько стоит?", now - timedelta(days=6)),
            ]
        )
        stats: dict[str, int] = {}
        since_ts = int((now - timedelta(days=10)).timestamp())
        comment_since_ts = int((now - timedelta(days=5)).timestamp())

        candidates = collector._collect_comments(
            post,
            since_ts,
            comment_since_ts,
            "Instagram @seller",
            "https://www.instagram.com/seller/",
            stats,
        )

        self.assertEqual([candidate.text for candidate in candidates], ["Цена до Москвы?"])
        self.assertEqual(stats.get("instagram_comments_old_skipped"), 1)


if __name__ == "__main__":
    unittest.main()
