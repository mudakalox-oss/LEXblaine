from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


# ============================================================
# CODEX — Blaine RP Legal Assistant
# ============================================================

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "laws.json"
DRIVER_FILE = DATA_DIR / "driver.json"

APP_VERSION = "1.1.0"

app = FastAPI(
    title="CODEX Blaine — Legal Assistant",
    version=APP_VERSION,
)

templates = Jinja2Templates(directory=str(ROOT / "templates"))

from fastapi import Request

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

# ============================================================
# НАСТРОЙКИ И КОДЕКСЫ
# ============================================================

DEFAULT_DRIVER: dict[str, Any] = {
    "version": 2,
    "article_resolution": {
        "bare_article_number_preference": ["УК", "АК", "УАК", "ПК"],
        "search_all_codes_when_unspecified": True,
        "exact_article_match_first": True,
        "never_substitute_another_article": True,
    },
    "answer_policy": {
        "concise": True,
        "no_invention": True,
        "always_include_source": True,
    },
}

CODE_ALIASES: dict[str, list[str]] = {
    "УК": [
        "ук", "ук рп", "уголовный кодекс", "уголовного кодекса",
        "уголовному кодексу", "уголовка",
    ],
    "АК": [
        "ак", "ак рп", "административный кодекс",
        "административного кодекса", "административному кодексу",
        "административка",
    ],
    "УАК": [
        "уак", "уак са", "уголовно-административный кодекс",
        "уголовно административный кодекс",
    ],
    "ПК": [
        "пк", "процессуальный кодекс", "процессуалка",
        "уголовно-процессуальный кодекс",
        "уголовно процессуальный кодекс",
    ],
}


# ============================================================
# РАСШИРЕННЫЙ СЛОВАРЬ
# Синонимы помогают искать документы, но не доказывают нарушение.
# ============================================================

