from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from app.models import MediaItem
from app.paths import app_data_dir


def normalize_text(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).casefold().strip()


def matches_query(query: str, target: str) -> bool:
    norm_q = normalize_text(query)
    if not norm_q:
        return True
    norm_t = normalize_text(target)
    if not norm_t:
        return False
    if norm_q in norm_t:
        return True
    # Split query into words and check if all exist in target
    q_words = norm_q.split()
    if len(q_words) > 1 and all(qw in norm_t for qw in q_words):
        return True
    return False


class CatalogManager:
    def __init__(self, cache_file: Path | None = None) -> None:
        self.cache_file = cache_file or app_data_dir() / "catalog_cache.json"
        self.items: dict[str, MediaItem] = {}  # id -> MediaItem
        self.categories_by_kind: dict[str, set[str]] = {
            "live": set(),
            "movie": set(),
            "series": set(),
        }
        self.load_cache()

    def load_cache(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            for raw in data.get("items", []):
                item = MediaItem.from_dict(raw)
                self.items[item.id] = item
                if item.kind in self.categories_by_kind and item.category_name:
                    self.categories_by_kind[item.kind].add(item.category_name)
        except Exception:
            pass

    def save_cache(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "items": [item.to_dict() for item in self.items.values()]
            }
            temp = self.cache_file.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            temp.replace(self.cache_file)
        except Exception:
            pass

    def add_items(self, new_items: list[MediaItem], replace_account: str | None = None) -> None:
        if replace_account:
            self.items = {k: v for k, v in self.items.items() if v.account_id != replace_account}

        for item in new_items:
            self.items[item.id] = item
            if item.kind in self.categories_by_kind and item.category_name:
                self.categories_by_kind[item.kind].add(item.category_name)

        self.save_cache()

    def get_items(
        self,
        kind: str | None = None,
        category: str | None = None,
        query: str = "",
        hidden_categories: list[str] | None = None,
        locked_categories: list[str] | None = None,
        unlock_locked: bool = False,
        account_id: str | None = None,
    ) -> list[MediaItem]:
        hidden_set = {normalize_text(c) for c in (hidden_categories or [])}
        locked_set = {normalize_text(c) for c in (locked_categories or [])}

        results: list[MediaItem] = []
        for item in self.items.values():
            if kind and item.kind != kind:
                continue

            if account_id and account_id != "all" and item.account_id != account_id:
                continue

            item_cat_norm = normalize_text(item.category_name)

            # Filter out hidden categories completely
            if item_cat_norm in hidden_set:
                continue

            # If locked and not unlocked, skip (or filter)
            if not unlock_locked and item_cat_norm in locked_set:
                continue

            # Category filter
            if category and category != "all" and normalize_text(category) != item_cat_norm:
                continue

            # Search query filter
            if query and not (matches_query(query, item.title) or matches_query(query, item.category_name)):
                continue

            results.append(item)

        return results

    def get_categories(self, kind: str, hidden_categories: list[str] | None = None) -> list[str]:
        hidden_set = {normalize_text(c) for c in (hidden_categories or [])}
        cats = [
            c for c in sorted(self.categories_by_kind.get(kind, set()))
            if normalize_text(c) not in hidden_set
        ]
        return cats

    def global_search(
        self,
        query: str,
        hidden_categories: list[str] | None = None,
        locked_categories: list[str] | None = None,
        account_id: str | None = None,
    ) -> dict[str, list[MediaItem]]:
        """Returns results grouped by kind: live, movie, series"""
        return {
            "live": self.get_items("live", query=query, hidden_categories=hidden_categories, locked_categories=locked_categories, account_id=account_id)[:30],
            "movie": self.get_items("movie", query=query, hidden_categories=hidden_categories, locked_categories=locked_categories, account_id=account_id)[:30],
            "series": self.get_items("series", query=query, hidden_categories=hidden_categories, locked_categories=locked_categories, account_id=account_id)[:30],
        }

    def clear(self) -> None:
        self.items.clear()
        for s in self.categories_by_kind.values():
            s.clear()
        if self.cache_file.exists():
            self.cache_file.unlink(missing_ok=True)
