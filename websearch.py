"""Keyless web search with optional keyed providers, used by research."""

import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import ClassVar

log = logging.getLogger("fam.websearch")
UA = "Mozilla/5.0 (X11; Linux x86_64) FamResearch/1.0"
TIMEOUT = 15


@dataclass
class Source:
    title: str
    url: str
    snippet: str = ""
    text: str = ""


def _get(url: str, data: bytes | None = None, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(2_000_000).decode("utf-8", "replace")


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _ddg_url(href: str) -> str:
    # DuckDuckGo wraps results in a redirect; the real target is the uddg parameter.
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    return q.get("uddg", [href])[0]


def parse_ddg(page: str, limit: int = 8) -> list[Source]:
    links = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.DOTALL)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.DOTALL)
    out = []
    for i, (href, title) in enumerate(links):
        url = _ddg_url(html.unescape(href))
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue
        out.append(Source(_clean(title), url, _clean(snippets[i]) if i < len(snippets) else ""))
        if len(out) >= limit:
            break
    return out


def duckduckgo(query: str, limit: int = 8) -> list[Source]:
    body = urllib.parse.urlencode({"q": query}).encode()
    return parse_ddg(_get("https://html.duckduckgo.com/html/", body), limit)


def brave(query: str, limit: int = 8) -> list[Source]:
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": limit})
    data = json.loads(_get(url, headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"}))
    return [Source(r.get("title", ""), r["url"], _clean(r.get("description", ""))) for r in data.get("web", {}).get("results", [])]


def tavily(query: str, limit: int = 8) -> list[Source]:
    body = json.dumps({"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": limit}).encode()
    data = json.loads(_get("https://api.tavily.com/search", body, {"Content-Type": "application/json"}))
    return [Source(r.get("title", ""), r["url"], r.get("content", "")[:500]) for r in data.get("results", [])]


def providers() -> list[tuple[str, Callable[[str, int], list[Source]]]]:
    chain: list[tuple[str, Callable[[str, int], list[Source]]]] = []
    if os.environ.get("TAVILY_API_KEY"):
        chain.append(("tavily", tavily))
    if os.environ.get("BRAVE_API_KEY"):
        chain.append(("brave", brave))
    chain.append(("duckduckgo", duckduckgo))
    return chain


class _Text(HTMLParser):
    skip: ClassVar[set[str]] = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.depth += tag in self.skip

    def handle_endtag(self, tag: str) -> None:
        self.depth -= tag in self.skip and self.depth > 0

    def handle_data(self, data: str) -> None:
        if not self.depth and data.strip():
            self.parts.append(data.strip())


def page_text(url: str, limit: int = 2500) -> str:
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        parser = _Text()
        parser.feed(_get(url))
    except (OSError, ValueError) as e:
        log.info("fetch failed for %s: %s", url, type(e).__name__)
        return ""
    return re.sub(r"\s+", " ", " ".join(parser.parts))[:limit]


def search(query: str, limit: int = 6, fetch: int = 3) -> list[dict]:
    """Search with the first provider that answers, then read the top pages."""
    for name, provider in providers():
        try:
            hits = provider(query, limit)
        except (OSError, ValueError, KeyError) as e:
            log.warning("search provider %s failed: %s", name, type(e).__name__)
            continue
        if hits:
            log.info("search provider %s served %d result(s)", name, len(hits))
            for hit in hits[:fetch]:
                hit.text = page_text(hit.url)
            return [{**asdict(h), "provider": name} for h in hits]
    return []
