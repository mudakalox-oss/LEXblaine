from __future__ import annotations

import json
import math
import os
import re
from difflib import get_close_matches
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "laws.json"
DRIVER_FILE = ROOT / "driver.json"

app = FastAPI(title="LEX Blaine — Legal Assistant", version="0.8")
templates = Jinja2Templates(directory=str(ROOT / "templates"))

# ==========================================
# 1. ДРАЙВЕР ПОВЕДЕНИЯ И ПРИОРИТЕТЫ
# ==========================================

DEFAULT_DRIVER = {
    "version": 1,
    "article_resolution": {"bare_article_number_preference": ["УК", "АК", "УАК", "ПК"]},
    "intent_rules": [],
    "answer_policy": {"concise": True, "no_invention": True}
}

def load_driver() -> dict[str, Any]:
    try:
        if DRIVER_FILE.exists():
            payload = json.loads(DRIVER_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                merged = dict(DEFAULT_DRIVER)
                merged.update(payload)
                return merged
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_DRIVER


# ==========================================
# 2. НОРМАЛИЗАЦИЯ И ПОЛНЫЕ СЛОВАРИ СЛЕНГА (В ИСХОДНОМ ОБЪЕМЕ)
# ==========================================

def norm(text: str) -> str:
    value = str(text or "").lower().replace("ё", "е")
    lookalikes = str.maketrans({
        "a": "а", "c": "с", "e": "е", "o": "о", "p": "р",
        "x": "х", "y": "у", "k": "к", "m": "м", "t": "т",
        "b": "в", "h": "н"
    })
    words = []
    for token in re.findall(r"[a-zа-яё0-9]+", value):
        if re.search(r"[а-яё]", token) and re.search(r"[a-z]", token):
            token = token.translate(lookalikes)
        words.append(token)
    return " ".join(words).strip()


STOP = set("""
и в во на по к ко с со от до для о об обо а но или ли же это как что где
когда можно нельзя бы при без под над из за у не да мне меня мой моя
этот эта эти такой такая такое ли же быть есть был была были будет
""".split())

# ПОЛНЫЙ ИСХОДНЫЙ СЛОВАРЬ АЛИАСОВ
ALIASES = {
    "уволили": "увольнение", "уволить": "увольнение",
    "увольняют": "увольнение", "уволен": "увольнение",
    "уволена": "увольнение", "увольнения": "увольнение",
    "работодатель": "работа", "работодателя": "работа",
    "сотрудника": "сотрудник", "сотрудники": "сотрудник",
    "оштрафовали": "штраф", "оштрафован": "штраф",
    "штрафуют": "штраф", "штрафа": "штраф", "штрафом": "штраф",
    "наказание": "ответственность", "наказания": "ответственность",
    "санкция": "ответственность", "санкции": "ответственность",
    "пдд": "дорожный", "дтп": "авария",
    "автомобиль": "транспорт", "машина": "транспорт",
    "машину": "транспорт", "машины": "транспорт",
    "водитель": "вождение", "водителя": "вождение",
    "парковка": "стоянка", "припарковался": "стоянка",
    "задержали": "задержание", "задержать": "задержание",
    "задержан": "задержание", "задержана": "задержание",
    "кафф": "задержание", "каффа": "задержание", "каффом": "задержание",
    "каффнуть": "задержание", "каффнул": "задержание",
    "каф": "задержание", "кафнуть": "задержание",
    "кафнул": "задержание", "кафнули": "задержание",
    "каффнула": "задержание", "каффают": "задержание",
    "закаффать": "задержание", "закаффал": "задержание",
    "закаффали": "задержание", "закaффить": "задержание",
    "арестовали": "арест", "арест": "арест", "кпз": "арест",
    "увал": "увольнение", "увала": "увольнение", "увалу": "увольнение",
    "выг": "выговор", "выгоро": "выговор", "выговор": "выговор",
    "заявку": "заявление", "анактив": "неактив", "овнер": "руководитель", "лидер": "руководитель",
    "директор": "руководитель", "зам": "заместитель", "удо": "удостоверение",
    "доки": "доказательство", "бодикамера": "доказательство", "собес": "собеседование",
    "лспдшник": "сотрудник полиции", "лспд": "сотрудник полиции",
    "фиб": "сотрудник полиции", "фибовец": "сотрудник полиции",
    "шериф": "сотрудник полиции", "гов": "сотрудник полиции", "юссс": "сотрудник полиции",
    "обыскали": "обыск", "судят": "суд",
    "лицензия": "разрешение", "лицензию": "разрешение",
    "лицензии": "разрешение", "разрешения": "разрешение",
    "полномочия": "полномочие", "документы": "документ",
    "броня": "бронежилет", "бронью": "бронежилет",
    "броник": "бронежилет", "бронника": "бронежилет",
}

# ПОЛНЫЕ ИСХОДНЫЕ ГРУППЫ СИНОНИМОВ
SYNONYM_GROUPS = {
    "оскорбление": "оскорбил оскорблять оскорбление оскорбления унизил унижение хамство грубость обматерил мат ругань неуважение",
    "госник": "гос сотрудник госник госникa государственный сотрудник сотрудник госструктуры госслужащий госслужба",
    "задержание": "задержать задержали задержан задержание задержания кафф каффнуть каффнул каффнули каффают закаффать закаффал закаффали кафнуть кафнул арестовать арест арестовали схватить заковать наручники надеть наручники доставить доставление",
    "основание": "основания причина причины повод законно правомерно разрешено допускается право полномочие полномочия",
    "увольнение": "уволить уволили уволен уволена увольнение увольняют увольняться увольнения снять снять с должности отстранить отстранение исключить из фракции выгнать исключение расторгнуть контракт расторжение трудовой договор",
    "выговор": "выговор выг выгоро предупреждение взыскание дисциплинарное наказание замечание строгий выговор дисциплина нарушение",
    "руководитель": "начальник лидер овнер директор руководитель глава организации",
    "заместитель": "заместитель зам директора зам овнера зам лидера замруководителя",
    "неактив": "неактив анактив ан актив отсутствие на службе неявка пропуск смены",
    "сотрудник полиции": "лспдшник лспд фиб фибовец шериф гов юссс полицейский полиция сотрудник полиции",
    "удостоверение": "удо удостоверение удостоверение сотрудника служебное удостоверение документы",
    "собеседование": "собес собеседование набор приём на службу",
    "доказательство": "доки боди камера бодикамера видеозапись нагрудная камера доказательство доказательства",
    "оружие": "оружие ствол пистолет автомат нелегальное оружие незаконный оборот оружия оружие в инвентаре",
    "применение оружия": "применение оружия использование оружия стрельба выстрел оружие нападение",
    "заявление": "заявление заявка на увал заявление на увольнение рапорт прошение об увольнении",
    "повышение": "повысить повышение повышение в должности назначение назначить ранг должность повышение ранга",
    "понижение": "понизить понижение понижение в должности снижение ранга снять с должности",
    "зарплата": "зарплата заработная плата оплата труда выплата оклад деньги начисление премия премирование невыплата",
    "отпуск": "отпуск отгул выходной больничный отдых перерыв обед",
    "суд": "суд судебный заседание слушание разбирательство иск истец ответчик дело судья судебный процесс вердикт решение приговор обжалование апелляция",
    "иск": "иск заявление жалоба подать в суд истец ответчик судебное обращение исковое заявление",
    "прокуратура": "прокурор прокуратура генпрок генеральный прокурор надзор проверка жалоба обращение",
    "обыск": "обыск обыскать досмотр осмотр проверить вещи проверка имущества изъятие конфискация",
    "штраф": "штраф оштрафовать оштрафовали денежное взыскание сумма оплата наказание",
    "арест": "арест арестовать арестовали заключение кпз тюрьма заключенный лишение свободы срок",
    "применение силы": "сила физическая сила спецсредства наручники задержание сопротивление неподчинение",
    "неподчинение": "неподчинение отказ подчиняться неповиновение не выполнил требование игнорирование законное требование",
    "взятка": "взятка коррупция подкуп взяточничество незаконное вознаграждение",
    "клевета": "клевета оклеветал ложные сведения распространение лжи порочащие сведения",
    "угроза": "угроза угрожать запугивание угрожающее высказывание",
    "нападение": "нападение напасть агрессия удар избить побои причинение вреда",
    "кража": "кража украсть похитить хищение присвоение ограбление",
    "транспорт": "машина автомобиль авто транспорт тачка тс транспортное средство",
    "дтп": "дтп авария столкновение наезд дорожное происшествие",
    "пдд": "пдд дорожный правила движения нарушение скорость светофор парковка стоянка",
    "лицензия": "лицензия лицензии лицензирование разрешение допуск удостоверение",
    "документ": "документы документ паспорт удостоверение лицензия справка предъявить показать",
    "права": "права конституционные права свободы гражданин неприкосновенность право",
    "обычный гражданин": "гражданский гражданин житель человек лицо гражданское лицо",
    "сотрудник": "сотрудник работник служащий офицер должностное лицо представитель",
    "устав": "устав внутренний распорядок правила организации регламент положение инструкция",
    "трудовой кодекс": "тк трудовой кодекс трудовое законодательство трудовые отношения работодатель работник служба фракция",
    "испытательный срок": "стажировка испытательный срок испытание стажер стажёр непрохождение",
    "прогул": "прогул отсутствие неявка опоздание рабочее время смена",
    "премия": "премия премирование бонус выплата поощрение премиальные",
    "восстановление": "восстановить восстановление вернуть должность незаконное увольнение обжаловать увольнение",
    "жалоба": "жалоба обращение заявление рапорт сообщить обжаловать оспорить",
    "рапорт": "рапорт заявление доклад служебная записка обращение",
    "ордер": "ордер санкция разрешение судебное разрешение постановление",
    "неприкосновенность": "неприкосновенность иммунитет защищенность статус неприкосновенный",
    "госструктура": "госструктура государственная структура госорган государственный орган ведомство организация фракция",
    "медицинская помощь": "медик врач больница лечение медицинская помощь скорая",
    "радио": "рация радиоканал связь эфир переговоры канал",
    "маскировка": "маска лицо скрыть личность маскировка",
    "чрезвычайная ситуация": "чп чрезвычайная ситуация происшествие вызов тревога",
}

for _canonical, _terms in SYNONYM_GROUPS.items():
    for _term in _terms.split():
        ALIASES.setdefault(_term, _canonical)
    ALIASES.setdefault(_canonical, _canonical)

def tokens(text: str) -> list[str]:
    out: list[str] = []
    for word in norm(text).split():
        if len(word) <= 1 or word in STOP:
            continue
        mapped = ALIASES.get(word, word)
        for part in norm(mapped).split():
            if len(part) > 1 and part not in STOP:
                out.append(part)
    return out


# ==========================================
# 3. ТОЧНЫЙ АНАЛИЗАТОР НОМЕРОВ СТАТЕЙ
# ==========================================

def extract_article_details(question: str) -> dict[str, Any]:
    q = norm(question)
    m = re.search(r"(?<!\d)(\d{1,2})[.\s](\d{1,2})(?!\d)", q)
    article_number = ""
    if m:
        article_number = f"{m.group(1)}.{m.group(2)}"
    else:
        m_single = re.search(r"(?:статья|ст\.?)\s*(\d+)", q)
        if m_single:
            article_number = m_single.group(1)
        else:
            m_alt = re.search(r"\b(\d{1,2})[.,](\d{1,2})\b", q)
            if m_alt:
                article_number = f"{m_alt.group(1)}.{m_alt.group(2)}"

    target_code = ""
    if re.search(r"\бук\b|уголовн", q):
        target_code = "ук"
    elif re.search(r"\бак\b|административн", q):
        target_code = "ак"
    elif re.search(r"\буак\b", q):
        target_code = "уак"
    elif re.search(r"\бпк\b|процессуальн", q):
        target_code = "пк"

    return {
        "article_number": article_number,
        "target_code": target_code
    }


# ==========================================
# 4. РАБОТА С БД И УМНЫЙ РАНЖИРОВЩИК
# ==========================================

def load_db() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        laws = payload.get("laws", []) if isinstance(payload, dict) else []
        return laws if isinstance(laws, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []

def split_law(law: dict[str, Any]) -> list[dict[str, Any]]:
    body = str(law.get("text", "") or "").replace("\r\n", "\n")
    if not body.strip():
        return []

    marker = r"(?=^[ \t]*(?:Статья\s+[0-9IVXА-Яа-я.-]+|Глава\s+[IVXА-Яа-я0-9.-]+))"
    pieces = re.split(marker, body, flags=re.I | re.M)
    pieces = [p.strip() for p in pieces if len(p.strip()) > 15]
    if not pieces:
        pieces = [body.strip()]

    result = []
    doc_title = str(law.get("title") or law.get("document") or "Документ")
    
    for piece in pieces:
        lines = [line.strip() for line in piece.splitlines() if line.strip()]
        head = lines[0][:220] if lines else doc_title
        
        art_match = re.search(r"(Статья\s+[0-9А-Яа-я.-]+)", head, re.I)
        if not art_match:
            art_match = re.search(r"(Статья\s+[0-9А-Яа-я.-]+)", piece[:150], re.I)
            
        article_name = art_match.group(1).strip() if art_match else "Общая норма"
        
        result.append({
            "title": doc_title,
            "document": doc_title,
            "article": article_name,
            "text": piece[:16000],
            "url": str(law.get("url") or ""),
        })
    return result

def all_chunks() -> list[dict[str, Any]]:
    return [chunk for law in load_db() for chunk in split_law(law)]

def rank(question: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    info = extract_article_details(question)
    target_art = info["article_number"]
    target_code = info["target_code"]
    q_tokens = set(tokens(question))
    
    scored = []
    for chunk in chunks:
        score = 0.0
        doc = norm(str(chunk.get("document", "")))
        art = norm(str(chunk.get("article", "")))
        text = norm(str(chunk.get("text", "")))
        
        if target_art:
            pattern = r"(?<!\d)" + re.escape(target_art) + r"(?!\d)"
            if re.search(pattern, art):
                score += 300.0
            elif re.search(pattern, text[:200]):
                score += 150.0
            elif re.search(pattern, text):
                score += 50.0
            else:
                score -= 100.0

        if target_code:
            if target_code in doc:
                score += 50.0
            else:
                score -= 30.0

        chunk_tokens = set(tokens(doc + " " + art + " " + text))
        overlap = len(q_tokens & chunk_tokens)
        score += overlap * 4.0
        
        item = dict(chunk)
        item["score"] = round(score, 3)
        item["coverage"] = float(overlap) / max(1, len(q_tokens))
        scored.append(item)

    scored.sort(key=lambda x: (x["score"], x["coverage"]), reverse=True)
    return scored


# ==========================================
# 5. ФОРМИРОВАНИЕ ОТВЕТА (UI LEX)
# ==========================================

def make_answer(question: str, sources: list[dict[str, Any]]) -> tuple[str, str]:
    if not sources:
        return (
            "• **Суть:** Норма права по вашему запросу не найдена в базе.\n"
            "• **Закон:** Не найден\n"
            "• **Детали:** Уточните номер статьи или формулировку запроса.",
            "Недостаточно данных"
        )

    best = sources[0]
    text = str(best.get("text") or "").strip()
    doc = str(best.get("document") or best.get("title") or "Документ")
    article = str(best.get("article") or "Статья").strip()
    
    details = re.sub(r"\s+", " ", text).strip()
    if len(details) > 280:
        details = details[:280].rsplit(" ", 1)[0] + "…"

    answer = (
        f"• **Суть:** Разъяснение нормы права по запросу.\n"
        f"• **Закон:** {article} ({doc})\n"
        f"• **Детали:** {details}"
    )
    return answer, "Найден источник"

def llm_answer(question: str, sources: list[dict[str, Any]]) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not sources:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        evidence = "\n\n".join(
            f"[ИСТОЧНИК {i+1}] {s.get('document')} / {s.get('article')}\nТЕКСТ:\n{s.get('text', '')[:2500]}"
            for i, s in enumerate(sources[:3])
        )
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            instructions=(
                "Ты LEX — юридический помощник GTA5RP Blaine. "
                "Отвечай строго в формате 3 строчек:\n"
                "• **Суть:** ...\n• **Закон:** ...\n• **Детали:** ...\n"
                "Опирайся только на предоставленные источники."
            ),
            input=f"Вопрос: {question}\n\nИсточники:\n{evidence}"
        )
        return response.output_text
    except Exception:
        return None


# ==========================================
# 6. API ENDPOINTS
# ==========================================

class AskRequest(BaseModel):
    question: str
    limit: int = 6

class ImportRequest(BaseModel):
    laws: list[dict[str, Any]]

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/status")
def status():
    return {
        "topic_count": len(load_db()),
        "chunk_count": len(all_chunks()),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "driver_loaded": DRIVER_FILE.exists(),
    }

@app.get("/api/laws")
def laws():
    return {
        "laws": [{
            k: law.get(k) for k in ("title", "document", "url", "revision_label", "extraction_chars", "has_text")
        } for law in load_db()]
    }

@app.post("/api/ask")
def ask(req: AskRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Введи вопрос.")

    chunks = all_chunks()
    if not chunks:
        return {
            "verdict": "База не загружена",
            "answer": "• **Суть:** База данных пуста.\n• **Закон:** Нет данных\n• **Детали:** Загрузите законы в data/laws.json.",
            "sources": [], "ai_used": False,
        }

    hits = rank(question, chunks)
    limit = max(1, min(req.limit, 6))
    hits = hits[:limit]

    sources = [{
        k: h.get(k) for k in ("title", "document", "article", "text", "url", "revision_label", "score", "coverage")
    } for h in hits]

    generated = llm_answer(question, sources)
    if generated:
        return {"verdict": "Ответ по источникам", "answer": generated, "sources": sources, "ai_used": True}

    answer, verdict = make_answer(question, sources)
    return {"verdict": verdict, "answer": answer, "sources": sources, "ai_used": False}

@app.post("/api/import")
def import_laws(req: ImportRequest):
    DATA_FILE.parent.mkdir(exist_ok=True)
    current = load_db()
    known = {x.get("url") for x in current if x.get("url")}
    added = 0
    for i, law in enumerate(req.laws):
        if not law.get("text"):
            continue
        law.setdefault("title", law.get("document", f"Импорт {i + 1}"))
        law.setdefault("document", law["title"])
        law.setdefault("url", "")
        if law.get("url") and law["url"] in known:
            continue
        current.append(law)
        added += 1
    DATA_FILE.write_text(
        json.dumps({"server": "Blaine", "laws": current}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return {"ok": True, "added": added, "topic_count": len(current)}

@app.post("/api/reload")
def reload_db():
    return {"ok": True, "topic_count": len(load_db()), "chunk_count": len(all_chunks())}
