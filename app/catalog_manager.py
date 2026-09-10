from __future__ import annotations

import json
import difflib
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


def search_score(query: str, target: str) -> float:
    """Return a relevance score without letting fuzzy matching flood the results."""
    norm_q = normalize_text(query)
    if not norm_q:
        return 1.0
    norm_t = normalize_text(target)
    if not norm_t:
        return 0.0
    if norm_q == norm_t:
        return 1000.0
    if norm_t.startswith(norm_q):
        return 850.0 - min(len(norm_t) - len(norm_q), 100)
    if norm_q in norm_t:
        return 700.0 - norm_t.index(norm_q)

    query_tokens = re.findall(r"[\w']+", norm_q)
    target_tokens = re.findall(r"[\w']+", norm_t)
    if not query_tokens or not target_tokens:
        return 0.0

    token_scores: list[float] = []
    for query_token in query_tokens:
        best = 0.0
        for target_token in target_tokens:
            if query_token == target_token:
                best = 1.0
            elif target_token.startswith(query_token) or query_token.startswith(target_token):
                best = max(best, 0.9)
            elif len(query_token) >= 4 and len(target_token) >= 4:
                ratio = difflib.SequenceMatcher(None, query_token, target_token).ratio()
                if ratio >= 0.78:
                    best = max(best, ratio)
        if best == 0.0:
            return 0.0
        token_scores.append(best)
    return 500.0 * (sum(token_scores) / len(token_scores))


def matches_query(query: str, target: str) -> bool:
    return search_score(query, target) > 0


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
            cached_items = []
            for item in self.items.values():
                raw = item.to_dict()
                # Stream URLs may contain Xtream credentials or signed M3U tokens.
                # They are reconstructed after account sync and never persisted here.
                raw["stream_url"] = ""
                cached_items.append(raw)
            data = {
                "items": cached_items
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

        ranked_results: list[tuple[float, MediaItem]] = []
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
            relevance = 1.0
            if query:
                title_score = search_score(query, item.title)
                category_score = search_score(query, item.category_name) * 0.45
                relevance = max(title_score, category_score)
                if relevance <= 0:
                    continue

            ranked_results.append((relevance, item))

        if query:
            ranked_results.sort(key=lambda pair: (-pair[0], normalize_text(pair[1].title)))
        return [item for _, item in ranked_results]

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
