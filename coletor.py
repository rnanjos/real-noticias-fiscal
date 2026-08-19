from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from fontes import SOURCES

TZ = ZoneInfo("America/Sao_Paulo")
WINDOW_HOURS = 24
MAX_LINKS_PER_SOURCE = 40
TIMEOUT = 25

OUTPUT = Path(__file__).with_name("rss.xml")
STATUS_OUTPUT = Path(__file__).with_name("status.json")

DOCS_DIR = Path(__file__).with_name("docs")
DOCS_RSS_OUTPUT = DOCS_DIR / "rss.xml"
DOCS_STATUS_OUTPUT = DOCS_DIR / "status.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; RealNoticiasFiscal/1.0; +https://github.com/rnanjos/real-noticias-fiscal)",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("real-noticias-fiscal")

PT_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    published: datetime
    source: str

    @property
    def guid(self) -> str:
        return hashlib.sha256(
            self.link.encode("utf-8")
        ).hexdigest()


def clean_text(value: str | None) -> str:
    if not value:
        return ""

    return re.sub(
        r"\s+",
        " ",
        BeautifulSoup(
            value,
            "html.parser"
        ).get_text(" ", strip=True)
    ).strip()


def canonical_url(url: str) -> str:
    p = urlparse(url)

    clean = p._replace(
        fragment="",
        query=""
    )

    result = urlunparse(clean)

    return (
        result.rstrip("/")
        if p.path not in ("", "/")
        else result
    )


def normalize_dt(
    dt: datetime,
    date_only: bool = False
) -> datetime:

    if date_only:
        dt = datetime.combine(
            dt.date(),
            time(23, 59, 59)
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)

    return dt.astimezone(TZ)


def parse_datetime(
    value: str | None
) -> datetime | None:

    if not value:
        return None

    text = clean_text(value)

    if not text:
        return None

    iso_candidate = (
        text
        .replace("Publicado", "")
        .replace("Publicada em", "")
        .strip(" :.-")
    )

    try:
        dt = dtparser.parse(
            iso_candidate,
            dayfirst=True,
            fuzzy=False
        )

        date_only = not bool(
            re.search(
                r"\b\d{1,2}:\d{2}\b|T\d{2}:\d{2}",
                text
            )
        )

        return normalize_dt(
            dt,
            date_only=date_only
        )

    except Exception:
        pass

    m = re.search(
        r"(\d{1,2})\s+de\s+([A-Za-zÀ-ÿ]+)\s+de\s+(\d{4})(?:\s+(?:às\s+)?(\d{1,2}):(\d{2}))?",
        text,
        re.I
    )

    if m:
        day, month_name, year, hh, mm = m.groups()

        month = PT_MONTHS.get(
            month_name.lower()
        )

        if month:
            dt = datetime(
                int(year),
                month,
                int(day),
                int(hh or 0),
                int(mm or 0)
            )

            return normalize_dt(
                dt,
                date_only=(hh is None)
            )

    m = re.search(
        r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?:\s+(?:às\s+)?(\d{1,2}):(\d{2}))?\b",
        text,
        re.I
    )

    if m:
        day, month, year, hh, mm = m.groups()

        dt = datetime(
            int(year),
            int(month),
            int(day),
            int(hh or 0),
            int(mm or 0)
        )

        return normalize_dt(
            dt,
            date_only=(hh is None)
        )

    return None


def request(url: str) -> requests.Response:

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT,
        allow_redirects=True
    )

    response.raise_for_status()

    return response


def first_meta(
    soup: BeautifulSoup,
    selectors: Iterable[tuple[str, str]]
) -> str:

    for key, value in selectors:

        tag = soup.find(
            "meta",
            attrs={key: value}
        )

        if tag and tag.get("content"):
            return str(
                tag["content"]
            ).strip()

    return ""


def iter_jsonld(soup: BeautifulSoup):

    for tag in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"}
    ):

        raw = (
            tag.string
            or tag.get_text(strip=True)
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)

        except Exception:
            continue

        stack = (
            data
            if isinstance(data, list)
            else [data]
        )

        while stack:

            obj = stack.pop()

            if isinstance(obj, dict):

                yield obj

                graph = obj.get("@graph")

                if isinstance(graph, list):
                    stack.extend(graph)

            elif isinstance(obj, list):
                stack.extend(obj)


