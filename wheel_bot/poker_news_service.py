from __future__ import annotations

import asyncio
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("wheel_bot.poker_news")

_USER_AGENT = "WheelBotMorningDigest/1.0 (+https://t.me/)"
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)

RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("GipsyTeam", "https://www.gipsyteam.ru/rss/news.xml"),
    ("GipsyTeam LIVE", "https://www.gipsyteam.ru/rss/reportages.xml"),
    ("PokerNews", "https://www.pokernews.com/news/rss"),
    ("CardPlayer", "https://www.cardplayer.com/rss/news"),
)


@dataclass(frozen=True)
class PokerNewsItem:
    title: str
    summary: str
    link: str
    image_url: Optional[str]
    source: str
    published: Optional[datetime]
    focus_score: int = 0


def _local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return re.sub(r"\s+", " ", (elem.text or "") + "".join(elem.itertext())).strip()


def _image_from_element(elem: ET.Element) -> Optional[str]:
    for child in elem.iter():
        tag = _local_tag(child.tag)
        if tag == "enclosure":
            mime = (child.get("type") or "").lower()
            url = (child.get("url") or "").strip()
            if url and (mime.startswith("image/") or not mime):
                return url
        if tag in ("content", "thumbnail", "media:content", "media:thumbnail"):
            url = (child.get("url") or child.get("href") or "").strip()
            if url:
                return url
    return None


def _parse_rss_xml(source: str, xml_text: str) -> list[PokerNewsItem]:
    root = ET.fromstring(xml_text)
    root_tag = _local_tag(root.tag)
    items: list[ET.Element] = []
    if root_tag == "rss":
        channel = root.find("channel")
        if channel is not None:
            items = channel.findall("item")
    elif root_tag == "feed":
        items = root.findall("{http://www.w3.org/2005/Atom}entry")
        if not items:
            items = root.findall("entry")

    out: list[PokerNewsItem] = []
    for item in items:
        title = _text(item.find("title"))
        if not title:
            continue
        title = re.sub(r"^(?:статья|видео|live|интервью|обзор)\s*[:\-—]\s*", "", title, flags=re.IGNORECASE).strip()
        link = ""
        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.get("href") or link_el.text or "").strip()
        if not link:
            guid = _text(item.find("guid"))
            if guid.startswith("http"):
                link = guid
        summary = _text(item.find("description")) or _text(item.find("summary"))
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        published: Optional[datetime] = None
        for tag in ("pubDate", "published", "updated"):
            raw = _text(item.find(tag))
            if raw:
                try:
                    published = parsedate_to_datetime(raw)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError, OverflowError):
                    published = None
                break
        image_url = _image_from_element(item)
        out.append(
            PokerNewsItem(
                title=title,
                summary=summary[:500],
                link=link,
                image_url=image_url,
                source=source,
                published=published,
            )
        )
    return out


def _fetch_url(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _fetch_og_image_sync(article_url: str) -> Optional[str]:
    if not article_url.startswith("http"):
        return None
    try:
        html = _fetch_url(article_url, timeout=20)
    except Exception:
        return None
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_ALT):
        m = pattern.search(html)
        if m:
            return m.group(1).strip()
    return None


def _focus_keywords(focus_events: str) -> list[str]:
    parts = re.split(r"[,;\n]+", focus_events or "")
    keys = [p.strip().lower() for p in parts if p.strip()]
    return keys or ["wsop", "world series of poker"]


def _score_item(item: PokerNewsItem, keywords: list[str]) -> int:
    hay = f"{item.title} {item.summary}".lower()
    score = 0
    for kw in keywords:
        if kw and kw in hay:
            score += 10 if len(kw) >= 4 else 5
    if score:
        score += 3
    return score


def _rank_items(items: list[PokerNewsItem], focus_events: str) -> list[PokerNewsItem]:
    keywords = _focus_keywords(focus_events)
    ranked: list[PokerNewsItem] = []
    for item in items:
        score = _score_item(item, keywords)
        ranked.append(
            PokerNewsItem(
                title=item.title,
                summary=item.summary,
                link=item.link,
                image_url=item.image_url,
                source=item.source,
                published=item.published,
                focus_score=score,
            )
        )
    ranked.sort(
        key=lambda x: (
            x.focus_score,
            x.published.timestamp() if x.published else 0,
        ),
        reverse=True,
    )
    return ranked


def _fetch_all_news_sync() -> list[PokerNewsItem]:
    all_items: list[PokerNewsItem] = []
    seen_titles: set[str] = set()
    for source_name, feed_url in RSS_FEEDS:
        try:
            xml_text = _fetch_url(feed_url)
            for item in _parse_rss_xml(source_name, xml_text):
                key = item.title.lower()
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                all_items.append(item)
        except Exception:
            log.warning("failed to fetch poker RSS: %s", feed_url, exc_info=True)
    return all_items


async def fetch_poker_news(focus_events: str, *, limit: int = 8) -> list[PokerNewsItem]:
    items = await asyncio.to_thread(_fetch_all_news_sync)
    ranked = _rank_items(items, focus_events)
    return ranked[:limit]


async def pick_featured_news(focus_events: str) -> Optional[PokerNewsItem]:
    items = await fetch_poker_news(focus_events, limit=12)
    if not items:
        return None
    featured = items[0]
    if featured.focus_score <= 0:
        # Нет новостей по приоритетным событиям — берём самую свежую яркую.
        featured = max(
            items,
            key=lambda x: x.published.timestamp() if x.published else 0,
        )
    if not featured.image_url and featured.link:
        og = await asyncio.to_thread(_fetch_og_image_sync, featured.link)
        if og:
            featured = PokerNewsItem(
                title=featured.title,
                summary=featured.summary,
                link=featured.link,
                image_url=og,
                source=featured.source,
                published=featured.published,
                focus_score=featured.focus_score,
            )
    return featured


def format_news_context(items: list[PokerNewsItem], focus_events: str) -> str:
    if not items:
        return "Свежих заголовков из RSS не получено."
    lines = [f"Приоритетные события: {focus_events or 'WSOP, World Series of Poker'}", ""]
    for i, item in enumerate(items[:6], start=1):
        when = ""
        if item.published:
            when = item.published.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M")
        score = f", релевантность {item.focus_score}" if item.focus_score else ""
        lines.append(f"{i}. [{item.source}] {item.title}")
        if item.summary:
            lines.append(f"   {item.summary[:220]}")
        if when:
            lines.append(f"   Время: {when}{score}")
        if item.link:
            lines.append(f"   Ссылка: {item.link}")
        lines.append("")
    return "\n".join(lines).strip()
