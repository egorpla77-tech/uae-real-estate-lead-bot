"""Discover public regional VK and Telegram communities for manual curation.

The script is intentionally separate from the production monitor. It searches
public directories, checks basic activity and prints a JSON catalog that can be
reviewed before sources are enabled.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bot import ENV_PATH, Source, VkClient, load_env, parse_vk_tokens
from telegram_collector import parse_telegram_proxy


REGIONS = {
    "moscow": "Москва",
    "saint_petersburg": "Санкт-Петербург",
    "yekaterinburg": "Екатеринбург",
    "kazan": "Казань",
    "tyumen": "Тюмень",
    "surgut": "Сургут",
    "novosibirsk": "Новосибирск",
    "ufa": "Уфа",
    "krasnodar": "Краснодар",
    "sochi": "Сочи",
    "samara": "Самара",
    "chelyabinsk": "Челябинск",
    "perm": "Пермь",
    "rostov_on_don": "Ростов-на-Дону",
    "vladivostok": "Владивосток",
    "khabarovsk": "Хабаровск",
}

CATEGORY_QUERIES = {
    "investments": ("инвестиции", "инвесторы"),
    "business": ("бизнес клуб", "предприниматели"),
    "real_estate": ("новостройки", "недвижимость"),
    "finance": ("финансы", "ипотека"),
}

CATEGORY_MARKERS = {
    "investments": ("инвест", "инвестор", "капитал", "трейд"),
    "business": ("бизнес", "предприним", "деловой клуб", "стартап", "основател"),
    "real_estate": ("недвиж", "новостро", "квартир", "риелт", "застрой", "жилой комплекс"),
    "finance": ("финанс", "ипотек", "банк", "wealth", "капитал"),
}

EXCLUDED = (
    "работа",
    "ваканси",
    "барахолк",
    "куплю продам",
    "объявлен",
    "знакомств",
    "авто",
    "такси",
    "доставк",
    "ставки",
    "крипто-сигнал",
)


def _excluded(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in EXCLUDED)


def _base_item(platform: str, name: str, title: str, city_key: str, category: str, members: int) -> dict[str, Any]:
    return {
        "platform": platform,
        "name": name,
        "title": title,
        "city": REGIONS[city_key],
        "city_key": city_key,
        "category": category,
        "members_count": max(0, int(members or 0)),
    }


def discover_vk(per_city: int) -> list[dict[str, Any]]:
    load_env(ENV_PATH)
    tokens = parse_vk_tokens(os.getenv("VK_TOKEN", ""), os.getenv("VK_TOKENS", ""))
    vk = VkClient(tokens, os.getenv("VK_API_VERSION", "5.199"), 0.42)
    found: dict[tuple[str, str], dict[str, Any]] = {}

    for city_key, city in REGIONS.items():
        for category, suffixes in CATEGORY_QUERIES.items():
            for suffix in suffixes[:1]:
                response = vk.request(
                    "groups.search",
                    {
                        "q": f"{city} {suffix}",
                        "count": 10,
                        "fields": "members_count,description,city,activity",
                    },
                )
                items = response.get("items", []) if isinstance(response, dict) else []
                for group in items:
                    group_id = int(group.get("id", 0) or 0)
                    screen_name = str(group.get("screen_name") or f"club{group_id}")
                    title = str(group.get("name") or screen_name)
                    description = str(group.get("description") or "")
                    group_city = str((group.get("city") or {}).get("title") or "")
                    members = int(group.get("members_count", 0) or 0)
                    if not group_id or int(group.get("is_closed", 0) or 0) or _excluded(f"{title} {description}"):
                        continue
                    if not any(marker in title.lower() for marker in CATEGORY_MARKERS[category]):
                        continue
                    city_match = city.lower() in f"{title} {description} {group_city}".lower()
                    if not city_match or members < 100:
                        continue
                    key = (city_key, screen_name.lower())
                    candidate = found.get(key)
                    if not candidate:
                        candidate = _base_item("vk", screen_name, title, city_key, category, members)
                        candidate.update({"group_id": group_id, "description": description[:500], "categories": []})
                        found[key] = candidate
                    if category not in candidate["categories"]:
                        candidate["categories"].append(category)

    cutoff = int((datetime.now(timezone.utc) - timedelta(days=120)).timestamp())
    raw_by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city_key, _name), candidate in found.items():
        raw_by_city[city_key].append(candidate)

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for city_key, raw_candidates in raw_by_city.items():
        # Activity checks require a wall request, so pre-rank by coverage and
        # audience size and inspect a bounded shortlist for every city.
        shortlist = sorted(
            raw_candidates,
            key=lambda item: (len(item["categories"]), item["members_count"]),
            reverse=True,
        )[: max(per_city * 2, 10)]
        for candidate in shortlist:
            source = Source(
                screen_name=candidate["name"],
                group_id=int(candidate["group_id"]),
                title=candidate["title"],
                city=REGIONS[city_key],
            )
            posts = list(vk.wall_posts(source, 12))
            latest = max((int(post.get("date", 0) or 0) for post in posts), default=0)
            if latest < cutoff:
                continue
            comments = sum(int((post.get("comments") or {}).get("count", 0) or 0) for post in posts)
            candidate["last_post_at"] = latest
            candidate["recent_comments"] = comments
            candidate["score"] = round(
                math.log10(max(10, candidate["members_count"])) * 2
                + min(5, len(candidate["categories"]))
                + min(8, math.log2(comments + 1)),
                3,
            )
            by_city[city_key].append(candidate)

    selected: list[dict[str, Any]] = []
    for city_key in REGIONS:
        ranked = sorted(
            by_city.get(city_key, []),
            key=lambda item: (item["recent_comments"] > 0, item["score"], item["members_count"]),
            reverse=True,
        )
        selected.extend(ranked[:per_city])
    return selected


def discover_telegram(per_city: int) -> list[dict[str, Any]]:
    load_env(ENV_PATH)
    try:
        from telethon.sync import TelegramClient
        from telethon.tl.functions.contacts import SearchRequest
    except ImportError as exc:
        raise RuntimeError("Telethon is required for Telegram discovery") from exc

    session = ENV_PATH.parent / "data" / (os.getenv("TELEGRAM_SESSION_NAME", "telegram_house_leads") or "telegram_house_leads")
    client = TelegramClient(
        str(session),
        int(os.getenv("TELEGRAM_API_ID", "0")),
        os.getenv("TELEGRAM_API_HASH", ""),
        proxy=parse_telegram_proxy(os.getenv("TELEGRAM_PROXY", "")),
    )
    found: dict[tuple[str, str], dict[str, Any]] = {}
    client.connect()
    try:
        if not client.is_user_authorized():
            raise RuntimeError("Telegram user session is not authorized")
        for city_key, city in REGIONS.items():
            for category, suffixes in CATEGORY_QUERIES.items():
                query = f"{city} {suffixes[0]}"
                result = client(SearchRequest(q=query, limit=20))
                for chat in result.chats:
                    username = str(getattr(chat, "username", "") or "")
                    title = str(getattr(chat, "title", "") or username)
                    members = int(getattr(chat, "participants_count", 0) or 0)
                    if not username or members < 100 or _excluded(title):
                        continue
                    if not any(marker in title.lower() for marker in CATEGORY_MARKERS[category]):
                        continue
                    if city.lower() not in title.lower():
                        continue
                    key = (city_key, username.lower())
                    candidate = found.get(key)
                    if not candidate:
                        candidate = _base_item("telegram", username, title, city_key, category, members)
                        candidate["categories"] = []
                        found[key] = candidate
                    if category not in candidate["categories"]:
                        candidate["categories"].append(category)
                time.sleep(0.35)
    finally:
        client.disconnect()

    by_city: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (city_key, _name), candidate in found.items():
        candidate["score"] = round(
            math.log10(max(10, candidate["members_count"])) * 2 + min(5, len(candidate["categories"])),
            3,
        )
        by_city[city_key].append(candidate)
    selected: list[dict[str, Any]] = []
    for city_key in REGIONS:
        selected.extend(
            sorted(
                by_city.get(city_key, []),
                key=lambda item: (item["score"], item["members_count"]),
                reverse=True,
            )[:per_city]
        )
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=("vk", "telegram", "all"), default="all")
    parser.add_argument("--per-city", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    items: list[dict[str, Any]] = []
    if args.platform in {"vk", "all"}:
        items.extend(discover_vk(max(1, args.per_city)))
    if args.platform in {"telegram", "all"}:
        items.extend(discover_telegram(max(1, args.per_city)))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cities": list(REGIONS.values()),
        "count": len(items),
        "sources": items,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {len(items)} sources to {args.output}")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
