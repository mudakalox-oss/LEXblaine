"""Проверка «логики» бота без обращения к Groq: вместо модели стоит заглушка, которая
отдаёт заранее заданный разбор вопроса (как это сделал бы аналитик запроса) и запоминает,
что именно было бы отправлено ИИ. Запуск из корня проекта:  python tests/run_logic_tests.py"""
import asyncio, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["GROQ_API_KEY"] = "test"
import app as A

class Req:
    class client:
        host = "test"

state = {"plans": {}, "calls": [], "answer_prompt": None}

def fake_groq(payload, api_key, timeout=None):
    system = payload["messages"][0]["content"]
    user = payload["messages"][1]["content"]
    if "аналитик запросов" in system:
        state["calls"].append("plan")
        for key, value in state["plans"].items():
            if key in user:
                return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)), None
        return None, 0
    state["calls"].append("answer")
    state["answer_prompt"] = user
    return "• Вывод: заглушка", None

A._groq_call = fake_groq

def ask(question, structure, history=None, planner="auto"):
    A.PLANNER_MODE = planner
    A._RATE_BUCKETS.clear()
    A._RESPONSE_CACHE.clear()
    state["calls"].clear()
    state["answer_prompt"] = None
    turns = [A.Turn(q=h[0], a=h[1]) for h in (history or [])]
    return asyncio.run(A.api_ask(A.AskRequest(question=question, structure=structure, history=turns), Req()))

def names(result):
    return [f"{s['document'][:24]} {s['article'] or s['title'][:30]}" for s in result["sources"]]

def has(result, doc_part, article=None, title_part=None):
    for s in result["sources"]:
        if doc_part.lower() in s["document"].lower() and (article is None or str(s["article"]) == article) \
                and (title_part is None or title_part.lower() in s["title"].lower()):
            return True
    return False

checks = []
def check(name, ok, detail=""):
    ok = bool(ok)
    checks.append(ok)
    print(("✓ " if ok else "✗ ") + name + ("" if ok else f"\n    {detail}"))

# 1. Полномочия: разбор вопроса выбирает карточку и документы
state["plans"] = {"работать по ДК": {
    "intent": "power", "standalone": "Может ли сотрудник FIB работать по Дорожному кодексу",
    "subject": "сотрудник FIB", "action": "применять Дорожный кодекс",
    "queries": ["административные правонарушения полномочия органов", "органы, уполномоченные назначать наказания"],
    "docs": ["koap", "dk", "zakon_o_fib"], "cards": ["admin_competence"]}}
r = ask("Может ли сотрудник FIB работать по ДК", "fib")
check("FIB и ДК: в выдаче КоАП ст. 19 и 20", has(r, "Административных", "19") and has(r, "Административных", "20"), names(r))
check("FIB и ДК: вводная часть ДК и права ФБР", has(r, "Дорожный", title_part="Общие положения") and has(r, "Федеральн"), names(r))
check("FIB и ДК: ИИ получил порядок рассуждения", state["answer_prompt"] and "Порядок рассуждения" in state["answer_prompt"], "")
check("FIB и ДК: режим вывода и карточка в ответе", r["reasoning"] and r["understanding"]["cards"] == ["admin_competence"], r.get("understanding"))
check("FIB и ДК: два вызова модели (разбор + ответ)", state["calls"] == ["plan", "answer"], state["calls"])

# 2. Уточняющий вопрос с памятью диалога
state["plans"] = {"сколько за это дают": {
    "intent": "penalty", "standalone": "Какое наказание предусмотрено за кражу",
    "subject": "", "action": "кража чужого имущества",
    "queries": ["кража хищение чужого имущества наказание"], "docs": ["yk"], "cards": []}}
r = ask("а сколько за это дают", "lspd", history=[("Какая статья за кражу?", "Ст. 10.1 УК: кража.")])
check("Уточнение: найдена статья про кражу (УК 10.1)", has(r, "Уголовный", "10.1"), names(r))
check("Уточнение: вопрос переписан с учётом диалога", "кражу" in r["understanding"]["standalone"].lower(), r.get("understanding"))
check("Уточнение: ИИ видит предыдущий обмен", state["answer_prompt"] and "Предыдущий вопрос" in state["answer_prompt"], "")

# 3. Разбор недоступен: уточнение всё равно работает по эвристике
state["plans"] = {}
r = ask("а сколько за это дают", "lspd", history=[("Какая статья за кражу?", "Ст. 10.1 УК.")])
check("Без разбора: уточнение дополняется прошлым вопросом", has(r, "Уголовный", "10.1"), names(r))

# 4. Мусор вместо JSON и чужие документы
state["plans"] = {"работать по ДК": "Я думаю, что можно. Без JSON."}
r = ask("Может ли сотрудник FIB работать по ДК", "fib")
check("Мусор вместо JSON: ответ всё равно строится по картам и поиску", has(r, "Административных", "19") and r["answer"], names(r))
state["plans"] = {"работать по ДК": {"intent": "power", "standalone": "x y z w", "queries": ["полномочия"],
                                     "docs": ["ustav_lspd", "koap", "zzz"], "cards": ["несуществующая"]}}
r = ask("Может ли сотрудник FIB работать по ДК", "fib")
check("Чужой и выдуманный документ отброшены", r["understanding"]["docs"] == ["koap"] and "несуществующая" not in r["understanding"]["cards"], r.get("understanding"))

# 5. Без разбора карточки подбираются по ключевым словам
r = ask("Может ли сотрудник LSPD обыскать гараж без ордера", "lspd", planner="off")
check("Без ИИ-разбора: карточка «обыск» по словам, ПК ст. 4", has(r, "Процессуальный", "4") and "search" in r["understanding"]["cards"], r.get("understanding"))
check("Планировщик выключен: он не вызывался", "plan" not in state["calls"], state["calls"])

# 6. Точный номер статьи: ни разбора, ни ИИ
r = ask("статья 12.8 УК", "fib")
check("Номер статьи: без вызовов модели", state["calls"] == [] and r["deterministic"], state["calls"])

# 7. Обычный вопрос с уверенным поиском не тратит запрос на разбор
r = ask("Какой штраф за превышение скорости на 30 км/ч", "lspd")
check("Уверенный вопрос: разбор не нужен", "plan" not in state["calls"], state["calls"])

print(f"\nПройдено: {sum(checks)}/{len(checks)}")
sys.exit(0 if all(checks) else 1)