ALIASES: dict[str, list[str]] = {
    "увольнение": [
        "увал", "увол", "увольняюсь", "увольняют", "уволили",
        "уволиться", "увольнение из фракции", "заявка на увал",
        "заявку на увал", "заявление на увольнение",
        "заявление об увольнении", "хочу уволиться",
        "уйти из фракции", "покинуть фракцию",
    ],
    "выговор": [
        "выг", "выгоро", "выговор", "предупреждение",
        "дисциплинарное взыскание", "получил выговор",
        "дали выговор", "впаяли выговор", "снятие выговора",
        "снять выговор", "обжаловать выговор",
    ],
    "неактив": [
        "анактив", "ан актив", "не актив", "неактивность",
        "отсутствие на службе", "неявка на службу",
        "не вышел на смену", "пропуск смены",
        "долго не заходил", "отсутствовал на службе",
        "прогул", "нет активности",
    ],
    "руководитель": [
        "овнер", "лидер", "начальник", "директор",
        "глава фракции", "руководитель организации",
        "главный во фракции", "руководство", "рук",
    ],
    "заместитель руководителя": [
        "зам", "заместитель", "зам директора",
        "зам овнера", "зам лидера", "заместитель лидера",
        "заместитель директора", "заместитель овнера",
        "замка", "зам главы",
    ],
    "приём во фракцию": [
        "набор", "приём", "прием", "зачисление",
        "вступить во фракцию", "вступление во фракцию",
        "попасть во фракцию", "трудоустройство",
    ],
    "собеседование": [
        "собес", "собеседование", "пройти собес",
        "провести собеседование", "приёмное собеседование",
        "набор во фракцию",
    ],
    "служебная дисциплина": [
        "дисциплина", "нарушение дисциплины",
        "нарушение субординации", "субординация",
        "служебное нарушение", "нарушение устава",
    ],
    "устав": [
        "устав фракции", "правила фракции",
        "внутренний устав", "внутренние правила",
        "должностные обязанности", "уставные правила",
    ],
    "задержание": [
        "кафф", "каффнул", "каффнули", "каффнуть",
        "заковали в наручники", "наручники", "задержали",
        "задержание", "меня задержали",
        "задержание гражданина", "задержание сотрудником",
        "забрали в участок", "повязали",
    ],
    "арест": [
        "арестовали", "арест", "посадили", "кпз",
        "камера предварительного заключения",
        "заключение под стражу", "заключили под стражу",
        "отправили в кпз", "посадка",
    ],
    "обыск": [
        "обыскали", "обыск", "досмотр", "досмотрели",
        "проверили вещи", "проверка вещей",
        "личный досмотр", "обыск гражданина",
        "обыск автомобиля", "обыск машины",
    ],
    "допрос": [
        "допросили", "допрос", "опрос", "опросили",
        "задают вопросы", "просят дать показания",
        "показания", "объяснения",
    ],
    "применение силы": [
        "применил силу", "применение силы", "избил",
        "ударил", "напал", "нападение",
        "физическое воздействие", "превышение силы",
        "превышение полномочий",
    ],
    "применение оружия": [
        "оружие", "стрельба", "выстрелил", "открыл огонь",
        "применил оружие", "стрелял", "угрожал оружием",
        "использовал оружие", "огнестрельное оружие",
    ],
    "неподчинение": [
        "не подчинился", "неподчинение", "неповиновение",
        "отказался выполнять требование", "не выполнил требование",
        "не послушался сотрудника", "не выполнил приказ",
        "отказ от выполнения приказа",
    ],
    "оскорбление": [
        "оскорбил", "оскорбление", "оскорблять",
        "обматерил", "материл", "нагрубил", "хамил",
        "грубость", "брань", "нецензурная брань",
        "оскорбление сотрудника", "оскорбление госслужащего",
        "обозвал сотрудника", "ругался с сотрудником",
    ],
    "угроза": [
        "угрожал", "угроза", "угрожать",
        "угроза сотруднику", "угроза расправой",
        "угрожал убийством", "угроза применения силы",
    ],
    "хулиганство": [
        "хулиган", "хулиганил", "дебош",
        "нарушение общественного порядка",
        "мешал окружающим", "агрессивное поведение",
    ],
    "провокация": [
        "провоцировал", "провокация",
        "провоцировал сотрудника", "провокационные действия",
    ],
    "документы": [
        "доки", "док", "документы", "бумаги",
        "показать документы", "проверка документов",
        "предъявить документы", "проверить документы",
    ],
    "удостоверение личности": [
        "удо", "удостоверение", "паспорт",
        "личные документы", "документ личности",
        "предъявить удостоверение", "показать паспорт",
    ],
    "доказательства": [
        "доки", "доказательства", "улики",
        "боди камера", "бодикамера", "боди-камера",
        "запись с боди-камеры", "видеозапись",
        "видео", "запись", "фотофиксация",
        "аудиозапись", "материалы дела",
    ],
    "видеонаблюдение": [
        "камера наблюдения", "камеры", "запись камер",
        "видеонаблюдение", "запись с камеры",
        "запись с камер наблюдения",
    ],
    "законность действий": [
        "законно ли", "законно", "незаконно",
        "имеет ли право", "имеют ли право",
        "правомерно ли", "это законно", "нарушил ли закон",
        "по закону ли", "может ли сотрудник", "разрешено ли",
        "имеет право сотрудник", "превысил полномочия",
    ],
    "жалоба": [
        "обжаловать", "подать жалобу", "жалоба",
        "оспорить действия", "написать жалобу",
        "подать заявление", "обращение",
        "жалоба на сотрудника", "жалоба на госслужащего",
    ],
    "ответственность": [
        "наказание", "санкция", "штраф", "срок",
        "срок заключения", "какое наказание",
        "что мне грозит", "что за это будет",
        "привлечь к ответственности",
        "уголовная ответственность",
        "административная ответственность",
    ],
    "суд": [
        "суд", "судебное разбирательство", "подать в суд",
        "иск", "судья", "судебное дело", "рассмотрение дела",
    ],
    "свидетель": [
        "свидетель", "очевидец", "свидетели",
        "показания свидетеля", "видел нарушение",
    ],
    "самооборона": [
        "самооборона", "защищался", "защита",
        "необходимая оборона", "защищал себя",
        "защищал другого человека",
    ],
    "приказ": [
        "приказ", "распоряжение", "приказ начальника",
        "приказ руководителя", "не выполнил приказ",
    ],
    "полномочия": [
        "полномочия", "служебные полномочия",
        "превышение полномочий", "злоупотребление полномочиями",
        "должностные полномочия",
    ],
    "служебное удостоверение": [
        "корочка", "служебные документы",
        "удостоверение сотрудника", "значок",
        "бейдж", "служебный жетон",
    ],
    "взятка": [
        "взятка", "дал взятку", "взял взятку",
        "подкуп", "коррупция", "вымогательство денег",
    ],
    "погоня": [
        "погоня", "преследование", "уход от полиции",
        "уход от сотрудников", "скрылся от полиции",
    ],
    "ориентировка": [
        "ориентировка", "объявили в розыск", "розыск",
        "находится в розыске", "разыскиваемый", "объявили розыск",
    ],
}

