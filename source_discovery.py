"""Persistent rotation and promotion state for VK source discovery."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from storage import JsonStore


ALLOWED_REGIONS = {"uae", "dubai", "abu_dhabi", "other_uae"}
UAE_RE = re.compile(r"(?:дуба\w*|оаэ|эмират\w*|dubai|uae|abu[\s-]?dhabi|sharjah|ras[\s-]?al[\s-]?khaimah)", re.IGNORECASE)
REAL_ESTATE_RE = re.compile(
    r"(?:недвижим\w*|квартир\w*|апартамент\w*|вилл\w*|новостро\w*|"
    r"real[\s-]?estate|realty|propert\w*|apartment\w*|villa\w*)",
    re.IGNORECASE,
)
NON_TARGET_MARKERS = (
    "туры",
    "туризм",
    "отели",
    "вакансии",
    "работа в дубае",
    "автомобили",
    "рестораны",
)


@dataclass(frozen=True)
class DiscoveryCandidate:
    screen_name: str
    title: str
    region: str
    description: str = ""


def is_uae_real_estate_candidate(title: str, description: str = "") -> bool:
    text = f"{title} {description}".lower()
    return (
        bool(UAE_RE.search(text))
        and bool(REAL_ESTATE_RE.search(text))
        and not any(marker in text for marker in NON_TARGET_MARKERS)
    )


def load_catalog(path: Path, excluded_names: Iterable[str] = ()) -> List[DiscoveryCandidate]:
    excluded = {str(name).strip().lower() for name in excluded_names if str(name).strip()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_items = payload.get("candidates", []) if isinstance(payload, dict) else payload
    result: List[DiscoveryCandidate] = []
    seen: Set[str] = set()
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        screen_name = str(item.get("screen_name") or "").strip()
        region = str(item.get("region") or "").strip().lower()
        title = str(item.get("name") or item.get("title") or screen_name).strip()
        description = str(item.get("description") or "").strip()
        key = screen_name.lower()
        if (
            not screen_name
            or key in seen
            or key in excluded
            or region not in ALLOWED_REGIONS
            or not is_uae_real_estate_candidate(title, description)
        ):
            continue
        seen.add(key)
        result.append(DiscoveryCandidate(screen_name, title, region, description))
    return result


class SourceDiscovery:
    """Keeps candidate outcomes across restarts without modifying .env."""

    def __init__(
        self,
        catalog: List[DiscoveryCandidate],
        state_store: JsonStore,
        retry_after_seconds: int,
        reject_after_seconds: int,
    ) -> None:
        self.catalog = catalog
        self.state_store = state_store
        self.retry_after_seconds = retry_after_seconds
        self.reject_after_seconds = reject_after_seconds

    def _payload(self) -> Dict[str, Any]:
        payload = self.state_store.read()
        if not isinstance(payload, dict):
            return {"cursor": 0, "candidates": {}}
        candidates = payload.get("candidates", {})
        return {
            "cursor": int(payload.get("cursor", 0) or 0),
            "candidates": candidates if isinstance(candidates, dict) else {},
        }

    def accepted_names(self) -> List[str]:
        payload = self._payload()
        states = payload["candidates"]
        return [
            candidate.screen_name
            for candidate in self.catalog
            if str((states.get(candidate.screen_name) or {}).get("status")) == "accepted"
        ]

    def select(self, limit: int, active_names: Iterable[str], now: int | None = None) -> List[DiscoveryCandidate]:
        if limit <= 0 or not self.catalog:
            return []
        current_time = int(time.time()) if now is None else int(now)
        active = {str(name).strip().lower() for name in active_names if str(name).strip()}
        payload = self._payload()
        states = payload["candidates"]
        cursor = payload["cursor"] % len(self.catalog)
        selected: List[DiscoveryCandidate] = []

        # Prefer never-checked groups. Once the catalog is exhausted, revisit candidates
        # whose cooldown has passed so a quiet week does not permanently exclude a source.
        for only_unchecked in (True, False):
            for offset in range(len(self.catalog)):
                if len(selected) >= limit:
                    break
                candidate = self.catalog[(cursor + offset) % len(self.catalog)]
                key = candidate.screen_name.lower()
                state = states.get(candidate.screen_name, {})
                status = str(state.get("status") or "pending")
                if key in active or status == "accepted":
                    continue
                if only_unchecked and state:
                    continue
                if not only_unchecked:
                    next_check = int(state.get("next_check_at", 0) or 0)
                    if not state or next_check > current_time:
                        continue
                selected.append(candidate)
            if selected:
                break

        payload["cursor"] = (cursor + max(1, len(selected))) % len(self.catalog)
        self.state_store.write(payload)
        return selected

    def record(
        self,
        candidate: DiscoveryCandidate,
        matched_leads: int,
        sampled_posts: int,
        sampled_comments: int,
        min_matched_leads: int,
        now: int | None = None,
        unavailable: bool = False,
    ) -> str:
        current_time = int(time.time()) if now is None else int(now)
        payload = self._payload()
        states = payload["candidates"]
        previous = states.get(candidate.screen_name, {})
        attempts = int(previous.get("attempts", 0) or 0) + 1
        if matched_leads >= min_matched_leads:
            status = "accepted"
            next_check_at = 0
        elif unavailable or attempts >= 2:
            status = "rejected"
            next_check_at = current_time + self.reject_after_seconds
        else:
            status = "recheck"
            next_check_at = current_time + self.retry_after_seconds
        states[candidate.screen_name] = {
            "status": status,
            "attempts": attempts,
            "checked_at": current_time,
            "next_check_at": next_check_at,
            "matched_leads": matched_leads,
            "sampled_posts": sampled_posts,
            "sampled_comments": sampled_comments,
        }
        self.state_store.write(payload)
        return status
