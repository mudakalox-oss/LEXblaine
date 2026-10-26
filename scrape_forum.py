from __future__ import annotations
import json, re, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup

BASE = "https://forum.gta5rp.com/forums/zakonodatel-naya-baza.2232/"
OUT = Path("data")
OUT.mkdir(exist_ok=True)
HEADERS = {
    "User-Agent": "LEX-Blaine-RP-Legal-Research/0.1 (public-page reader)"
}
session = requests.Session()
session.headers.update(HEADERS)

def clean_url(url: str) -> str:
    url = urldefrag(url)[0]
    return url.rstrip("/") + ("/" if "/forums/" in urlparse(url).path else "")

def get_soup(url: str):
    response = session.get(url, timeout=25)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")

def find_forum_pages():
    pages = {BASE}
    soup = get_soup(BASE)
    for a in soup.select("a[href]"):
        href = urljoin(BASE, a.get("href", ""))
        path = urlparse(href).path
        if "/forums/zakonodatel-naya-baza.2232/page-" in path:
            pages.add(clean_url(href))
    # XenForo page navigation can hide a page link; explicitly check page 2.
    pages.add("https://forum.gta5rp.com/forums/zakonodatel-naya-baza.2232/page-2/")
    return sorted(pages)

def is_thread_url(url: str) -> bool:
    p = urlparse(url)
    return p.netloc.endswith("gta5rp.com") and "/threads/" in p.path

def find_threads():
    thread_urls = {}
    page_errors = []
    for page_url in find_forum_pages():
        try:
            soup = get_soup(page_url)
            for a in soup.select("a[href]"):
                href = clean_url(urljoin(page_url, a.get("href", "")))
                title = " ".join(a.get_text(" ", strip=True).split())
                if is_thread_url(href) and title and len(title) > 5:
                    # Keep useful topic-title links; skip author/profile/post anchors.
                    if re.search(r"\.(\d+)/?$", urlparse(href).path) or "редакц" in title.lower() or "кодекс" in title.lower() or "закон" in title.lower() or "конституц" in title.lower() or "устав" in title.lower() or "прецедент" in title.lower():
                        thread_urls[href] = title
            time.sleep(0.7)
        except Exception as e:
            page_errors.append({"url": page_url, "error": str(e)})
    return thread_urls, page_errors

def extract_topic(url: str, index_title: str):
    soup = get_soup(url)
    h1 = soup.select_one("h1")
    title = " ".join((h1.get_text(" ", strip=True) if h1 else index_title).split())
    # XenForo commonly puts the post body in .message-body .bbWrapper.
    bodies = soup.select(".message-body .bbWrapper")
    if not bodies:
        bodies = soup.select(".bbWrapper")
    if not bodies:
        bodies = soup.select("article .message-body")
    body = "\n\n".join(" ".join(x.get_text("\n", strip=True).splitlines()) for x in bodies)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    # Keep only the first post as the primary law text; record other replies separately for review.
    first_body = ""
    if bodies:
        first_body = re.sub(r"\n{3,}", "\n\n", "\n".join(bodies[0].get_text("\n", strip=True).splitlines())).strip()
    if not first_body:
        first_body = body
    published = ""
    time_tag = soup.select_one("article time[datetime], .message time[datetime], time[datetime]")
    if time_tag:
        published = time_tag.get("datetime", "")
    # Extract a visible revision marker from title/body where present.
    revision = ""
    match = re.search(r"(?:редакц(?:ия|ии|ию)\s*(?:от)?\s*)?(\d{1,2}\s+[А-Яа-яЁё]+\s+20\d{2})", title, re.I)
    if match:
        revision = match.group(1)
    return {
        "title": title or index_title,
        "document": title or index_title,
        "article": "",
        "text": first_body[:250000],
        "url": url,
        "forum_published_at": published,
        "revision_label": revision,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source_type": "forum_thread",
        "extraction_chars": len(first_body),
        "has_text": bool(first_body.strip()),
        "replies_text": body[len(first_body):][:50000] if len(body) > len(first_body) else ""
    }

def main():
    print("LEX scraper: scanning Blaine legal base…")
    threads, errors = find_threads()
    print(f"Discovered {len(threads)} candidate topic links.")
    records, topic_errors = [], []
    for i, (url, title) in enumerate(threads.items(), 1):
        try:
            item = extract_topic(url, title)
            records.append(item)
            print(f"[{i}/{len(threads)}] {item['title']} — {item['extraction_chars']} chars")
        except Exception as e:
            topic_errors.append({"url": url, "title": title, "error": str(e)})
            print(f"[{i}/{len(threads)}] ERROR {title}: {e}")
        time.sleep(0.8)
    # Deduplicate exact URLs only. Different editions remain available for audit.
    unique = {}
    for item in records:
        unique[item["url"]] = item
    records = list(unique.values())
    data_file = OUT / "laws.json"
    data_file.write_text(json.dumps({"server": "Blaine", "category_url": BASE,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "topic_count": len(records), "laws": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "category_pages_checked": len(find_forum_pages()),
        "candidate_topics_found": len(threads),
        "topics_saved": len(records),
        "empty_text_topics": [x["url"] for x in records if not x["has_text"]],
        "page_errors": errors,
        "topic_errors": topic_errors,
        "notes": [
            "This is an automated extraction report, not a certification of legal completeness.",
            "Review empty_text_topics and errors manually.",
            "Compare revision labels and duplicate titles before relying on answers."
        ]
    }
    (OUT / "scrape_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {len(records)} topics to {data_file}")
    print(f"Review report: {OUT / 'scrape_report.json'}")

if __name__ == "__main__":
    main()
