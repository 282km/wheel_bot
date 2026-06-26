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


@dataclass(frozen=True)
class PokerNewsDigest:
    items: list[PokerNewsItem]
    hot_topics: list[str]


# Известные серии/ивенты — паттерны для автоопределения актуальных тем из RSS.
_SERIES_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("WSOP", (r"\bwsop\b", r"world series of poker", r"мировая серия", r"мировой серии")),
    ("EPT", (r"\bept\b", r"european poker tour", r"европейск\w* тур")),
    ("WPT", (r"\bwpt\b", r"world poker tour")),
    ("APT", (r"\bapt\b", r"asian poker tour")),
    ("EAPT", (r"\beapt\b", r"european asian poker tour")),
    ("Triton", (r"\btriton\b", r"тритон")),
    ("WCOOP", (r"\bwcoop\b",)),
    ("SCOOP", (r"\bscoop\b",)),
    ("PCA", (r"\bpca\b", r"pokerstars championship")),
    ("PGT", (r"\bpgt\b", r"pokergo tour")),
    ("Irish Open", (r"irish open",)),
    ("partypoker LIVE", (r"partypoker live", r"partypoker\s+live")),
    ("Poker Masters", (r"poker masters",)),
    ("Super High Roller", (r"super high roller", r"shr\b", r"суперхайроллер")),
)

_EVENT_SIGNALS: tuple[str, ...] = (
    "финальный стол",
    "final table",
    "главный ивент",
    "main event",
    "победил",
    "победитель",
    "wins ",
    " champion",
    "чемпион",
    "bracelet",
    "браслет",
    "турнир",
    "tournament",
    "занял",
    "выиграл",
)


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


def _fetch_url(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _fetch_og_image_sync(article_url: str) -> Optional[str]:
    if not article_url.startswith("http"):
        return None
    try:
        html = _fetch_url(article_url, timeout=10)
    except Exception:
        return None
    for pattern in (_OG_IMAGE_RE, _OG_IMAGE_RE_ALT):
        m = pattern.search(html)
        if m:
            return m.group(1).strip()
    return None


def _item_haystack(item: PokerNewsItem) -> str:
    return f"{item.title} {item.summary}".lower()


def _matches_series(hay: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pat, hay, re.IGNORECASE) for pat in patterns)


def _recency_weight(published: Optional[datetime], *, now: Optional[datetime] = None) -> float:
    if published is None:
        return 0.35
    ref = now or datetime.now(timezone.utc)
    age_hours = max(0.0, (ref - published).total_seconds() / 3600.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 48:
        return 0.85
    if age_hours <= 96:
        return 0.65
    if age_hours <= 168:
        return 0.45
    return 0.25


def _detect_hot_topics(items: list[PokerNewsItem], *, max_topics: int = 5) -> list[str]:
    """Определяет актуальные серии/ивенты по свежим заголовкам RSS."""
    now = datetime.now(timezone.utc)
    counts: dict[str, float] = {}
    for item in items:
        hay = _item_haystack(item)
        weight = _recency_weight(item.published, now=now)
        for name, patterns in _SERIES_PATTERNS:
            if _matches_series(hay, patterns):
                counts[name] = counts.get(name, 0.0) + weight
    ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [name for name, score in ranked[:max_topics] if score >= 0.45]


def _score_item_auto(item: PokerNewsItem, hot_topics: list[str], *, now: Optional[datetime] = None) -> int:
    hay = _item_haystack(item)
    score = 0

    ref = now or datetime.now(timezone.utc)
    if item.published:
        age_hours = max(0.0, (ref - item.published).total_seconds() / 3600.0)
        if age_hours <= 24:
            score += 30
        elif age_hours <= 48:
            score += 22
        elif age_hours <= 96:
            score += 12
        elif age_hours <= 168:
            score += 6

    series_by_name = {name: patterns for name, patterns in _SERIES_PATTERNS}
    for topic in hot_topics:
        patterns = series_by_name.get(topic)
        if patterns and _matches_series(hay, patterns):
            score += 18

    if any(sig in hay for sig in _EVENT_SIGNALS):
        score += 8

    return score


def _rank_items_auto(items: list[PokerNewsItem]) -> tuple[list[PokerNewsItem], list[str]]:
    hot_topics = _detect_hot_topics(items)
    now = datetime.now(timezone.utc)
    ranked: list[PokerNewsItem] = []
    for item in items:
        score = _score_item_auto(item, hot_topics, now=now)
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
    return ranked, hot_topics


def _fetch_single_feed(source_name: str, feed_url: str) -> list[PokerNewsItem]:
    try:
        xml_text = _fetch_url(feed_url)
        return _parse_rss_xml(source_name, xml_text)
    except Exception:
        log.warning("failed to fetch poker RSS: %s", feed_url, exc_info=True)
        return []


async def fetch_poker_news_digest(*, limit: int = 8) -> PokerNewsDigest:
    # Грузим фиды параллельно, чтобы не упереться в таймаут шлюза.
    results = await asyncio.gather(
        *(asyncio.to_thread(_fetch_single_feed, name, url) for name, url in RSS_FEEDS),
        return_exceptions=True,
    )
    all_items: list[PokerNewsItem] = []
    seen_titles: set[str] = set()
    for res in results:
        if isinstance(res, BaseException):
            continue
        for item in res:
            key = item.title.lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            all_items.append(item)
    ranked, hot_topics = _rank_items_auto(all_items)
    return PokerNewsDigest(items=ranked[:limit], hot_topics=hot_topics)


async def fetch_poker_news(*, limit: int = 8) -> list[PokerNewsItem]:
    digest = await fetch_poker_news_digest(limit=limit)
    return digest.items


def _select_featured(items: list[PokerNewsItem]) -> Optional[PokerNewsItem]:
    if not items:
        return None
    featured = items[0]
    if featured.focus_score <= 0:
        featured = max(items, key=lambda x: x.published.timestamp() if x.published else 0)
    return featured


async def attach_image(item: PokerNewsItem) -> PokerNewsItem:
    if item.image_url or not item.link:
        return item
    og = await asyncio.to_thread(_fetch_og_image_sync, item.link)
    if not og:
        return item
    return PokerNewsItem(
        title=item.title,
        summary=item.summary,
        link=item.link,
        image_url=og,
        source=item.source,
        published=item.published,
        focus_score=item.focus_score,
    )


async def pick_featured_news() -> Optional[PokerNewsItem]:
    digest = await fetch_poker_news_digest(limit=12)
    featured = _select_featured(digest.items)
    if featured is None:
        return None
    return await attach_image(featured)


def format_news_context(items: list[PokerNewsItem], hot_topics: list[str]) -> str:
    if not items:
        return "Свежих заголовков из RSS не получено."
    if hot_topics:
        lines = [
            f"Сейчас в покерной ленте особенно актуальны: {', '.join(hot_topics)}.",
            "Выбери самую свежую и яркую тему из списка — не зацикливайся на прошедших сериях.",
            "",
        ]
    else:
        lines = [
            "Ярких серий в свежих заголовках не выделено — выбери самую интересную актуальную новость.",
            "",
        ]
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
