from __future__ import annotations

import re
import uuid
from typing import Any

from app.models import MediaItem


EXTINF_ATTR_REGEX = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')


def parse_m3u_content(content: str, account_id: str = "") -> list[MediaItem]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    items: list[MediaItem] = []

    current_meta: dict[str, Any] = {}

    for line in lines:
        if line.startswith("#EXTINF:"):
            current_meta = {}
            # Extract attributes (tvg-id, tvg-name, tvg-logo, group-title, etc.)
            for match in EXTINF_ATTR_REGEX.finditer(line):
                key, val = match.groups()
                current_meta[key.lower()] = val

            # Channel / Title is after the last comma
            if "," in line:
                current_meta["title"] = line.rsplit(",", 1)[1].strip()
            else:
                current_meta["title"] = current_meta.get("tvg-name", "Sem título")

        elif not line.startswith("#") and current_meta:
            stream_url = line
            title = current_meta.get("title") or current_meta.get("tvg-name") or "Canal M3U"
            logo = current_meta.get("tvg-logo") or ""
            group = current_meta.get("group-title") or "Geral"
            tvg_id = current_meta.get("tvg-id") or ""

            # Determine kind (live, movie, series)
            group_lower = group.lower()
            url_lower = stream_url.lower()

            if any(k in group_lower for k in ("serie", "série", "series", "season", "temporada")) or \
               re.search(r"s\d{1,2}e\d{1,2}", title.lower()):
                kind = "series"
            elif any(k in group_lower for k in ("filme", "movie", "vod", "cinema")) or \
                 any(url_lower.endswith(ext) for ext in (".mp4", ".mkv", ".avi")):
                kind = "movie"
            else:
                kind = "live"

            item_id = f"m3u_{account_id}_{uuid.uuid5(uuid.NAMESPACE_URL, stream_url).hex[:12]}"
            media_item = MediaItem(
                id=item_id,
                kind=kind,
                title=title,
                stream_id=item_id,
                category_id=group,
                category_name=group,
                poster=logo,
                backdrop=logo,
                stream_url=stream_url,
                account_id=account_id,
                epg_channel_id=tvg_id,
                raw_data=current_meta,
            )
            items.append(media_item)
            current_meta = {}

    return items
