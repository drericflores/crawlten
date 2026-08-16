"""Crawler and downloader engine, independent of the graphical interface."""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger(__name__)
USER_AGENT = "CrawlTen/1.0 (+https://github.com/drericflores/crawlten)"
DEFAULT_TYPES = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "mp3": {"audio/mpeg"},
    "mpeg": {"video/mpeg", "audio/mpeg"},
}


@dataclass(frozen=True)
class CrawlConfig:
    download_dir: Path
    file_types: frozenset[str] = frozenset({"pdf"})
    max_depth: int = 2
    max_pages: int = 100
    max_file_bytes: int = 100 * 1024 * 1024
    request_delay: float = 1.0
    timeout: float = 15.0


@dataclass
class CrawlStats:
    pages: int = 0
    downloaded: int = 0
    skipped: int = 0
    blocked: int = 0
    failed: int = 0


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"visited": [], "valuable": []}
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data["visited"] = list(dict.fromkeys(raw.get("visited", [])))
                self.data["valuable"] = list(dict.fromkeys(raw.get("valuable", [])))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, TypeError):
            LOG.warning("Could not read memory file %s", self.path, exc_info=True)

    def remember(self, url: str, valuable: bool = False) -> None:
        with self._lock:
            if url not in self.data["visited"]:
                self.data["visited"].append(url)
            if valuable and url not in self.data["valuable"]:
                self.data["valuable"].append(url)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            temporary.replace(self.path)


def normalize_url(base: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "javascript:", "data:")):
        return None
    parsed = urlparse(urljoin(base, href))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse(parsed._replace(fragment=""))


def safe_filename(url: str, content_type: str = "") -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name or "download"
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "download"
    if "." not in name:
        extension = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if extension:
            name += extension
    return name[:180]


class RobotsPolicy:
    def __init__(self, session: requests.Session, timeout: float):
        self.session = session
        self.timeout = timeout
        self._cache: dict[str, RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._cache:
            parser = RobotFileParser()
            parser.set_url(urljoin(origin, "/robots.txt"))
            try:
                response = self.session.get(parser.url, timeout=self.timeout)
                if response.status_code == 404:
                    parser.parse([])
                else:
                    response.raise_for_status()
                    parser.parse(response.text.splitlines())
            except requests.RequestException:
                LOG.warning("Unable to verify robots policy for %s", origin)
                return False
            self._cache[origin] = parser
        return self._cache[origin].can_fetch(USER_AGENT, url)


class Crawler:
    def __init__(self, config: CrawlConfig, memory: MemoryStore, emit=None,
                 stop_event: threading.Event | None = None, session=None):
        self.config = config
        self.memory = memory
        self.emit = emit or (lambda _event, _data: None)
        self.stop_event = stop_event or threading.Event()
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = RobotsPolicy(self.session, config.timeout)
        self.stats = CrawlStats()

    def crawl(self, seed_url: str) -> CrawlStats:
        seed = normalize_url(seed_url, seed_url)
        if not seed:
            raise ValueError("Enter a valid HTTP or HTTPS URL")
        seed_host = urlparse(seed).netloc.lower()
        pending = deque([(seed, 0)])
        visited: set[str] = set()
        while pending and not self.stop_event.is_set() and self.stats.pages < self.config.max_pages:
            url, depth = pending.popleft()
            if url in visited or depth > self.config.max_depth:
                continue
            visited.add(url)
            if not self.robots.allowed(url):
                self.stats.blocked += 1
                self.emit("status", {"message": f"Blocked by robots.txt: {url}"})
                continue
            try:
                response = self.session.get(url, timeout=self.config.timeout)
                response.raise_for_status()
            except requests.RequestException as exc:
                self.stats.failed += 1
                self.emit("status", {"message": f"Request failed: {url} ({exc})"})
                continue
            self.stats.pages += 1
            content_type = response.headers.get("Content-Type", "").lower()
            if self._is_requested_file(url, content_type):
                self._save_response(url, response)
                continue
            if "html" not in content_type:
                self.stats.skipped += 1
                continue
            valuable = False
            for link in self._extract_links(url, response.text):
                if self.stop_event.is_set():
                    break
                if self._looks_requested(link):
                    self._download(link)
                    valuable = True
                elif depth < self.config.max_depth and urlparse(link).netloc.lower() == seed_host:
                    pending.append((link, depth + 1))
            self.memory.remember(url, valuable)
            self.emit("progress", {"pages": self.stats.pages, "queued": len(pending)})
            if pending and self.config.request_delay:
                time.sleep(self.config.request_delay)
        self.emit("complete", {"stats": self.stats})
        return self.stats

    @staticmethod
    def _extract_links(base: str, html: str) -> list[str]:
        result = []
        for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            link = normalize_url(base, anchor["href"])
            if link:
                result.append(link)
        return list(dict.fromkeys(result))

    def _looks_requested(self, url: str) -> bool:
        return Path(urlparse(url).path.lower()).suffix.lstrip(".") in self.config.file_types

    def _is_requested_file(self, url: str, content_type: str) -> bool:
        if self._looks_requested(url):
            return True
        mime = content_type.split(";", 1)[0].strip()
        return any(mime in DEFAULT_TYPES.get(ext, set()) for ext in self.config.file_types)

    def _download(self, url: str) -> None:
        if not self.robots.allowed(url):
            self.stats.blocked += 1
            self.emit("status", {"message": f"Blocked download: {url}"})
            return
        try:
            response = self.session.get(url, stream=True, timeout=self.config.timeout)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if not self._is_requested_file(url, content_type):
                self.stats.skipped += 1
                self.emit("status", {"message": f"Skipped unexpected content type: {url}"})
                response.close()
                return
            self._save_response(url, response)
        except requests.RequestException as exc:
            self.stats.failed += 1
            self.emit("status", {"message": f"Download failed: {url} ({exc})"})

    def _save_response(self, url: str, response: requests.Response) -> None:
        length = int(response.headers.get("Content-Length", 0) or 0)
        if length > self.config.max_file_bytes:
            self.stats.skipped += 1
            self.emit("status", {"message": f"Skipped oversized file: {url}"})
            response.close()
            return
        self.config.download_dir.mkdir(parents=True, exist_ok=True)
        destination = self.config.download_dir / safe_filename(
            url, response.headers.get("Content-Type", ""))
        if destination.exists():
            digest = hashlib.sha256(url.encode()).hexdigest()[:8]
            destination = destination.with_name(
                f"{destination.stem}-{digest}{destination.suffix}")
        temporary = destination.with_suffix(destination.suffix + ".part")
        written = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if self.stop_event.is_set():
                        raise InterruptedError("Download cancelled")
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > self.config.max_file_bytes:
                        raise ValueError("File exceeds configured size limit")
                    handle.write(chunk)
            temporary.replace(destination)
            self.stats.downloaded += 1
            self.emit("status", {"message": f"Downloaded: {destination.name}"})
        except (OSError, ValueError, InterruptedError) as exc:
            temporary.unlink(missing_ok=True)
            self.stats.failed += 1
            self.emit("status", {"message": f"Could not save {url}: {exc}"})
        finally:
            response.close()
