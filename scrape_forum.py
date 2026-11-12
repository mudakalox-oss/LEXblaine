"""LEX Blaine: сборщик официальных законов сервера Blaine с форума.

Запуск (из корня проекта):  python scrape_forum.py

Что делает:
  1. Обходит ВСЕ страницы раздела «Законодательная база» и находит темы с законами.
  2. Для каждой темы обходит ВСЕ страницы и собирает ВСЕ сообщения автора темы
     (кодекс часто разбит на несколько сообщений). Комментарии игроков в текст закона не попадают.
  3. Сохраняет текст слово в слово: убирает только разметку форума и невидимые символы.
  4. Проверяет полноту: пропуски в нумерации статей, обрыв текста, текст короче прежнего.
  5. Если у документа несколько редакций, в data/laws.json кладёт только самую новую,
     остальные уходят в data/laws_all_editions.json.
  6. Пишет подробный отчёт data/scrape_report.json.

Существующий data/laws.json не затирается, если сбор явно сломался (см. SAFETY_MIN_RATIO).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

SCRAPER_VERSION = 2
BASE = "https://forum.gta5rp.com/forums/zakonodatel-naya-baza.2232/"
OUT = Path("data")
OUT.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "LEX-Blaine-RP-Legal-Research/0.2 (public-page reader)"}
REQUEST_DELAY = float(os.getenv("SCRAPE_DELAY_SECONDS", "0.8"))
MAX_THREAD_PAGES = 80          # защита от бесконечного обхода страниц одной темы
MAX_FORUM_PAGES = 40           # защита от бесконечного обхода списка тем
MAX_TEXT_CHARS = 1_500_000
KEEP_REPLIES = os.getenv("SCRAPE_KEEP_REPLIES", "0") == "1"   # комментарии игроков (по умолчанию не нужны)
SAFETY_MIN_RATIO = 0.7         # если тем стало меньше 70% от прежнего, laws.json не перезаписываем

session = requests.Session()
session.headers.update(HEADERS)


class BlockedError(RuntimeError):
    """Сайт отклонил запрос (защита от ботов, закрытая тема)."""


# ------------------------------------------------------------
# Сеть
# ------------------------------------------------------------

def get_soup(url: str) -> BeautifulSoup:
    last_error = "unknown"
    for attempt in range(4):
        try:
            response = session.get(url, timeout=(10, 30))
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            retry_after = response.headers.get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else 2 ** attempt * 2
            last_error = f"HTTP {response.status_code}"
            time.sleep(min(wait, 60))
            continue
        if response.status_code in (401, 403):
            raise BlockedError(f"HTTP {response.status_code}: сайт отклонил запрос ({url})")
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    raise RuntimeError(f"{last_error} ({url})")


def clean_url(url: str) -> str:
    url = urldefrag(url)[0]
    path = urlparse(url).path
    return url.rstrip("/") + ("/" if "/forums/" in path or "/threads/" in path else "")


def next_page_url(soup: BeautifulSoup, current: str) -> str | None:
    node = soup.select_one("a.pageNav-jump--next") or soup.select_one('link[rel="next"]')
    if node and node.get("href"):
        return clean_url(urljoin(current, node["href"]))
    return None


# ------------------------------------------------------------
# Поиск тем в разделе
# ------------------------------------------------------------

THREAD_PATH_RE = re.compile(r"/threads/[^/]+\.\d+/?$")
LAW_WORDS = ("редакц", "кодекс", "закон", "конституц", "устав", "прецедент")


def find_threads() -> tuple[dict[str, str], list[dict[str, str]], int]:
    threads: dict[str, str] = {}
    errors: list[dict[str, str]] = []
    visited: set[str] = set()
    url: str | None = clean_url(BASE)
    pages = 0
    while url and url not in visited and pages < MAX_FORUM_PAGES:
        visited.add(url)
        pages += 1
        try:
            soup = get_soup(url)
        except BlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": url, "error": str(exc)})
            break
        found_here = 0
        for item in soup.select(".structItem--thread"):
            for a in item.select(".structItem-title a[href*='/threads/']"):
                href = clean_url(urljoin(url, a.get("href", "")))
                title = " ".join(a.get_text(" ", strip=True).split())
                if THREAD_PATH_RE.search(urlparse(href).path) and len(title) > 5:
                    threads[href] = title
                    found_here += 1
                    break
        if not found_here:  # запасной вариант для нестандартной темы оформления
            for a in soup.select("a[href]"):
                href = clean_url(urljoin(url, a.get("href", "")))
                title = " ".join(a.get_text(" ", strip=True).split())
                if (
                    urlparse(href).netloc.endswith("gta5rp.com")
                    and THREAD_PATH_RE.search(urlparse(href).path)
                    and len(title) > 5
                    and any(word in title.lower() for word in LAW_WORDS)
                ):
                    threads[href] = title
        time.sleep(REQUEST_DELAY)
        url = next_page_url(soup, url)
    return threads, errors, pages


# ------------------------------------------------------------
# Извлечение текста
# ------------------------------------------------------------

BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote", "pre", "hr"}
INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def element_text(node: Tag) -> str:
    """Текст с сохранением строк: <br> и блочные теги дают перенос, строчные теги склеиваются."""
    parts: list[str] = []

    def walk(current) -> None:
        if isinstance(current, NavigableString):
            parts.append(re.sub(r"\s+", " ", str(current)))
            return
        if not isinstance(current, Tag):
            return
        name = current.name
        if name in ("script", "style", "noscript"):
            return
        classes = current.get("class") or []
        if "bbCodeBlock-expandLink" in classes or "bbCodeBlock-shrinkLink" in classes:
            return
        if name == "br":
            parts.append("\n")
            return
        block = name in BLOCK_TAGS
        if block:
            parts.append("\n")
        if name in ("td", "th"):
            parts.append(" ")
        for child in current.children:
            walk(child)
        if block:
            parts.append("\n")

    walk(node)
    return clean_text("".join(parts))


def clean_text(text: str) -> str:
    text = INVISIBLE_RE.sub("", text).replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_posts(soup: BeautifulSoup) -> list[dict[str, str]]:
    posts: list[dict[str, str]] = []
    for article in soup.select("article.message"):
        body = article.select_one(".message-body .bbWrapper") or article.select_one(".bbWrapper")
        if body is None:
            continue
        author = (article.get("data-author") or "").strip()
        if not author:
            user = article.select_one(".message-userDetails .username, a.username")
            author = user.get_text(strip=True) if user else ""
        time_tag = article.select_one("time[datetime]")
        posts.append({
            "id": (article.get("data-content") or article.get("id") or "").strip(),
            "author": author,
            "published": time_tag.get("datetime", "") if time_tag else "",
            "text": element_text(body),
        })
    return posts


def fetch_thread(url: str) -> tuple[BeautifulSoup, list[dict[str, str]], int]:
    """Возвращает первую страницу, все сообщения темы и число страниц."""
    first_soup: BeautifulSoup | None = None
    all_posts: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    visited: set[str] = set()
    page_url: str | None = url
    pages = 0
    while page_url and page_url not in visited and pages < MAX_THREAD_PAGES:
        visited.add(page_url)
        soup = get_soup(page_url)
        pages += 1
        if first_soup is None:
            first_soup = soup
        for post in parse_posts(soup):
            key = post["id"] or f"{post['author']}|{post['text'][:80]}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            all_posts.append(post)
        page_url = next_page_url(soup, page_url)
        if page_url:
            time.sleep(REQUEST_DELAY)
    assert first_soup is not None
    return first_soup, all_posts, pages


# ------------------------------------------------------------
# Редакции, названия, проверка полноты
# ------------------------------------------------------------

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
REVISION_RE = re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(20\d{2})")


def parse_revision(text: str) -> tuple[str, str]:
    """Возвращает (подпись редакции как на форуме, дата ISO)."""
    for match in REVISION_RE.finditer(text):
        month = MONTHS.get(match.group(2).lower())
        if month:
            iso = f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(1)):02d}"
            return f"{match.group(1)} {match.group(2)} {match.group(3)}", iso
    return "", ""


def document_key(title: str) -> str:
    text = re.sub(r"^\s*важно\s+", "", title, flags=re.I)
    text = re.sub(r"\(?\s*редакц\w*\s*(?:от)?[^)]*\)?\s*$", "", text, flags=re.I)
    text = REVISION_RE.sub("", text)
    text = re.sub(r"[\"'«»“”„‘’`().,:;-]", " ", text.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", text).strip()


def display_name(title: str) -> str:
    return re.sub(r"^\s*важно\s+", "", title, flags=re.I).strip()


HEAD_RE = re.compile(r"(?im)^\s*статья\s+(\d{1,3}(?:\.\d{1,3})?)\s*[.\-—:)]?\s+[А-ЯЁA-Z«\"“]")


def analyze_text(text: str) -> dict:
    numbers = HEAD_RE.findall(text)
    warnings: list[str] = []
    gaps: list[str] = []
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    chapters: dict[int, set[int]] = {}
    plain: set[int] = set()
    for number in numbers:
        if "." in number:
            chapter, item = number.split(".")
            chapters.setdefault(int(chapter), set()).add(int(item))
        else:
            plain.add(int(number))
    for chapter, items in sorted(chapters.items()):
        missing = sorted(set(range(1, max(items) + 1)) - items)
        gaps.extend(f"{chapter}.{m}" for m in missing)
    if plain:
        gaps.extend(str(m) for m in sorted(set(range(1, max(plain) + 1)) - plain))
    if gaps:
        warnings.append("Пропуски в нумерации статей: " + ", ".join(gaps[:30]) + (" …" if len(gaps) > 30 else ""))
    if duplicates:
        warnings.append("Повторяются номера статей (возможно, ссылки в начале строки): " + ", ".join(duplicates[:15]))
    tail = text.rstrip()[-1:]
    if tail and (tail.isalpha() or tail == ","):
        warnings.append("Текст, возможно, обрывается на середине предложения.")
    if len(text) > 3000 and not numbers:
        warnings.append("Заголовки статей не найдены: структура документа нестандартная.")
    return {"articles_found": len(numbers), "gaps": gaps, "duplicates": duplicates, "warnings": warnings}


# ------------------------------------------------------------
# Сбор одной темы
# ------------------------------------------------------------

def extract_topic(url: str, index_title: str) -> dict:
    soup, posts, pages = fetch_thread(url)
    h1 = soup.select_one("h1")
    title = " ".join((h1.get_text(" ", strip=True) if h1 else index_title).split()) or index_title

    if not posts:
        raise RuntimeError("в теме не найдено ни одного сообщения (возможно, изменилась вёрстка форума)")

    thread_author = posts[0]["author"]
    if thread_author:
        official = [p for p in posts if p["author"] == thread_author]
    else:
        official = posts[:1]
    others = [p for p in posts if p not in official]

    law_text = "\n\n".join(p["text"] for p in official if p["text"]).strip()[:MAX_TEXT_CHARS]
    replies = ""
    if KEEP_REPLIES and others:
        replies = "\n\n".join(p["text"] for p in others if p["text"])[:20000]

    revision_label, revision_iso = parse_revision(title)
    published = official[0]["published"] if official else ""
    analysis = analyze_text(law_text)
    thread_id = (re.search(r"\.(\d+)/?$", urlparse(url).path) or [None, ""])[1]

    return {
        "title": title,
        "document": display_name(title),
        "article": "",
        "text": law_text,
        "url": url,
        "thread_id": thread_id,
        "forum_published_at": published,
        "revision_label": revision_label,
        "revision_date": revision_iso,
        "is_current": True,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source_type": "forum_thread",
        "extraction_chars": len(law_text),
        "has_text": bool(law_text.strip()),
        "replies_text": replies,
        "scrape_info": {
            "thread_pages": pages,
            "posts_total": len(posts),
            "official_posts": len(official),
            "thread_author": thread_author,
            "articles_found": analysis["articles_found"],
            "warnings": analysis["warnings"],
        },
    }


# ------------------------------------------------------------
# Выбор актуальных редакций, запись
# ------------------------------------------------------------

def mark_current_editions(records: list[dict]) -> list[dict]:
    """Для каждого документа оставляет текущей самую новую редакцию. Возвращает список устаревших."""
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(document_key(record["title"]), []).append(record)
    superseded: list[dict] = []
    for items in groups.values():
        if len(items) < 2:
            continue
        dated = [r for r in items if r.get("revision_date")]
        if not dated:
            continue
        newest = max(r["revision_date"] for r in dated)
        for record in items:
            if record.get("revision_date") and record["revision_date"] < newest:
                record["is_current"] = False
                superseded.append(record)
    return superseded


def sha256_of(laws: list[dict]) -> str:
    return hashlib.sha256(json.dumps(laws, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_payload(laws: list[dict]) -> dict:
    return {
        "server": "Blaine",
        "category_url": BASE,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "topic_count": len(laws),
        "_meta": {
            "schema_version": 1,
            "scraper_version": SCRAPER_VERSION,
            "content_sha256": sha256_of(laws),
            "note": "Метаданные целостности; исходные документы и тексты законов не изменены.",
        },
        "laws": laws,
    }


def load_previous() -> dict[str, int]:
    path = OUT / "laws.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        laws = payload["laws"] if isinstance(payload, dict) else payload
        return {x["url"]: len(x.get("text", "")) for x in laws if isinstance(x, dict) and x.get("url")}
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    print("LEX scraper v%d: обход законодательной базы Blaine…" % SCRAPER_VERSION)
    try:
        threads, page_errors, forum_pages = find_threads()
    except BlockedError as exc:
        print(f"\nОШИБКА: {exc}\nФорум не отдал страницу. Откройте раздел в браузере, подождите и запустите снова.")
        return 2
    print(f"Страниц раздела: {forum_pages}. Найдено тем: {len(threads)}.")
    if not threads:
        print("Темы не найдены: проверьте адрес раздела и доступ к форуму. Файлы не изменены.")
        return 2

    previous = load_previous()
    records: list[dict] = []
    topic_errors: list[dict] = []
    for index, (url, title) in enumerate(threads.items(), 1):
        try:
            item = extract_topic(url, title)
            old_len = previous.get(url, 0)
            if old_len and item["extraction_chars"] < old_len * 0.7:
                item["scrape_info"]["warnings"].append(
                    f"Текст стал заметно короче прежнего ({old_len} -> {item['extraction_chars']} знаков)."
                )
            records.append(item)
            info = item["scrape_info"]
            flag = "  ⚠" if info["warnings"] else ""
            print(f"[{index}/{len(threads)}] {item['document'][:60]} — {item['extraction_chars']} зн., "
                  f"страниц {info['thread_pages']}, сообщений автора {info['official_posts']}, статей {info['articles_found']}{flag}")
        except BlockedError as exc:
            topic_errors.append({"url": url, "title": title, "error": str(exc)})
            print(f"[{index}/{len(threads)}] ОТКАЗ ДОСТУПА {title}: {exc}")
        except Exception as exc:  # noqa: BLE001
            topic_errors.append({"url": url, "title": title, "error": str(exc)})
            print(f"[{index}/{len(threads)}] ОШИБКА {title}: {exc}")
        time.sleep(REQUEST_DELAY)

    unique = {item["url"]: item for item in records}
    records = list(unique.values())
    superseded = mark_current_editions(records)
    current = [r for r in records if r["is_current"]]

    report = {
        "scraper_version": SCRAPER_VERSION,
        "forum_pages_checked": forum_pages,
        "candidate_topics_found": len(threads),
        "topics_saved": len(current),
        "superseded_editions": [{"title": r["title"], "url": r["url"], "revision_date": r["revision_date"]} for r in superseded],
        "empty_text_topics": [r["url"] for r in current if not r["has_text"]],
        "documents_with_warnings": [
            {"title": r["document"], "url": r["url"], "chars": r["extraction_chars"], "warnings": r["scrape_info"]["warnings"]}
            for r in current if r["scrape_info"]["warnings"]
        ],
        "page_errors": page_errors,
        "topic_errors": topic_errors,
        "notes": [
            "Автоматическая выгрузка, не подтверждение юридической полноты.",
            "Проверьте documents_with_warnings и topic_errors вручную.",
        ],
    }
    (OUT / "scrape_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    target = OUT / "laws.json"
    broken = not current or (previous and len(current) < len(previous) * SAFETY_MIN_RATIO)
    if broken:
        target = OUT / "laws.new.json"
        print(f"\n⚠ Тем собрано заметно меньше прежнего ({len(current)} против {len(previous)}). "
              f"Старый laws.json оставлен нетронутым, результат записан в {target}.")
    elif (OUT / "laws.json").exists():
        (OUT / "laws.previous.json").write_bytes((OUT / "laws.json").read_bytes())

    target.write_text(json.dumps(build_payload(current), ensure_ascii=False, indent=2), encoding="utf-8")
    if superseded:
        (OUT / "laws_all_editions.json").write_text(json.dumps(build_payload(records), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nСохранено документов: {len(current)} -> {target}")
    if superseded:
        print(f"Устаревших редакций исключено: {len(superseded)} (см. data/laws_all_editions.json)")
    if report["documents_with_warnings"]:
        print(f"Документов с предупреждениями: {len(report['documents_with_warnings'])}. Подробности: {OUT / 'scrape_report.json'}")
    if topic_errors:
        print(f"Ошибок по темам: {len(topic_errors)}. Подробности: {OUT / 'scrape_report.json'}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
