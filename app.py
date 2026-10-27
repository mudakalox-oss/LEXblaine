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
app = FastAPI(title="LEX Blaine — Legal Assistant", version="0.4")
templates = Jinja2Templates(directory=str(ROOT / "templates"))

DRIVER_FILE = ROOT / "driver.json"

DEFAULT_DRIVER = {
    "version": 1,
    "article_resolution": {"bare_article_number_preference": ["УК", "АК"]},
    "intent_rules": [],
    "answer_policy": {"concise": True, "no_invention": True}
}

def load_driver() -> dict[str, Any]:
    try:
        payload = json.loads(DRIVER_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            merged = dict(DEFAULT_DRIVER)
            merged.update(payload)
            return merged
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_DRIVER

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

ALIASES = {
    "уволили": "увольнение", "уволить": "увольнение", "увольняют": "увольнение", "уволен": "увольнение",
    "кафф": "задержание", "каффнуть": "задержание", "каффнул": "задержание", "каффают": "задержание",
    "увал": "увольнение", "выг": "выговор", "выговор": "выговор", "доки": "доказательство",
    "бодикамера": "доказательство", "лспд": "сотрудник полиции", "фиб": "сотрудник полиции",
}

SYNONYM_GROUPS = {
    "оскорбление": "оскорбил оскорблять оскорбление унизил унижение хамство грубость обматерил мат ругань неуважение",
    "задержание": "задержать задержали задержан задержание кафф каффнуть каффнул арестовать арест заковать наручники",
    "увольнение": "уволить уволили уволен увольнение отстранить исключить выговор",
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

def classify_question(question: str) -> dict[str, Any]:
    q = norm(question)
    m = re.search(r"(?<!\d)(\d{1,2})[.\s](\d{1,2})(?!\d)", q)
    if m:
        article_number = f"{m.group(1)}.{m.group(2)}"
    else:
        m_single = re.search(r"(?:статья|ст\.?)\s*(\d+)", q)
        article_number = m_single.group(1) if m_single else ""
        if not article_number:
            m_alt = re.search(r"\b(\d{1,2})[.,](\d{1,2})\b", q)
            if m_alt:
                article_number = f"{m_alt.group(1)}.{m_alt.group(2)}"

    explicit_pc = bool(re.search(r"\bпк\b|процессуальн|задержан|кафф", q))
    return {
        "article_number": article_number,
        "explicit_pc": explicit_pc
    }

def split_law(law: dict[str, Any]) -> list[dict[str, Any]]:
    body = str(law.get("text", "") or "").replace("\r\n", "\n")
    if not body.strip():
        return []

    marker = (
        r"(?=^[ \t]*(?:Статья\s+[0-9IVXА-Яа-я.-]+|"
        r"Глава\s+[IVXА-Яа-я0-9.-]+|Раздел\s+[IVXА-Яа-я0-9.-]+)"
        r")"
    )
    pieces = re.split(marker, body, flags=re.I | re.M)
    pieces = [p.strip() for p in pieces if len(p.strip()) > 20]
    if not pieces:
        pieces = [body.strip()]

    result = []
    for piece in pieces:
        lines = [line.strip() for line in piece.splitlines() if line.strip()]
        head = lines[0][:220] if lines else str(law.get("title", ""))
        article_match = re.match(
            r"^(Статья\s+[^\n:;.]+|Глава\s+[^\n:;.]+|Раздел\s+[^\n:;.]+)",
            head, re.I
        )
        result.append({
            "title": str(law.get("title") or law.get("document") or "Документ"),
            "document": str(law.get("document") or law.get("title") or "Документ"),
            "article": article_match.group(1).strip() if article_match else "",
            "text": piece[:16000],
            "url": str(law.get("url") or ""),
            "revision_label": str(law.get("revision_label") or ""),
        })
    return result

def all_chunks() -> list[dict[str, Any]]:
    return [chunk for law in load_db() for chunk in split_law(law)]

def rank(question: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    info = classify_question(question)
    target_article = info["article_number"]
    
    scored = []
    for chunk in chunks:
        score = 0.0
        doc = str(chunk.get("document", ""))
        art = str(chunk.get("article", ""))
        text = str(chunk.get("text", ""))
        
        heading_combined = norm(f"{doc} {art}")
        
        if target_article:
            if target_article in norm(art) or re.search(r"(?<!\d)" + re.escape(target_article) + r"(?!\d)", heading_combined):
                score += 100.0
            else:
                score -= 50.0
        
        query_tokens = set(tokens(question))
        chunk_tokens = set(tokens(text + " " + heading_combined))
        overlap = len(query_tokens & chunk_tokens)
        score += overlap * 2.0
        
        item = dict(chunk)
        item["score"] = round(score, 3)
        item["coverage"] = float(overlap) / max(1, len(query_tokens))
        scored.append(item)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored

class AskRequest(BaseModel):
    question: str
    limit: int = 6

class ImportRequest(BaseModel):
    laws: list[dict[str, Any]]

def load_db() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        laws = payload.get("laws", []) if isinstance(payload, dict) else []
        return laws if isinstance(laws, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []

def make_answer(question: str, sources: list[dict[str, Any]]) -> tuple[str, str]:
    if not sources or sources[0].get("score", -99) < -10:
        return (
            "• **Суть:** Не удалось найти подходящую статью по вашему запросу.\n"
            "• **Закон:** Не найден\n"
            "• **Детали:** Уточните номер статьи или формулировку.",
            "Недостаточно данных"
        )

    best = sources[0]
    text = str(best.get("text") or "").strip()
    doc = str(best.get("document") or best.get("title") or "Документ")
    article = str(best.get("article") or "").strip()
    
    details = re.sub(r"\s+", " ", text).strip()
    if len(details) > 280:
        details = details[:280].rsplit(" ", 1)[0] + "…"

    answer = (
        f"• **Суть:** Норма права по вашему запросу.\n"
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
            f"[ИСТОЧНИК {i+1}] {s.get('document')} / {s.get('article') or s.get('title')}\nТЕКСТ:\n{s.get('text', '')[:3000]}"
            for i, s in enumerate(sources[:3])
        )
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            instructions=(
                "Ты LEX — юридический справочник GTA5RP Blaine. Отвечай кратко, строго в формате:\n"
                "• **Суть:** ...\n• **Закон:** ...\n• **Детали:** ...\n"
                "Не выдумывай статьи и не отклоняйся от источников."
            ),
            input=f"Вопрос: {question}\n\nИсточники:\n{evidence}"
        )
        return response.output_text
    except Exception:
        return None

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/status")
def status():
    laws = load_db()
    return {
        "topic_count": len(laws),
        "chunk_count": len(all_chunks()),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
        "driver_loaded": DRIVER_FILE.exists(),
    }

@app.get("/api/laws")
def laws():
    return {
        "laws": [{
            k: law.get(k) for k in (
                "title", "document", "url", "revision_label",
                "extraction_chars", "has_text"
            )
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
        k: h.get(k) for k in (
            "title", "document", "article", "text", "url",
            "revision_label", "score", "coverage"
        )
    } for h in hits]

    generated = llm_answer(question, sources)
    if generated:
        return {
            "verdict": "Ответ по источникам",
            "answer": generated, "sources": sources, "ai_used": True,
        }

    answer, verdict = make_answer(question, sources)
    return {
        "verdict": verdict,
        "answer": answer,
        "sources": sources,
        "ai_used": False,
    }

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
    