ORGANIZATIONS: dict[str, list[str]] = {
    "LSPD": [
        "лспд", "лспдшник", "лспдшники",
        "сотрудник лспд", "полицейский лспд",
        "департамент полиции лос-сантоса",
    ],
    "FIB": [
        "фиб", "фибовец", "фибовцы",
        "агент фиб", "сотрудник фиб",
    ],
    "GOV": [
        "gov", "гос", "госник", "госники",
        "госслужащий", "правительство", "сотрудник правительства",
    ],
    "USSS": [
        "usss", "юссс", "секретная служба",
        "агент usss", "сотрудник usss",
    ],
    "SHERIFF": [
        "шериф", "департамент шерифа",
        "сотрудник шерифа", "офис шерифа",
    ],
}


# ============================================================
# ЗАГРУЗКА DRIVER И БАЗЫ ЗАКОНОВ
# ============================================================

def load_driver() -> dict[str, Any]:
    if not DRIVER_FILE.exists():
        return DEFAULT_DRIVER.copy()

    try:
        with DRIVER_FILE.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        if not isinstance(payload, dict):
            return DEFAULT_DRIVER.copy()

        result = DEFAULT_DRIVER.copy()
        result.update(payload)
        return result
    except (OSError, json.JSONDecodeError):
        return DEFAULT_DRIVER.copy()


def load_db() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        return []

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        laws = payload.get("laws", [])
        if isinstance(laws, list):
            return [item for item in laws if isinstance(item, dict)]

    return []


def save_db(laws: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DATA_FILE.with_suffix(".json.tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(
            {"laws": laws},
            file,
            ensure_ascii=False,
            indent=2,
        )

    temporary.replace(DATA_FILE)


# ============================================================
# НОРМАЛИЗАЦИЯ И РАСШИРЕНИЕ ЗАПРОСА
# ============================================================

LATIN_LOOKALIKES = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о",
    "p": "р", "x": "х", "y": "у", "k": "к",
    "m": "м", "t": "т", "b": "в", "h": "н",
})


def norm(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text).strip()

    def fix_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if re.search(r"[а-я]", token) and re.search(r"[a-z]", token):
            return token.translate(LATIN_LOOKALIKES)
        return token

    return re.sub(r"[a-zа-я0-9]+", fix_token, text)


def expand_query(question: str) -> str:
    text = norm(question)
    additions: list[str] = []

    for canonical, variants in ALIASES.items():
        if any(norm(alias) in text for alias in variants):
            additions.append(canonical)

    for canonical, variants in ORGANIZATIONS.items():
        if any(norm(alias) in text for alias in variants):
            additions.append(canonical)

    # Не интерпретируем одиночную букву «н» без контекста.
    if additions:
        text += " " + " ".join(additions)

    return text


def words(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-я0-9]+", norm(value))
        if len(token) > 1
    }


