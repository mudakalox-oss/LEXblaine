"""Проверка карточек знаний: все ли статьи из basis есть в базе и не изменился ли их смысл.
Запуск из корня проекта после каждого обновления laws.json:  python tests/run_knowledge_check.py"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A

raw = json.loads((ROOT / "data" / "knowledge.json").read_text(encoding="utf-8"))
chunks = A.all_chunks(A.load_db())

def find(ref):
    for chunk in chunks:
        if chunk.get("doc_id") != ref["doc"] or chunk.get("mention_only"):
            continue
        if ref.get("article") is not None and str(chunk.get("article") or "") == str(ref["article"]):
            return chunk
        if ref.get("heading") and ref["heading"].lower() in str(chunk.get("heading") or "").lower():
            return chunk
    return None

problems = 0
for card in raw["cards"]:
    missing = [ref for ref in card.get("basis", []) if not find(ref)]
    for ref in card.get("check", []):
        chunk = find(ref)
        if chunk is None or ref["contains"].lower() not in chunk["text"].lower():
            missing.append({**ref, "проблема": "нужной фразы нет в статье"})
    print(("✓" if not missing else "✗"), card["id"], "—", card["title"])
    for ref in missing:
        problems += 1
        print("    нет в базе или изменилось:", ref)
print("\nВсе карточки в порядке." if not problems else f"\nПроблем: {problems}. Обновите карточки.")
sys.exit(1 if problems else 0)
