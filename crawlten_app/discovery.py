"""Research discovery and user-controlled download services."""
from __future__ import annotations

import csv
import threading
from collections import deque
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .engine import CrawlConfig, Crawler, MemoryStore, RobotsPolicy, USER_AGENT, normalize_url


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    file_type: str
    source: str
    size: int | None = None
    access: str = "Found"

    @property
    def size_label(self) -> str:
        if self.size is None:
            return "Unknown"
        if self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        return f"{self.size / (1024 * 1024):.1f} MB"


class DiscoveryService:
    def __init__(self, file_types: frozenset[str], max_results: int = 50,
                 emit=None, stop_event: threading.Event | None = None,
                 session: requests.Session | None = None):
        self.file_types = file_types
        self.max_results = max_results
        self.emit = emit or (lambda _event, _data: None)
        self.stop_event = stop_event or threading.Event()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = RobotsPolicy(self.session, 15)

    def cancel(self) -> None:
        """Request cancellation and close idle network resources."""
        self.stop_event.set()
        self.session.close()

    def search(self, query: str, website: str = "") -> list[SearchResult]:
        if not query.strip() and not website.strip():
            raise ValueError("Enter a research query or website.")
        candidates = (
            self._discover_website(website, query)
            if website.strip()
            else self._search_web(query)
        )
        results: list[SearchResult] = []
        seen: set[str] = set()
        for title, url in candidates:
            if self.stop_event.is_set() or len(results) >= self.max_results:
                break
            url = self._unwrap_search_url(url)
            if not url or url in seen or not self._wanted(url):
                continue
            seen.add(url)
            result = self._inspect(title, url)
            results.append(result)
            self.emit("result", {"result": result})
        if self.stop_event.is_set():
            self.emit("cancelled", {})
        else:
            self.emit("search_complete", {"count": len(results)})
        return results

    def _search_web(self, query: str):
        self.emit("status", {"message": "Searching the public web index…"})
        extensions = " OR ".join(f"filetype:{item}" for item in sorted(self.file_types))
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query + ' ' + extensions)}"
        response = self.session.get(url, timeout=(5, 8))
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        return [
            (anchor.get_text(" ", strip=True) or "Untitled result", anchor["href"])
            for anchor in soup.select("a.result__a[href]")
        ]

    def _discover_website(self, website: str, query: str):
        seed = normalize_url(website, website)
        if not seed:
            raise ValueError("Enter a valid HTTP or HTTPS website.")
        terms = tuple(word.lower() for word in query.split() if len(word) > 2)
        pending = deque([(seed, 0)])
        visited: set[str] = set()
        domains: set[str] = set()
        pages_per_domain: Counter[str] = Counter()
        found: list[tuple[str, str]] = []
        self.emit("status", {"message": "Autonomous research crawl started…"})
        while pending and not self.stop_event.is_set() and len(visited) < 100:
            page, depth = pending.popleft()
            domain = urlparse(page).netloc.lower()
            if (
                page in visited
                or depth > 3
                or len(domains | {domain}) > 12
                or pages_per_domain[domain] >= 15
            ):
                continue
            visited.add(page)
            domains.add(domain)
            pages_per_domain[domain] += 1
            if not self.robots.allowed(page):
                self.emit("status", {"message": f"Blocked by robots.txt: {page}"})
                continue
            try:
                response = self.session.get(page, timeout=(5, 8))
                response.raise_for_status()
            except requests.RequestException as exc:
                self.emit("status", {"message": f"Could not inspect {page}: {exc}"})
                continue
            if "html" not in response.headers.get("Content-Type", "").lower():
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                if self.stop_event.is_set():
                    break
                link = normalize_url(page, anchor["href"])
                if not link:
                    continue
                title = anchor.get_text(" ", strip=True) or Path(urlparse(link).path).name
                searchable = f"{title} {link}".lower()
                relevant = not terms or any(term in searchable for term in terms)
                if self._wanted(link) and relevant:
                    found.append((title or "Untitled document", link))
                elif depth < 3 and (
                    urlparse(link).netloc.lower() == domain or relevant
                ):
                    pending.append((link, depth + 1))
            self.emit("status", {
                "message": (
                    f"Researching page {len(visited)}/100 across "
                    f"{len(domains)}/12 domains — {len(found)} documents found"
                )
            })
            self.stop_event.wait(0.25)
        return found

    def _inspect(self, title: str, url: str) -> SearchResult:
        suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
        size = None
        access = "Available"
        try:
            if not self.robots.allowed(url):
                access = "Blocked by robots.txt"
            else:
                response = self.session.head(
                    url, allow_redirects=True, timeout=(5, 6))
                if response.status_code in {403, 405}:
                    response = self.session.get(
                        url, stream=True, timeout=(5, 6))
                response.raise_for_status()
                size = int(response.headers.get("Content-Length", 0) or 0) or None
        except requests.RequestException:
            access = "Unverified"
        return SearchResult(
            title=title.strip() or Path(urlparse(url).path).name or "Untitled document",
            url=url,
            file_type=suffix.upper() or "FILE",
            source=urlparse(url).netloc,
            size=size,
            access=access,
        )

    def _wanted(self, url: str) -> bool:
        return Path(urlparse(url).path.lower()).suffix.lstrip(".") in self.file_types

    @staticmethod
    def _unwrap_search_url(url: str) -> str | None:
        absolute = normalize_url("https://duckduckgo.com", url)
        if not absolute:
            return None
        parsed = urlparse(absolute)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            return parse_qs(parsed.query).get("uddg", [None])[0]
        return absolute


def download_selected(results: list[SearchResult], directory: Path,
                      file_types: frozenset[str], emit=None,
                      stop_event: threading.Event | None = None):
    memory = MemoryStore(Path.home() / ".local/share/crawlten/memory.json")
    crawler = Crawler(
        CrawlConfig(
            download_dir=directory,
            file_types=file_types,
            timeout=6.0,
            request_delay=0.25,
        ),
        memory,
        emit,
        stop_event,
    )
    for result in results:
        if stop_event and stop_event.is_set():
            break
        if result.access == "Blocked by robots.txt":
            continue
        crawler._download(result.url)
    return crawler.stats


def export_results(results: list[SearchResult], destination: Path) -> None:
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("Title", "Type", "Source", "Size", "Access", "URL"))
        for item in results:
            writer.writerow((
                item.title, item.file_type, item.source, item.size_label,
                item.access, item.url,
            ))