# ============================================================
# РАСПОЗНАВАНИЕ КОДЕКСА И НОМЕРА СТАТЬИ
# ============================================================

def detect_code(question: str) -> str | None:
    text = norm(question)

    # Сначала проверяем наиболее специфичные обозначения.
    for code in ("УАК", "УК", "АК", "ПК"):
        aliases = sorted(CODE_ALIASES[code], key=len, reverse=True)
        for alias in aliases:
            pattern = r"(?<!\w)" + re.escape(norm(alias)) + r"(?!\w)"
            if re.search(pattern, text):
                return code

    return None


def extract_article(question: str) -> dict[str, Any] | None:
    text = norm(question)
    code = detect_code(text)

    # Например: 12.8, статья 12.8, ст. 12.8.
    dotted = re.search(
        r"(?<!\d)(\d{1,3})\s*\.\s*(\d{1,3})(?!\d)",
        text,
    )

    if dotted:
        return {
            "article": dotted.group(1) + "." + dotted.group(2),
            "part": None,
            "code": code,
            "explicit": True,
        }

    # Например: статья 12, ст. 12, статья 12 часть 2.
    simple = re.search(
        r"\b(?:статья|ст\.?)\s*(\d{1,3})\b",
        text,
    )

    if simple:
        part_match = re.search(
            r"\b(?:часть|ч\.?)\s*(\d{1,2})\b",
            text,
        )

        return {
            "article": simple.group(1),
            "part": part_match.group(1) if part_match else None,
            "code": code,
            "explicit": True,
        }

    return None


# ============================================================
# РАЗБИЕНИЕ ДОКУМЕНТОВ НА СТАТЬИ
# ============================================================

ARTICLE_HEADING_RE = re.compile(
    r"(?im)^\s*(?:статья|ст\.?)\s*"
    r"(\d{1,3}(?:\.\d{1,3})?)"
    r"(?:\s*[-—:.)]\s*|\s+)?([^\n]{0,180})"
)


def first_value(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def law_text(item: dict[str, Any]) -> str:
    return first_value(
        item,
        ["text", "content", "body", "description", "full_text"],
    )


def law_title(item: dict[str, Any]) -> str:
    return first_value(
        item,
        ["title", "name", "document", "law", "code", "category"],
    ) or "Документ без названия"


def law_url(item: dict[str, Any]) -> str:
    return first_value(item, ["url", "link", "source", "href"])


def infer_code(item: dict[str, Any]) -> str | None:
    metadata = " ".join(
        str(item.get(key, ""))
        for key in ("title", "name", "document", "law", "code", "category")
    )
    return detect_code(metadata)


def split_law(item: dict[str, Any]) -> list[dict[str, Any]]:
    text = law_text(item)
    if not text:
        return []

    title = law_title(item)
    url = law_url(item)
    code = infer_code(item)
    matches = list(ARTICLE_HEADING_RE.finditer(text))

    if not matches:
        return [{
            "title": title,
            "document": title,
            "code": code,
            "article": None,
            "heading": title,
            "text": text,
            "url": url,
        }]

    chunks: list[dict[str, Any]] = []

    preamble = text[:matches[0].start()].strip()
    if len(preamble) > 80:
        chunks.append({
            "title": title,
            "document": title,
            "code": code,
            "article": None,
            "heading": "Общие положения",
            "text": preamble,
            "url": url,
        })

    for index, match in enumerate(matches):
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )

        body = text[start:end].strip()
        article_number = match.group(1)
        heading_suffix = match.group(2).strip(" —-:.)")

        chunks.append({
            "title": title,
            "document": title,
            "code": code,
            "article": article_number,
            "heading": (
                f"Статья {article_number}"
                + (f". {heading_suffix}" if heading_suffix else "")
            ),
            "text": body,
            "url": url,
        })

    return chunks


