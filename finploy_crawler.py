#!/usr/bin/env python3
"""
finploy_crawler.py
Playwright-based crawler with in-memory frontier and sitemap output (no database required).
"""

import asyncio
import gzip
import os
import re
from pathlib import Path
from typing import Optional, Dict, List, Set, Any
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, tostring
import os as _os

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---------- CONFIG ----------
START_URL = "https://www.finploy.com/"
ALLOWED_HOSTS = {"www.finploy.com", "finploy.com", "www.finploy.co.uk", "finploy.co.uk"}
OUTPUT_DIR = Path("output_sitemaps")

CONCURRENT_PAGES = 8      # number of parallel Playwright pages
MAX_PAGES = 300000        # stop after this many unique pages (set high)
NAV_TIMEOUT = 45000       # milliseconds
SCROLL_PAUSE = 0.7        # seconds between scrolls
CLICK_RETRY_LIMIT = 12    # how many times to click "View More" before giving up
REQUEST_DELAY = 0.25      # seconds delay between page navigations per worker

# Common CSS/text selectors for "view more" type buttons
VIEW_MORE_SELECTORS = [
    "text=View More",
    "text=Show More",
    "text=More",
    "text=Load more",
    "text=See more",
    "button[aria-label*='more' i]",
    "button[aria-label*='load' i]",
    ".view-more",
    ".load-more",
    ".show-more",
    ".btn-more",
    ".btn-load",
    "a.load-more",
    "[data-action='load-more']",
    "[data-load-more]",
    "[data-more]",
]

# Sitemap limits
MAX_URLS_PER_SITEMAP = 50000

# Additional seeds for broader discovery
SEED_URLS = [
    START_URL,
    "https://www.finploy.com/browse-jobs",
    "https://www.finploy.com/jobs",
    "https://www.finploy.com/locations",
    "https://www.finploy.com/companies",
]

# ---------- Frontier (In-Memory, No DB) ----------


