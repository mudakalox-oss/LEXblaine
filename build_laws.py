#!/usr/bin/env python3
"""
LEX Blaine — сборка data/laws.json из файлов законов data/docs/*.txt

Как пользоваться
1. Положите .txt файлы законов в папку data/docs/ (по одному файлу на закон).
2. Запустите:  python build_laws.py
3. Прочитайте отчёт в консоли: число статей, пропуски, как заканчивается текст.
4. Если всё в порядке — загрузите data/laws.json в репозиторий (вместе с app.py и
   data/structures.json).

Что делает скрипт
- Берёт название и тип документа из таблицы MANIFEST ниже (ключ = имя файла без .txt,
  регистр не важен). Файлы, которых нет в MANIFEST, берут название из строки
  «ЗАГОЛОВОК: …» в начале файла или из первой строки текста.
- Каждому документу даёт doc_id (= имя файла маленькими буквами). По doc_id
  data/structures.json решает, какие документы доступны выбранной структуре.
- Уставы без заголовков «Статья N» режет на разделы по главам и пунктам, чтобы поиск
  находил нужный пункт, а не весь устав целиком.
- Заменяет в текущем laws.json документы с тем же названием, остальные не трогает.
- Оригинальный текст не меняется: убираются только невидимые символы, имена
  картинок и служебные строки форума. Перед записью старый файл копируется в
  data/laws.backup.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("data")
DOCS = DATA / "docs"
LAWS = DATA / "laws.json"
BACKUP = DATA / "laws.backup.json"
STRUCTURES = DATA / "structures.json"
BASE_URL = "https://forum.gta5rp.com/forums/zakonodatel-naya-baza.2232/"
BUILDER_VERSION = "2"
SECTION_MAX_CHARS = 2300  # приложение передаёт ИИ не более 2500 знаков одного источника

# имя файла (без .txt, маленькими буквами) -> (название, тип)
MANIFEST: dict[str, tuple[str, str]] = {
    "yk": ("Уголовный кодекс штата Сан-Андреас", "law"),
    "koap": ("Кодекс об Административных правонарушениях штата Сан-Андреас", "law"),
    "pk": ("Процессуальный кодекс штата Сан-Андреас", "law"),
    "tk": ("Трудовой кодекс штата Сан-Андреас", "law"),
    "dk": ("Дорожный кодекс штата Сан-Андреас", "law"),
    "etn_k": ("Этический кодекс штата Сан-Андреас", "law"),
    "konstitysiya": ("Конституция штата Сан-Андреас", "law"),
    "zakon_oryjie": ("Закон «О регулировании оборота оружия, боеприпасов и спецсредств в штате Сан-Андреас»", "law"),
    "goss_taina": ("Закон «О государственной тайне в Штате Сан-Андреас»", "law"),
    "mitingi": ("Закон «О собраниях, митингах и публичных мероприятиях»", "law"),
    "zakon_neprikos": ("Закон «Об обеспечении неприкосновенности государственных служащих»", "law"),
    "zakon_o_fib": ("Закон «О Федеральном Расследовательском Бюро»", "law"),
    "zakon_o_lspd": ("Закон «О Полицейском Департаменте города Лос-Сантос»", "law"),
    "zakon_o_lssd": ("Закон «О Департаменте Шерифа округа Блейн»", "law"),
    "zakon_o_ng": ("Закон «О Национальной Гвардии штата Сан-Андреас»", "law"),
    "zakon_o_usss": ("Закон «О деятельности Секретной Службы Соединенных Штатов Америки в штате Сан-Андреас»", "law"),
    "ustav_fib": ("Устав FIB", "ustav"),
    "ustav_lspd": ("Внутренний устав Los Santos Police Department (LSPD)", "ustav"),
    "ustav_lssd": ("Устав Шерифского Департамента Los Santos (LSSD)", "ustav"),
    "ustav_sang": ("Внутренний устав San Andreas National Guard (SANG)", "ustav"),
}

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
REVISION_RE = re.compile(r"(\d{1,2})\s+([А-Яа-яЁё]+)\s+(20\d{2})")
MARKER_RE = re.compile(r"(?m)^#(\d+)[ \t]*$")
HEAD_RE = re.compile(r"(?im)^\s*статья\s+(\d{1,3}(?:\.\d{1,3}){0,2})\s*[.\-—:)]?(?:\s|$)")
CHAPTER_RE = re.compile(r"(?im)^\s*(?:глава|раздел)\s+[IVXLCDM\d]+\b[^\n]*$")
POINT_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){0,3})\.?\s+\S")
IMAGE_LINE_RE = re.compile(r"^\s*\S+\.(?:png|jpe?g|gif|webp|bmp)\s*$", re.I)
LABEL_LINES = {"ук са", "ук", "тк", "дк", "коап", "эк", "пк", "форум gta 5 rp"}


# ------------------------------------------------------------
# Названия
# ------------------------------------------------------------

def parse_revision(text: str) -> tuple[str, str]:
    for m in REVISION_RE.finditer(text):
        month = MONTHS.get(m.group(2).lower())
        if month:
            return f"{m.group(1)} {m.group(2)} {m.group(3)}", f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
    return "", ""


def document_key(title: str) -> str:
    t = re.sub(r"^\s*важно\s+", "", title, flags=re.I)
    t = re.sub(r"\(?\s*редакц\w*\s*(?:от)?[^)]*\)?\s*$", "", t, flags=re.I)
    t = REVISION_RE.sub("", t)
    t = re.sub(r"[\"'«»“”„‘’`().,:;-]", " ", t.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", t).strip()


def display_name(title: str) -> str:
    return re.sub(r"^\s*важно\s+", "", title, flags=re.I).strip()


# ------------------------------------------------------------
# Чтение и очистка
# ------------------------------------------------------------

def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def pop_meta(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = re.match(r"^\s*(ЗАГОЛОВОК|ССЫЛКА)\s*:\s*(.+?)\s*$", lines[i], flags=re.I)
        if m:
            meta[m.group(1).upper()] = m.group(2)
            i += 1
        elif not lines[i].strip() and meta:
            i += 1
        else:
            break
    return meta, "\n".join(lines[i:])


def find_title(head: str) -> str:
    for line in head.split("\n")[:20]:
        line = line.strip()
        if re.search(r"редакц", line, flags=re.I) and len(line) < 250 and "Автор темы" not in line:
            return line
    return ""


def clean_page(text: str) -> tuple[str, list[str], str]:
    """Если вставлена целая страница форума: убирает шапку, склеивает сообщения автора."""
    warnings: list[str] = []
    if "=== КОНЕЦ ===" in text:
        text = text.split("=== КОНЕЦ ===", 1)[0]
    markers = list(MARKER_RE.finditer(text))
    if not markers:
        return text.strip(), warnings, ""
    head = text[: markers[0].start()]
    title = find_title(head)
    author_m = re.search(r"Автор темы\s*(.+?)\s+Дата начала", head)
    author = author_m.group(1).strip() if author_m else ""
    posts: list[tuple[str, str]] = []
    cur_author = author
    for idx, m in enumerate(markers):
        end = markers[idx + 1].start() if idx + 1 < len(markers) else len(text)
        body = text[m.end():end]
        next_author = None
        if idx + 1 < len(markers):
            lines = body.split("\n")
            cut = None
            for j in range(len(lines) - 2, max(0, len(lines) - 12) - 1, -1):
                a, b = lines[j].strip(), lines[j + 1].strip()
                if a and a == b and len(a) < 60:
                    cut, next_author = j, a
            if cut is not None:
                body = "\n".join(lines[:cut])
        posts.append((cur_author, body))
        cur_author = next_author or author
    kept = [b.strip("\n") for a, b in posts if not author or a == author]
    if len(posts) > 1:
        warnings.append(f"Склеено сообщений автора: {len(kept)} из {len(posts)}.")
    return "\n\n".join(kept).strip(), warnings, title


def tidy(text: str) -> str:
    """Убирает невидимые символы, имена картинок и ярлыки в начале. Текст закона не меняется."""
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "").replace("\xa0", " ")
    lines = [ln.rstrip() for ln in text.split("\n")]
    lines = [ln for ln in lines if not IMAGE_LINE_RE.match(ln)]
    while lines and (not lines[0].strip() or lines[0].strip().lower() in LABEL_LINES):
        lines.pop(0)
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ------------------------------------------------------------
# Деление уставов на разделы
# ------------------------------------------------------------

def _pack_blocks(blocks: list[tuple[str, str]], limit: int) -> list[list[tuple[str, str]]]:
    packs: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    size = 0
    for num, body in blocks:
        if cur and size + len(body) > limit:
            packs.append(cur)
            cur, size = [], 0
        cur.append((num, body))
        size += len(body) + 1
    if cur:
        packs.append(cur)
    return packs


def _hard_split(body: str, limit: int) -> list[str]:
    parts, cur = [], ""
    for line in body.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        while len(line) > limit:  # очень длинная строка: режем по предложению
            cut = line.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 200 else limit
            parts.append((cur + "\n" + line[:cut]).strip("\n") if cur else line[:cut])
            cur, line = "", line[cut:].lstrip()
        cur = (cur + "\n" + line) if cur else line
    if cur:
        parts.append(cur)
    return parts


def split_sections(text: str) -> list[dict]:
    lines = text.split("\n")
    heads = [i for i, ln in enumerate(lines) if CHAPTER_RE.match(ln)]
    segments: list[tuple[str, list[str]]] = []
    first = heads[0] if heads else len(lines)
    pre = [ln for ln in lines[:first]]
    pre_text = "\n".join(pre).strip()
    if len(pre_text) > 120:
        name = next((ln.strip().rstrip(".") for ln in pre if ln.strip().lower().startswith("общие положения")), "Вводная часть")
        segments.append((name, pre))
    for n, start in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        segments.append((lines[start].strip().rstrip("."), lines[start + 1:end]))
    if not segments:
        segments.append(("Текст документа", lines))

    sections: list[dict] = []
    for heading, body_lines in segments:
        body = "\n".join(body_lines).strip()
        if len(body) < 60:  # запись из оглавления, не раздел
            continue
        blocks: list[tuple[str, str]] = []
        for ln in body_lines:
            m = POINT_RE.match(ln)
            if m or not blocks:
                blocks.append((m.group(1) if m else "", ln))
            else:
                blocks[-1] = (blocks[-1][0], blocks[-1][1] + "\n" + ln)
        packs = _pack_blocks(blocks, SECTION_MAX_CHARS)
        multi = len(packs) > 1
        for pack in packs:
            piece = "\n".join(b for _, b in pack).strip()
            if not piece:
                continue
            nums = [n for n, _ in pack if n]
            for part in _hard_split(piece, SECTION_MAX_CHARS):
                label = heading
                if multi and nums:
                    label += f" (п. {nums[0]}" + (f"–{nums[-1]}" if nums[-1] != nums[0] else "") + ")"
                elif multi:
                    label += " (продолжение)"
                sections.append({"heading": label, "text": f"{heading}\n{part.strip()}" if not part.lstrip().startswith(heading) else part.strip()})
    return sections


# ------------------------------------------------------------
# Проверка полноты
# ------------------------------------------------------------

def analyze(text: str) -> dict:
    numbers = HEAD_RE.findall(text)
    seen: set[str] = set()
    dups: list[str] = []
    for n in numbers:
        if n in seen and n not in dups:
            dups.append(n)
        seen.add(n)
    chapters: dict[int, set[int]] = {}
    plain: set[int] = set()
    dotted = 0
    for n in seen:
        parts = n.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            dotted += 1
            chapters.setdefault(int(parts[0]), set()).add(int(parts[1]))
        elif parts[0].isdigit():
            plain.add(int(parts[0]))
    gaps: list[str] = []
    if seen and dotted / len(seen) > 0.6:  # нумерация вида 5.1, 5.2 (как в УК)
        for c, s in sorted(chapters.items()):
            gaps += [f"{c}.{k}" for k in sorted(set(range(1, max(s) + 1)) - s)]
        known = sorted(chapters)
        gaps += [f"глава {c} целиком" for c in range(known[0], known[-1] + 1) if c not in chapters]
    elif plain:  # сквозная нумерация 1, 2, 3 (подстатьи вида 6.1 не считаются пропусками)
        gaps += [str(k) for k in sorted(set(range(1, max(plain) + 1)) - plain)]
    return {"articles": len(seen), "duplicates": dups, "gaps": gaps}


def looks_truncated(text: str) -> bool:
    """Подозрение на обрыв: последняя строка длинная и не заканчивается знаком конца."""
    last = text.rstrip().split("\n")[-1].strip()
    return len(last) > 150 and last[-1:] not in ".;:!?)»”\"$%…>"


# ------------------------------------------------------------
# Сборка
# ------------------------------------------------------------

def load_existing() -> tuple[list[dict], dict]:
    if not LAWS.exists():
        return [], {}
    payload = json.loads(LAWS.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], {}
    return [x for x in payload.get("laws", []) if isinstance(x, dict)], payload


def build_record(path: Path, old_by_key: dict[str, dict]) -> tuple[dict | None, list[str]]:
    notes: list[str] = []
    doc_id = path.stem.lower()
    meta, body = pop_meta(read_text(path))
    text, warns, page_title = clean_page(body)
    notes += warns
    text = tidy(text)
    if not text:
        return None, notes + ["Файл пустой после очистки — пропущен."]

    manifest = MANIFEST.get(doc_id)
    title = meta.get("ЗАГОЛОВОК") or (manifest[0] if manifest else "") or page_title
    kind = manifest[1] if manifest else ("ustav" if "устав" in path.stem.lower() else "law")
    if not title:
        title = next((ln.strip() for ln in text.split("\n") if ln.strip()), "") or path.stem
        notes.append(f"Название взято из первой строки: «{title[:70]}». Добавьте файл в MANIFEST или строку ЗАГОЛОВОК:.")

    old = old_by_key.get(document_key(title))
    label, iso = parse_revision(title)
    url = meta.get("ССЫЛКА") or (old or {}).get("url") or ""
    record = {
        "doc_id": doc_id,
        "doc_type": kind,
        "title": title,
        "document": display_name(title),
        "article": "",
        "text": text,
        "url": url,
        "forum_published_at": (old or {}).get("forum_published_at", ""),
        "revision_label": label or (old or {}).get("revision_label", ""),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source_type": "manual_file",
        "extraction_chars": len(text),
        "has_text": True,
        "replies_text": "",
        "source_file": path.name,
    }
    if iso:
        record["revision_date"] = iso
    info = analyze(text)
    if kind == "ustav" or info["articles"] == 0:
        sections = split_sections(text)
        if sections:
            record["sections"] = sections
            covered = sum(len(s["text"]) for s in sections)
            notes.append(f"Разбит на разделов: {len(sections)} (покрытие текста {covered * 100 // max(1, len(text))}%).")
    if old:
        old_len = int(old.get("extraction_chars") or len(old.get("text", "")))
        if old_len and len(text) < old_len * 0.7:
            notes.append(f"Текст короче прежнего ({old_len} → {len(text)} знаков). Проверьте, что файл полный.")
    return record, notes


def check_structures(records: list[dict]) -> None:
    if not STRUCTURES.exists():
        print("\n! Нет data/structures.json — выбор структур в приложении работать не будет.")
        return
    try:
        payload = json.loads(STRUCTURES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"\n! data/structures.json повреждён: {exc}")
        return
    present = {r.get("doc_id") for r in records if r.get("doc_id")}
    print("\nПроверка структур:")
    for item in payload.get("structures", []):
        docs = item.get("docs", [])
        missing = [d for d in docs if d not in present]
        status = "нет документов (пока не подключена)" if not docs else ("ОК" if not missing else "нет в базе: " + ", ".join(missing))
        print(f"  {item.get('name', item.get('id'))}: документов {len(docs)} — {status}")


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in DOCS.glob("*.txt") if not p.name.startswith("."))
    print(f"LEX build_laws v{BUILDER_VERSION}. Папка с файлами: {DOCS.resolve()}")
    if not files:
        print("В папке data/docs нет .txt файлов. Положите туда законы и запустите снова.")
        return 1

    existing, old_payload = load_existing()
    old_by_key = {document_key(x.get("title") or x.get("document") or ""): x for x in existing}
    print(f"Сейчас в laws.json документов: {len(existing)}")

    new_records: dict[str, dict] = {}
    report: list[dict] = []
    for path in files:
        record, notes = build_record(path, old_by_key)
        print(f"\n=== {path.name}")
        if record is None:
            for n in notes:
                print("  !", n)
            continue
        info = analyze(record["text"])
        if looks_truncated(record["text"]):
            notes.append("Текст обрывается без конца предложения. Файл, возможно, неполный.")
        if info["gaps"]:
            notes.append(f"Пропуски в нумерации статей: {', '.join(info['gaps'][:25])}{' …' if len(info['gaps']) > 25 else ''}")
        if info["duplicates"]:
            notes.append(f"Повторяются номера статей: {', '.join(info['duplicates'][:15])}")
        print(f"  Название: {record['document']}")
        print(f"  doc_id: {record['doc_id']} | знаков: {len(record['text'])} | статей: {info['articles']}")
        print(f"  Конец: …{record['text'][-80:].replace(chr(10), ' ')}")
        for n in notes:
            print("  !", n)
        key = document_key(record["title"])
        if key in new_records:
            print("  ! Документ с таким названием уже есть среди файлов — оставлен более длинный.")
            if len(record["text"]) <= len(new_records[key]["text"]):
                continue
        new_records[key] = record
        report.append({"file": path.name, "doc_id": record["doc_id"], "document": record["document"],
                       "chars": len(record["text"]), "articles": info["articles"], "gaps": info["gaps"],
                       "sections": len(record.get("sections", [])), "warnings": notes})

    if not new_records:
        print("\nНичего не собрано, laws.json не тронут.")
        return 1

    merged: list[dict] = []
    replaced = 0
    for item in existing:
        key = document_key(item.get("title") or item.get("document") or "")
        if key in new_records:
            merged.append(new_records.pop(key))
            replaced += 1
        else:
            merged.append(item)
    added = len(new_records)
    merged.extend(new_records.values())

    payload = {
        "server": old_payload.get("server", "Blaine"),
        "category_url": old_payload.get("category_url", BASE_URL),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "topic_count": len(merged),
        "_meta": {
            "schema_version": 2,
            "builder_version": BUILDER_VERSION,
            "content_sha256": hashlib.sha256(json.dumps(merged, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            "note": "Тексты законов из data/docs не изменены: убраны только невидимые символы, имена картинок и служебные строки.",
        },
        "laws": merged,
    }
    if LAWS.exists():
        BACKUP.write_bytes(LAWS.read_bytes())
    tmp = LAWS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LAWS)
    (DATA / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nГотово: заменено документов {replaced}, добавлено {added}, всего в базе {len(merged)}.")
    print(f"Файл: {LAWS.resolve()}")
    if BACKUP.exists():
        print(f"Старая версия сохранена: {BACKUP.name}")
    check_structures(merged)
    print("\nОтчёт: data/build_report.json. Проверьте строки со знаком «!» выше.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
