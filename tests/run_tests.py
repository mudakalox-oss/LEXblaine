"""Прогон тестовых вопросов по поиску LEX Blaine.
Запуск из корня проекта:
  python tests/run_tests.py                                  (основной набор)
  python tests/run_tests.py tests/test_questions_holdout.json (свежий набор для честной проверки)
Показывает, в какой доле вопросов правильная статья оказалась 1-й / в топ-3 / в топ-5, и список промахов."""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app as A

test_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "test_questions.json"
tests = json.loads(test_file.read_text(encoding="utf-8"))
chunks = A.all_chunks(A.load_db())
A.search_chunks("прогрев", chunks, 1)
hits = {1: 0, 3: 0, 5: 0}
misses = []
started = time.perf_counter()
for case in tests:
    results, _ = A.search_chunks(case["question"], chunks, limit=5)
    got = [f"{r.get('code')} {r.get('article')}" for r in results]
    rank = next((i + 1 for i, g in enumerate(got) if g in case["expected"]), None)
    for k in hits:
        hits[k] += bool(rank and rank <= k)
    if not rank or rank > 3:
        misses.append((case["question"], case["expected"], got[:3]))
n = len(tests)
print(f"Вопросов: {n} | время {time.perf_counter() - started:.1f} с")
print(f"Верная статья 1-я: {hits[1]}/{n} ({100*hits[1]//n}%) | в топ-3: {hits[3]}/{n} ({100*hits[3]//n}%) | в топ-5: {hits[5]}/{n} ({100*hits[5]//n}%)")
for question, expected, got in misses:
    print(f"\n✗ {question}\n   ожидалось: {expected}\n   выдало:    {got}")