class MemoryFrontier:
    def __init__(self):
        self.lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._enqueued: Set[str] = set()
        self._seen: Dict[str, Dict[str, Any]] = {}
        self._seen_order: List[str] = []

    @classmethod
    async def create(cls):
        return cls()

    async def enqueue_if_new(self, url: str):
        async with self.lock:
            if url in self._enqueued or url in self._seen:
                return
            self._enqueued.add(url)
            self._queue.put_nowait(url)

    async def dequeue(self):
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def mark_seen(self, url: str, status_code: Optional[int] = None, last_modified: Optional[str] = None):
        async with self.lock:
            if url not in self._seen:
                self._seen_order.append(url)
            self._seen[url] = {
                "status_code": status_code,
                "last_modified": last_modified,
                "crawled_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

    async def is_seen(self, url: str) -> bool:
        return url in self._seen

    async def seen_count(self) -> int:
        return len(self._seen)

    async def queue_count(self) -> int:
        return self._queue.qsize()

    async def fetch_all_seen(self):
        rows = []
        for url in self._seen_order:
            meta = self._seen.get(url, {})
            rows.append((url, meta.get("last_modified"), meta.get("crawled_at")))
        return rows

    async def close_async(self):
        return None

# ---------- URL helpers ----------
def normalize_url(url):
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    # drop fragments
    fragment_free = parsed._replace(fragment="")
    # remove utm_* query params and session ids heuristically
    qs = [(k, v) for (k, v) in parse_qsl(fragment_free.query, keep_blank_values=True)
          if not (k.startswith("utm_") or k.lower() in ("sessionid", "sid", "phpsessid"))]
    qs.sort()
    cleaned = fragment_free._replace(query=urlencode(qs, doseq=True))
    return urlunparse(cleaned)

def is_allowed(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host in ALLOWED_HOSTS:
            return True
    except:
        return False
    return False

# ---------- Crawler ----------
async def render_and_extract(page, url):
    """
    Navigate, scroll, click "view more" buttons, and return set of discovered URLs (normalized).
    """
    discovered = set()
    nav_response = None
    try:
        nav_response = await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
    except PWTimeout:
        # try again but continue
        print("Timeout loading", url)
    except Exception as e:
        print("Navigation error:", e, url)
        return discovered

    # optional: check canonical link and prefer it
    try:
        canonical = await page.eval_on_selector("link[rel=canonical]", "el => el.href", strict=False)
        if canonical:
            cn = normalize_url(canonical)
            if cn:
                discovered.add(cn)
    except Exception:
        pass

    # Scroll to bottom repeatedly to trigger lazy-load
    try:
        previous_height = None
        for _ in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(SCROLL_PAUSE)
            cur_height = await page.evaluate("document.body.scrollHeight")
            if cur_height == previous_height:
                break
            previous_height = cur_height
    except Exception:
        pass

    # Click "View More" / "Load More" buttons until they disappear or up to a limit
    for selector in VIEW_MORE_SELECTORS:
        for attempt in range(CLICK_RETRY_LIMIT):
            try:
                el = await page.query_selector(selector)
                if not el:
                    break
                if not await el.is_visible():
                    break
                try:
                    await el.click(timeout=5000)
                except Exception:
                    await page.evaluate("(el) => el.click()", el)
                await asyncio.sleep(0.8)
            except Exception:
                break

    # Extract anchors (absolute hrefs)
    try:
        hrefs = await page.eval_on_selector_all("a[href]", "nodes => nodes.map(n => n.href)")
        for h in hrefs:
            n = normalize_url(h)
            if n and is_allowed(n):
                discovered.add(n)
    except Exception as e:
        print("Anchor extraction failed:", e)

    # Extract from data-* attributes commonly used for lazy links
    try:
        data_links = await page.eval_on_selector_all(
            "[data-url],[data-href],[data-link],[data-target-url]",
            "nodes => nodes.map(n => n.getAttribute('data-url') || n.getAttribute('data-href') || n.getAttribute('data-link') || n.getAttribute('data-target-url')).filter(Boolean)"
        )
        for h in data_links:
            full = h if h.startswith("http") else urljoin(url, h)
            n = normalize_url(full)
            if n and is_allowed(n):
                discovered.add(n)
    except Exception:
        pass

    # Extract URLs from onclick handlers
    try:
        onclicks = await page.eval_on_selector_all("[onclick]", "nodes => nodes.map(n => n.getAttribute('onclick'))")
        for script in onclicks:
            if not script:
                continue
            for m in re.findall(r"https?://[^\s'\"]+", script):
                n = normalize_url(m)
                if n and is_allowed(n):
                    discovered.add(n)
            for m in re.findall(r"['\"](/[^'\"]+)['\"]", script):
                full = urljoin(url, m)
                n = normalize_url(full)
                if n and is_allowed(n):
                    discovered.add(n)
    except Exception:
        pass

    # Extract potential endpoints from inline scripts
    try:
        scripts_text = await page.eval_on_selector_all("script", "nodes => nodes.map(n => n.innerText || '')")
        for s in scripts_text:
            if not s:
                continue
            for m in re.findall(r"['\"](/(?:api|ajax|data|jobs|search)[^'\"]+)['\"]", s):
                full = urljoin(url, m)
                n = normalize_url(full)
                if n and is_allowed(n):
                    discovered.add(n)
    except Exception:
        pass

    # Heuristic pagination expansion: if we saw ?page=N, add next few pages
    try:
        page_links = [h for h in list(discovered) if "page=" in h]
        for pl in page_links:
            parsed = urlparse(pl)
            qs = parse_qsl(parsed.query, keep_blank_values=True)
            new_qs = dict(qs)
            if "page" in new_qs and new_qs["page"].isdigit():
                base_num = int(new_qs["page"])
                for inc in range(1, 6):
                    new_qs["page"] = str(base_num + inc)
                    new_url = urlunparse(parsed._replace(query=urlencode(new_qs)))
                    n = normalize_url(new_url)
                    if n and is_allowed(n):
                        discovered.add(n)
    except Exception:
        pass

    return discovered, nav_response

async def worker(name, frontier, browser):
    page = await browser.new_page()
    while True:
        # stop condition
        seen_count = await frontier.seen_count()
        if seen_count >= MAX_PAGES:
            print(f"[{name}] reached max pages {seen_count}. exiting.")
            break

        url = await frontier.dequeue()
        if url is None:
            # nothing to do; small sleep and check again
            await asyncio.sleep(1)
            # re-check if queue is empty and no progress, then break after small idle
            qcount = await frontier.queue_count()
            if qcount == 0:
                print(f"[{name}] queue empty, exiting.")
                break
            continue

        # double-check not seen
        if await frontier.is_seen(url):
            continue

        print(f"[{name}] crawling: {url}")
        discovered, nav_response = await render_and_extract(page, url)
        # mark seen with metadata
        status_code = None
        last_modified = None
        try:
            if nav_response is not None:
                status_code = nav_response.status
                try:
                    # headers() returns a dict of response headers
                    headers = await nav_response.headers()
                    last_modified = headers.get("last-modified")
                except Exception:
                    pass
        except Exception:
            pass
        await frontier.mark_seen(url, status_code=status_code, last_modified=last_modified)
        # enqueue discovered
        for u in discovered:
            if not await frontier.is_seen(u):
                await frontier.enqueue_if_new(u)
        await asyncio.sleep(REQUEST_DELAY)
    await page.close()

# ---------- Sitemap writer ----------
async def write_single_sitemap_from_frontier(frontier, outpath=OUTPUT_DIR / "sitemap.xml"):
    rows = await frontier.fetch_all_seen()
    if not rows:
        print("No URLs found to write.")
        return
    outpath.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    root = Element('urlset', xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for row in rows:
        u = row[0]
        last_modified = row[1] if len(row) > 1 else None
        crawled_at = row[2] if len(row) > 2 else None
        url_el = SubElement(root, 'url')
        loc = SubElement(url_el, 'loc'); loc.text = u
        lm_value = last_modified or (crawled_at if crawled_at else now)
        lastmod = SubElement(url_el, 'lastmod'); lastmod.text = str(lm_value)
    with open(outpath, "wb") as f:
        f.write(tostring(root, encoding="utf-8", xml_declaration=True))
    print(f"Wrote {len(rows)} urls to {outpath}")


# ---------- Main ----------
async def main():
    # In-memory frontier (no database)
    frontier = await MemoryFrontier.create()
    print("Using in-memory frontier (no DB)")
    # seed if queue empty
    qcount = await frontier.queue_count()
    scount = await frontier.seen_count()
    if qcount == 0 and scount == 0:
        # enqueue multiple seeds for breadth-first coverage
        for s in SEED_URLS:
            seed_norm = normalize_url(s)
            if seed_norm:
                await frontier.enqueue_if_new(seed_norm)
        print("Seeded with", ", ".join(SEED_URLS))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # create concurrent workers
        tasks = [asyncio.create_task(worker(f"w{i+1}", frontier, browser)) for i in range(CONCURRENT_PAGES)]
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("Interrupted by user, shutting down...")
        finally:
            await browser.close()
            await frontier.close_async()

    # write a single sitemap when done (from active backend)
    await write_single_sitemap_from_frontier(frontier, OUTPUT_DIR / "sitemap.xml")

if __name__ == "__main__":
    asyncio.run(main())
