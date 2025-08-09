## Finploy Playwright Crawler

A fast, headless Playwright crawler that discovers Finploy URLs and writes a standards-compliant XML sitemap.

- In-memory frontier (no database)
- Automatic scroll and "View more" button clicking
- URL normalization and domain allowlist
- Concurrent workers with gentle rate limiting
- Output: `output_sitemaps/sitemap.xml`

### Requirements
- Python 3.8+
- Windows/macOS/Linux

### Setup
1) Create a virtual environment and install dependencies
   ```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2) Install the Playwright browser (Chromium)
   ```bash
python -m playwright install chromium
```

### Run
```bash
python finploy_crawler.py
```
The crawler stores its frontier in memory and writes the final sitemap to `output_sitemaps/sitemap.xml` when finished.

### Configuration
Edit `finploy_crawler.py` to adjust behavior:
- `START_URL`: Seed homepage
- `ALLOWED_HOSTS`: Allowed hostnames set
- `CONCURRENT_PAGES`: Number of concurrent Playwright pages
- `MAX_PAGES`: Crawl cap for unique pages
- `NAV_TIMEOUT`: Navigation timeout (ms)
- `SCROLL_PAUSE`: Delay between scrolls (s)
- `CLICK_RETRY_LIMIT`: Attempts to click "view more" style buttons
- `REQUEST_DELAY`: Delay between navigations per worker (s)
- `VIEW_MORE_SELECTORS`: CSS/text selectors to expand content

### How it works
- Navigates pages headlessly via Playwright
- Auto-scrolls and clicks common "view more" patterns
- Extracts anchors, data-* links, onclick/script URLs, and paginated variants
- Normalizes and filters URLs to allowed hosts
- Maintains an in-memory queue and seen set
- Writes a single XML sitemap

### Technical Report

#### Tools and languages used
- **Language**: Python 3.8+
- **Runtime/Automation**: Playwright (`playwright.async_api`)
- **Concurrency**: `asyncio`
- **Parsing & utilities**: `urllib.parse`, `re`, `pathlib`, `datetime`
- **Sitemap generation**: `xml.etree.ElementTree`
- Note: `requirements.txt` contains some legacy/optional deps (e.g., Selenium, BeautifulSoup) that are not required for this Playwright-based crawler.

#### Approach
- **Frontier (no DB)**: In-memory `asyncio.Queue` plus a `seen` map with crawl metadata; guarded by an `asyncio.Lock`.
- **Concurrency**: `CONCURRENT_PAGES` Playwright pages, each running a worker loop with gentle `REQUEST_DELAY`.
- **Navigation**: `page.goto(..., wait_until="networkidle")` with `NAV_TIMEOUT` and basic exception handling.
- **Dynamic discovery**:
  - Repeated auto-scroll until page height stabilizes
  - Click common "View/Load more" selectors up to `CLICK_RETRY_LIMIT`
  - Extract links from anchors, common `data-*` attributes, `onclick` handlers, and inline scripts
- **Pagination heuristic**: When encountering `?page=N`, proactively enqueue the next few pages (N+1..N+5).
- **URL hygiene**: Normalize URLs (drop fragments, sort query, strip `utm_*` and common session params) and filter by `ALLOWED_HOSTS`.
- **Stop conditions**: Exit when the queue drains or `MAX_PAGES` is reached.
- **Output**: Generate a standards-compliant `urlset` sitemap with `lastmod` derived from HTTP `Last-Modified` header when available, otherwise `crawled_at`.

#### Challenges and resolutions
- **Dynamic/Lazy content**: Many pages require scrolling/clicking to reveal links.
  - Resolution: Iterative scroll plus robust selector list and retries for "view more" actions.
- **Timeouts/Fragile navigation**: Dynamic sites can intermittently stall.
  - Resolution: Conservative `NAV_TIMEOUT`, try/catch around navigation and extraction, continue on failure.
- **Duplicate/Noisy URLs**: Tracking params and session IDs inflate the frontier.
  - Resolution: URL normalization removes `utm_*` and common session params; queries sorted; fragments dropped.
- **Pagination discovery**: Next pages are not always linked visibly.
  - Resolution: Heuristic expansion for `?page=` patterns to uncover adjacent pages.
- **Politeness vs. throughput**: Balancing speed with server friendliness.
  - Resolution: Tunable `CONCURRENT_PAGES` and `REQUEST_DELAY`; domain allowlist prevents off-site crawl.
- **Sitemap correctness**: Ensuring valid XML and meaningful `lastmod`.
  - Resolution: Use `xml.etree.ElementTree`; prefer HTTP `Last-Modified` header with fallback to crawl timestamp.

### Output
- `output_sitemaps/sitemap.xml`: UTF-8 XML compliant with sitemaps.org

### Troubleshooting
- If Playwright browsers are missing: `python -m playwright install chromium`
- If no URLs appear: verify `ALLOWED_HOSTS` and seeds in `SEED_URLS`
- Windows path issues: run from project root in PowerShell

### Notes
- No database is used. Crawl state is ephemeral per run.
- To persist progress across runs, add a simple file-based checkpoint (can be added on request).

### License
MIT
