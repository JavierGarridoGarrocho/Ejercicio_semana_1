import json
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.marca.com/"
MAX_NEWS = 5
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
REQUEST_TIMEOUT = 15


def clean_text(value):
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def normalize_date(value):
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{10}", text):
        return datetime.fromtimestamp(int(text), tz=UTC).isoformat()
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(int(text) / 1000, tz=UTC).isoformat()
    return text


def fetch_html(url):
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def looks_like_article_url(url):
    parsed = urlparse(url)
    if parsed.netloc and "marca.com" not in parsed.netloc:
        return False
    path = parsed.path or ""
    if not path.endswith(".html"):
        return False
    if "#ancla_comentarios" in url:
        return False
    parts = [p for p in path.split("/") if p]
    if len(parts) < 5:
        return False
    year = parts[-4]
    month = parts[-3]
    day = parts[-2]
    return bool(
        re.fullmatch(r"\d{4}", year)
        and re.fullmatch(r"\d{2}", month)
        and re.fullmatch(r"\d{2}", day)
    )


def get_candidate_links(homepage_html):
    soup = BeautifulSoup(homepage_html, "html.parser")
    urls = []
    seen = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        full_url = urljoin(BASE_URL, href)
        full_url = full_url.split("#", 1)[0]
        full_url = full_url.split("?", 1)[0]

        if full_url in seen:
            continue
        if not looks_like_article_url(full_url):
            continue

        seen.add(full_url)
        urls.append(full_url)
        if len(urls) >= 50:
            # Keep a buffer so we can skip broken pages.
            break
    return urls


def extract_title(soup):
    h1 = soup.select_one("h1")
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))
        if title:
            return title

    og_title = soup.select_one('meta[property="og:title"]')
    if og_title:
        return clean_text(og_title.get("content"))
    return None


def collect_json_ld_objects(soup):
    objects = []
    for node in soup.select('script[type="application/ld+json"]'):
        raw = node.string or node.get_text()
        raw = clean_text(raw)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, list):
            objects.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            graph_items = parsed.get("@graph")
            if isinstance(graph_items, list):
                objects.extend(item for item in graph_items if isinstance(item, dict))
            objects.append(parsed)
    return objects


def extract_author_from_jsonld(soup):
    for obj in collect_json_ld_objects(soup):
        author = obj.get("author")
        if isinstance(author, dict):
            name = clean_text(author.get("name"))
            if name:
                return name
        elif isinstance(author, list):
            names = []
            for item in author:
                if isinstance(item, dict):
                    name = clean_text(item.get("name"))
                    if name:
                        names.append(name)
                elif isinstance(item, str):
                    text = clean_text(item)
                    if text:
                        names.append(text)
            if names:
                return " / ".join(names)
        elif isinstance(author, str):
            text = clean_text(author)
            if text:
                return text
    return None


def extract_author(soup):
    byline_selectors = [
        '[class*="autor"]',
        '[class*="author"]',
        '[class*="firma"]',
        '[class*="byline"]',
    ]
    for selector in byline_selectors:
        for node in soup.select(selector):
            text = clean_text(node.get_text(" ", strip=True))
            if not text:
                continue
            if "Redacción" in text or "Redaccion" in text:
                return text
            if text.lower().startswith("por "):
                return text

    # Fallback: search whole page for "Redacción: <name>"
    full_text = clean_text(soup.get_text(" ", strip=True)) or ""
    match = re.search(
        r"(Redacci[oó]n:\s*[A-ZÁÉÍÓÚÜÑ0-9][A-ZÁÉÍÓÚÜÑ0-9\.\-\s/&']{1,120})",
        full_text,
    )
    if match:
        return clean_text(match.group(1))
    return extract_author_from_jsonld(soup)


def extract_date(soup):
    for time_tag in soup.select("time"):
        datetime_value = normalize_date(time_tag.get("datetime"))
        if datetime_value:
            return datetime_value
        text_value = normalize_date(time_tag.get_text(" ", strip=True))
        if text_value:
            return text_value

    meta_selectors = [
        'meta[property="article:published_time"]',
        'meta[name="date"]',
        'meta[itemprop="datePublished"]',
        'meta[property="og:updated_time"]',
    ]
    for selector in meta_selectors:
        node = soup.select_one(selector)
        if node:
            value = normalize_date(node.get("content"))
            if value:
                return value

    for obj in collect_json_ld_objects(soup):
        date_value = normalize_date(obj.get("datePublished")) or normalize_date(obj.get("dateModified"))
        if date_value:
            return date_value
    return None


def extract_article_data(article_url):
    try:
        html = fetch_html(article_url)
    except requests.RequestException:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup)
    author = extract_author(soup)
    date = extract_date(soup)

    if not title:
        return None

    return {
        "title": title,
        "author": author,
        "date": date,
        "url": article_url,
    }


def scrape_marca_news(limit=MAX_NEWS):
    homepage_html = fetch_html(BASE_URL)
    candidates = get_candidate_links(homepage_html)

    items = []
    for article_url in candidates:
        article = extract_article_data(article_url)
        if not article:
            continue
        items.append(article)
        if len(items) >= limit:
            break
    return items


def main():
    try:
        news = scrape_marca_news(MAX_NEWS)
    except requests.RequestException as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from error

    print(json.dumps(news, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