def all_chunks(laws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for law in laws:
        result.extend(split_law(law))
    return result


# ============================================================
# ПОИСК
# Точное совпадение номера статьи важнее совпадения слов.
# ============================================================

def contains_article_number(
    chunk: dict[str, Any],
    number: str,
) -> bool:
    return str(chunk.get("article") or "").strip() == number


def chunk_score(
    question: str,
    chunk: dict[str, Any],
    article_request: dict[str, Any] | None,
) -> float:
    score = 0.0

    query_words = words(expand_query(question))
    heading = str(chunk.get("heading", ""))
    title = str(chunk.get("title", ""))
    body = str(chunk.get("text", ""))

    all_text = norm(f"{heading} {title} {body}")
    text_words = words(all_text)
    heading_words = words(heading)

    overlap = query_words & text_words
    if query_words:
        score += 35 * len(overlap) / len(query_words)

    if query_words:
        score += (
            20 * len(query_words & heading_words) / len(query_words)
        )

    if article_request:
        requested_number = str(article_request["article"])
        requested_code = article_request.get("code")

        if contains_article_number(chunk, requested_number):
            score += 1000

            if requested_code:
                if chunk.get("code") == requested_code:
                    score += 500
                else:
                    score -= 900

            requested_part = article_request.get("part")
            if requested_part:
                part_pattern = re.compile(
                    r"\b(?:часть|ч\.?)\s*"
                    + re.escape(str(requested_part))
                    + r"\b",
                    re.I,
                )
                if part_pattern.search(body):
                    score += 100
        else:
            score -= 200

    return score


def search_chunks(
    question: str,
    chunks: list[dict[str, Any]],
    limit: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    article_request = extract_article(question)
    candidates = chunks

    if article_request and article_request.get("code"):
        target_code = article_request["code"]
        candidates = [
            chunk for chunk in chunks
            if chunk.get("code") == target_code
        ]

    ranked: list[dict[str, Any]] = []

    for chunk in candidates:
        item = dict(chunk)
        item["score"] = chunk_score(question, item, article_request)
        ranked.append(item)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    
    if article_request:
        requested_number = str(article_request["article"])
        exact_matches = [
            item for item in ranked
            if contains_article_number(item, requested_number)
            and (
                not article_request.get("code")
                or item.get("code") == article_request["code"]
            )
        ]
        if exact_matches:
            return exact_matches[:limit], article_request
        return [], article_request

    return ranked[:limit], article_request


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/status")
async def api_status():
    laws = load_db()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "laws_count": len(laws),
    }


@app.get("/api/laws")
async def api_laws():
    return {"laws": load_db(), "count": len(load_db())}


@app.post("/api/ask")
async def api_ask(payload: AskRequest):
    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=400,
            detail="Введите вопрос."
        )

    laws = load_db()
    if not laws:
        raise HTTPException(
            status_code=503,
            detail="База законов пуста или не загружена."
        )

    chunks = all_chunks(laws)
    results, article_request = search_chunks(question, chunks)

    if not results:
        if article_request:
            answer = (
                f"Не удалось найти статью {article_request['article']}"
                + (
                    f" кодекса {article_request['code']}"
                    if article_request.get("code")
                    else ""
                )
                + " в загруженной базе. Проверьте номер статьи и кодекс."
            )
        else:
            answer = (
                "Подходящий фрагмент в базе не найден. "
                "Попробуйте уточнить вопрос."
            )

        return {
            "answer": answer,
            "sources": [],
            "question": question,
        }

    best = results[0]
    answer = str(best.get("text", "")).strip()

    sources = [
        {
            "title": str(item.get("heading") or item.get("title") or "Источник"),
            "document": str(item.get("document") or item.get("title") or ""),
            "article": item.get("article"),
            "url": str(item.get("url") or ""),
            "text": str(item.get("text") or ""),
        }
        for item in results
    ]

    return {
        "answer": answer or "Текст найденного источника пуст.",
        "sources": sources,
        "question": question,
    }


@app.post("/api/reload")
async def api_reload():
    return {
        "status": "ok",
        "laws_count": len(load_db()),
        "message": "База перечитана с диска."
            }
    
