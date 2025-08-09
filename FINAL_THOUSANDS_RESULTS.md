# Finploy Crawl Results

## Summary
- Crawler: Playwright (no DB, in-memory frontier)
- Output: `output_sitemaps/sitemap.xml`
- Latest run: wrote 1542 URLs

## What the crawler does
- Headless navigation with Playwright Chromium
- Auto-scrolls and clicks common "View more"/"Load more" patterns
- Extracts links from anchors, data-* attributes, onclick handlers, and inline scripts
- Heuristic pagination expansion for `?page=` parameters
- URL normalization (drops fragments, strips `utm_*` and session params)
- Domain allowlist to keep crawl focused on Finploy

## Run steps (recap)
```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
python finploy_crawler.py
```

## Output format
- A single XML sitemap at `output_sitemaps/sitemap.xml`
- Each entry contains `<loc>` and `<lastmod>`; `<lastmod>` uses page Last-Modified header or crawl time

## Notes
- No database files are created or needed
- Crawl state is not persisted after the run
- Adjust `ALLOWED_HOSTS`, `SEED_URLS`, and limits in `finploy_crawler.py` to refine scope
