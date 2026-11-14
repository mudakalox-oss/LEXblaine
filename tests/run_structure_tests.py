"""Простые вопросы по уставам и законам структур: попала ли нужная глава в топ-1 / топ-3.
Запуск из корня проекта:  python tests/run_structure_tests.py
Ожидание записано как «doc_id|часть заголовка»; засчитывается любое из перечисленных."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A

tests = json.loads((Path(__file__).parent / "test_structure_questions.json").read_text(encoding="utf-8"))
chunks = A.all_chunks(A.load_db())
structures = A.load_structures()
top1 = top3 = 0
for case in tests:
    st = structures[case["structure"]]
    allowed = frozenset(st["docs"])
    boost = A.structure_boost_docs(case["question"], st)
    results, _ = A.search_chunks(case["question"], chunks, limit=3, allowed_docs=allowed, boost_docs=boost)
    def hit(r):
        return any(r.get("doc_id") == e.split("|")[0] and e.split("|")[1].lower() in str(r.get("heading", "")).lower()
                   for e in case["expected"])
    rank = next((i + 1 for i, r in enumerate(results) if hit(r)), None)
    top1 += rank == 1
    top3 += bool(rank)
    print(("✓" if rank == 1 else "~" if rank else "✗"), f"[{case['structure']}] {case['question']}",
          f"(место {rank})" if rank else "→ " + "; ".join(str(r.get('heading'))[:40] for r in results))
print(f"\nВерная глава 1-я: {top1}/{len(tests)} | в топ-3: {top3}/{len(tests)}")
