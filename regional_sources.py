"""Loading and indexing of curated regional VK and Telegram sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RegionalSource:
    platform: str
    name: str
    city: str
    category: str = ""


def load_regional_sources(path: Path, platform: str = "") -> list[RegionalSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_items = payload.get("sources", []) if isinstance(payload, dict) else []
    wanted = platform.strip().lower()
    result: list[RegionalSource] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        item_platform = str(item.get("platform") or "").strip().lower()
        city = str(item.get("city") or "").strip()
        category = str(item.get("category") or "").strip()
        raw_names = item.get("names") if isinstance(item.get("names"), list) else [item.get("name")]
        if item_platform not in {"vk", "telegram"} or not city or (wanted and item_platform != wanted):
            continue
        for raw_name in raw_names:
            name = str(raw_name or "").strip().strip("@")
            key = (item_platform, name.lower())
            if not name or key in seen:
                continue
            seen.add(key)
            result.append(RegionalSource(item_platform, name, city, category))
    return result


def source_names(items: Iterable[RegionalSource]) -> list[str]:
    return [item.name for item in items]


def source_city_map(items: Iterable[RegionalSource]) -> dict[str, str]:
    return {item.name.lower(): item.city for item in items}