def parse_article(
    url: str,
    source: str,
    date_hint: str = ""
) -> NewsItem | None:

    response = request(url)

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    canonical_tag = soup.find(
        "link",
        rel=lambda v: v and "canonical" in v
    )

    if canonical_tag and canonical_tag.get("href"):

        canonical = canonical_url(
            urljoin(
                response.url,
                canonical_tag.get("href")
            )
        )

    else:
        canonical = canonical_url(
            response.url
        )

    title = first_meta(
        soup,
        [
            ("property", "og:title"),
            ("name", "twitter:title")
        ]
    )

    if not title:

        h1 = soup.find("h1")

        title = clean_text(
            h1.get_text(" ", strip=True)
            if h1
            else ""
        )

    if not title:

        title = clean_text(
            soup.title.string
            if soup.title and soup.title.string
            else ""
        )

    summary = first_meta(
        soup,
        [
            ("property", "og:description"),
            ("name", "description"),
            ("name", "twitter:description")
        ]
    )

    summary = clean_text(summary)

    date_values: list[str] = []

    for k, v in [
        ("property", "article:published_time"),
        ("name", "article:published_time"),
        ("name", "date"),
        ("name", "pubdate"),
        ("itemprop", "datePublished"),
    ]:

        date_value = first_meta(
            soup,
            [(k, v)]
        )

        if date_value:
            date_values.append(
                date_value
            )

    for obj in iter_jsonld(soup):

        if obj.get("datePublished"):
            date_values.append(
                str(
                    obj["datePublished"]
                )
            )

        if not summary and obj.get("description"):
            summary = clean_text(
                str(
                    obj["description"]
                )
            )

        if not title and obj.get("headline"):
            title = clean_text(
                str(
                    obj["headline"]
                )
            )

    if date_hint:
        date_values.append(
            date_hint
        )

    if not date_values:

        body_text = clean_text(
            soup.get_text(
                " ",
                strip=True
            )
        )[:10000]

        candidates = re.findall(
            r"(?:Publicado(?:a)?(?:\s+em)?\s*)?(?:\d{1,2}[/.]\d{1,2}[/.]\d{4}(?:\s+(?:às\s+)?\d{1,2}:\d{2})?|\d{1,2}\s+de\s+[A-Za-zÀ-ÿ]+\s+de\s+\d{4}(?:\s+(?:às\s+)?\d{1,2}:\d{2})?)",
            body_text,
            flags=re.I,
        )

        date_values.extend(
            candidates[:5]
        )

    published = next(
        (
            dt
            for dt in (
                parse_datetime(x)
                for x in date_values
            )
            if dt
        ),
        None
    )

    if not title or not published:
        return None

    if not summary:

        first_p = soup.find("p")

        summary = clean_text(
            first_p.get_text(
                " ",
                strip=True
            )
            if first_p
            else ""
        )

    summary = summary[:600]

    return NewsItem(
        title=title[:250],
        link=canonical,
        summary=summary,
        published=published,
        source=source
    )


def link_allowed(
    url: str,
    source: dict
) -> bool:

    parsed = urlparse(url)

    host = (
        parsed
        .netloc
        .lower()
        .removeprefix("www.")
    )

    allowed = [
        domain
        .lower()
        .removeprefix("www.")
        for domain
        in source.get(
            "allowed_domains",
            []
        )
    ]

    if allowed and not any(
        host == domain
        or host.endswith(
            "." + domain
        )
        for domain
        in allowed
    ):
        return False

    if (
        source.get("link_patterns")
        and not any(
            re.search(
                pattern,
                url,
                re.I
            )
            for pattern
            in source["link_patterns"]
        )
    ):
        return False

    if any(
        re.search(
            pattern,
            url,
            re.I
        )
        for pattern
        in source.get(
            "exclude_patterns",
            []
        )
    ):
        return False

    return True


def collect_html(
    source: dict
) -> list[NewsItem]:

    response = request(
        source["url"]
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    candidates: list[
        tuple[str, str]
    ] = []

    seen: set[str] = set()

    for a in soup.find_all(
        "a",
        href=True
    ):

        href = canonical_url(
            urljoin(
                response.url,
                a["href"]
            )
        )

        if (
            href in seen
            or not link_allowed(
                href,
                source
            )
        ):
            continue

        text_value = clean_text(
            a.get_text(
                " ",
                strip=True
            )
        )

        if len(text_value) < 15:
            continue

        context = clean_text(
            a.parent.get_text(
                " ",
                strip=True
            )
            if a.parent
            else text_value
        )

        seen.add(href)

        candidates.append(
            (
                href,
                context[:600]
            )
        )

        if (
            len(candidates)
            >= MAX_LINKS_PER_SOURCE
        ):
            break

    items: list[NewsItem] = []

    for url, context in candidates:

        try:

            item = parse_article(
                url,
                source["name"],
                date_hint=context
            )

            if item:
                items.append(item)

        except Exception as exc:

            log.warning(
                "%s | falha no artigo %s | %s",
                source["name"],
                url,
                exc
            )

    return items


def collect_rss(
    source: dict
) -> list[NewsItem]:

    response = request(
        source["url"]
    )

    feed = feedparser.parse(
        response.content
    )

    items: list[NewsItem] = []

    for entry in feed.entries:

        published = None

        for key in (
            "published",
            "updated",
            "created"
        ):

            published = parse_datetime(
                entry.get(key)
            )

            if published:
                break

        if (
            not published
            and entry.get(
                "published_parsed"
            )
        ):

            published = datetime(
                *entry.published_parsed[:6],
                tzinfo=timezone.utc
            ).astimezone(TZ)

        if not published:
            continue

        link = canonical_url(
            entry.get(
                "link",
                ""
            )
        )

        title = clean_text(
            entry.get(
                "title",
                ""
            )
        )

        if not link or not title:
            continue

        summary = clean_text(
            entry.get(
                "summary",
                entry.get(
                    "description",
                    ""
                )
            )
        )[:600]

        items.append(
            NewsItem(
                title=title[:250],
                link=link,
                summary=summary,
                published=published,
                source=source["name"]
            )
        )

    return items


def collect_confaz(
    source: dict
) -> list[NewsItem]:

    response = request(
        source["url"]
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    items: list[NewsItem] = []

    current_date: datetime | None = None

    container = (
        soup.find(id="content-core")
        or soup.body
        or soup
    )

    for node in container.find_all(
        [
            "p",
            "li",
            "a",
            "h2",
            "h3",
            "div"
        ]
    ):

        text_value = clean_text(
            node.get_text(
                " ",
                strip=True
            )
        )

        maybe_date = (
            parse_datetime(text_value)
            if re.fullmatch(
                r"\d{1,2}[/.]\d{1,2}[/.]\d{4}",
                text_value
            )
            else None
        )

        if maybe_date:

            current_date = maybe_date

            continue

        if (
            node.name != "a"
            or not node.get("href")
            or not current_date
        ):
            continue

        title = clean_text(
            node.get_text(
                " ",
                strip=True
            )
        )

        if len(title) < 15:
            continue

        if not re.search(
            r"ATO|AJUSTE|CONV[EÊ]NIO|DESPACHO|PROTOCOLO|RETIF",
            title,
            re.I
        ):
            continue

        link = canonical_url(
            urljoin(
                response.url,
                node["href"]
            )
        )

        summary = ""

        parent_text = clean_text(
            node.parent.get_text(
                " ",
                strip=True
            )
            if node.parent
            else ""
        )

        if (
            parent_text
            and parent_text != title
        ):

            summary = (
                parent_text
                .replace(
                    title,
                    "",
                    1
                )
                .strip(
                    " -–—"
                )[:600]
            )

        items.append(
            NewsItem(
                title=title[:250],
                link=link,
                summary=summary,
                published=current_date,
                source=source["name"]
            )
        )

    return items


def within_window(
    item: NewsItem,
    now: datetime
) -> bool:

    start = now - timedelta(
        hours=WINDOW_HOURS
    )

    return (
        start
        <= item.published
        <= now + timedelta(
            minutes=5
        )
    )


def dedupe(
    items: list[NewsItem]
) -> list[NewsItem]:

    by_link: dict[
        str,
        NewsItem
    ] = {}

    title_keys: set[str] = set()

    result: list[
        NewsItem
    ] = []

    for item in sorted(
        items,
        key=lambda x: x.published,
        reverse=True
    ):

        link_key = canonical_url(
            item.link
        ).lower()

        title_key = re.sub(
            r"\W+",
            "",
            item.title.lower()
        )[:180]

        if (
            link_key in by_link
            or title_key in title_keys
        ):
            continue

        by_link[link_key] = item

        title_keys.add(
            title_key
        )

        result.append(
            item
        )

    return result


def xml_escape(
    value: str
) -> str:

    return html.escape(
        value or "",
        quote=False
    )


def build_rss(
    items: list[NewsItem],
    generated_at: datetime
) -> str:

    rows = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        '<title>Real Notícias Fiscal</title>',
        '<link>https://rnanjos.github.io/real-noticias-fiscal/rss.xml</link>',
        '<description>Notícias fiscais e tributárias consolidadas das últimas 24 horas.</description>',
        '<language>pt-BR</language>',
        f'<lastBuildDate>{format_datetime(generated_at.astimezone(timezone.utc))}</lastBuildDate>',
    ]

    for item in items:

        rows.extend(
            [
                '<item>',
                f'<title>{xml_escape(item.title)}</title>',
                f'<link>{xml_escape(item.link)}</link>',
                f'<description>{xml_escape(item.summary)}</description>',
                f'<pubDate>{format_datetime(item.published.astimezone(timezone.utc))}</pubDate>',
                f'<guid isPermaLink="false">{item.guid}</guid>',
                f'<category>{xml_escape(item.source)}</category>',
                '</item>',
            ]
        )

    rows.extend(
        [
            '</channel>',
            '</rss>',
            ''
        ]
    )

    return "\n".join(rows)


def main() -> int:

    now = datetime.now(TZ)

    all_items: list[
        NewsItem
    ] = []

    status = {
        "generatedAt": now.isoformat(),
        "windowHours": WINDOW_HOURS,
        "sources": []
    }

    for source in SOURCES:

        info = {
            "name": source["name"],
            "ok": True,
            "found": 0,
            "accepted": 0,
            "error": ""
        }

        try:

            if source["type"] == "rss":

                found = collect_rss(
                    source
                )

            elif source["type"] == "confaz":

                found = collect_confaz(
                    source
                )

            else:

                found = collect_html(
                    source
                )

            accepted = [
                item
                for item
                in found
                if within_window(
                    item,
                    now
                )
            ]

            info["found"] = len(
                found
            )

            info["accepted"] = len(
                accepted
            )

            all_items.extend(
                accepted
            )

            log.info(
                "%s | encontrados=%s | ultimas24h=%s",
                source["name"],
                len(found),
                len(accepted)
            )

        except Exception as exc:

            info["ok"] = False

            info["error"] = str(
                exc
            )[:500]

            log.error(
                "%s | fonte ignorada por erro: %s",
                source["name"],
                exc
            )

        status["sources"].append(
            info
        )

    final_items = dedupe(
        all_items
    )

    rss = build_rss(
        final_items,
        now
    )

    status["totalItems"] = len(
        final_items
    )

    status_json = json.dumps(
        status,
        ensure_ascii=False,
        indent=2
    )

    # Arquivos principais do repositório
    OUTPUT.write_text(
        rss,
        encoding="utf-8"
    )

    STATUS_OUTPUT.write_text(
        status_json,
        encoding="utf-8"
    )

    # Arquivos publicados pelo GitHub Pages
    DOCS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    DOCS_RSS_OUTPUT.write_text(
        rss,
        encoding="utf-8"
    )

    DOCS_STATUS_OUTPUT.write_text(
        status_json,
        encoding="utf-8"
    )

    log.info(
        "RSS gerado: %s | %s itens",
        OUTPUT,
        len(final_items)
    )

    log.info(
        "RSS publicado em: %s",
        DOCS_RSS_OUTPUT
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
