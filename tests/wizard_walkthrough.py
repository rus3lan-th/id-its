"""Прогон мастера «Перечень работ» (4 шага) через HTTP на КОПИИ базы.

    python tests/wizard_walkthrough.py --db /workspace/idits-data/ro.sqlite3 --files /workspace/idits-data/data/files [--json out.json]

Проверяет: статусы шагов и блокировку следующих, счётчики, «устарело» после правки перечня, работу прежних эндпоинтов.
"""
import argparse, base64, hashlib, json, os, shutil, sys, tempfile

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
ap.add_argument("--json", default="")
a = ap.parse_args()

tmp = tempfile.mkdtemp(prefix="idits-wiz-")
shutil.copy(a.db, os.path.join(tmp, "db.sqlite3"))
import sqlite3 as _sq
_c = _sq.connect(os.path.join(tmp, "db.sqlite3"))
try:  # сценарий проверяет мастер с нуля: сбрасываем его состояние у проверяемого раздела (только в копии)
    _c.execute("DELETE FROM list_wizard WHERE section_id=?", (a.section,)); _c.commit()
except _sq.OperationalError:
    pass
_c.close()
salt = b"0123456789abcdef"
os.environ.update(APP_USER="wiztest", APP_PASSWORD_HASH="1000:%s:%s" % (salt.hex(), hashlib.pbkdf2_hmac("sha256", b"wiz", salt, 1000).hex()))
for k, v in dict(APP_DB_PATH=os.path.join(tmp, "db.sqlite3"), APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "seeds"),
                 APP_OVERRIDE_DIR=os.path.join(tmp, "ov"), APP_BACKUP_DIR=os.path.join(tmp, "ab"), APP_PHOTOS_DIR=os.path.join(tmp, "ph"),
                 APP_INBOX_DIR=os.path.join(tmp, "in"), APP_DATA_BACKUP_DIR=os.path.join(tmp, "bk")).items():
    os.environ[k] = v
sys.path.insert(0, os.path.abspath(a.app))
import app as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

A.init_db()
cl = TestClient(A.app)
cl.headers["Authorization"] = "Basic " + base64.b64encode(b"wiztest:wiz").decode()
S = a.section
log, fails = [], []


def call(m, path, body=None, expect=200):
    r = cl.request(m, path + ("&" if "?" in path else "?") + "section=" + S, json=body)
    if r.status_code != expect:
        fails.append(f"{m} {path}: {r.status_code} (ждали {expect}) {r.text[:200]}")
    return r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


def st(tag):
    w = call("GET", "/api/wizard")
    row = {"tag": tag, "statuses": [s["status"] for s in w["steps"]], "current": w["current"]}
    log.append(row)
    print(f"{tag:42} {' | '.join(row['statuses'])}")
    return w


def check(cond, msg):
    if not cond:
        fails.append(msg)


w = st("0. исходно (база до мастера)")
# v.90: при миграции в мастер 2.0 прежний перечень считается подтверждённым (шаги 1–2) — автосопоставление доступно сразу
check(w["steps"][0]["status"] == "done" and w["steps"][1]["status"] == "done", "шаги 1–2 подтверждены миграцией (перечень был до мастера)")
check(w["steps"][2]["status"] == "work" and w["steps"][3]["status"] == "locked", "шаг 3 доступен, шаг 4 — только после сопоставления")
call("POST", "/api/wizard/step", {"step": 4}, expect=409)

an = call("POST", "/api/wizard/analyze")
print("  шаг 1, найдено:", json.dumps({k: an["found"][k] for k in ("new_project", "new_by_kind", "reference", "estimate_positions", "other_nets")}, ensure_ascii=False))
call("POST", "/api/wizard/step", {"step": 1})
w = st("1. перечень сформирован")
s1 = w["steps"][0]["counters"]

lst = call("GET", "/api/wizard/list")["elements"]
print(f"  шаг 2: объектов в перечне {len(lst)}")
call("POST", "/api/wizard/step", {"step": 2})
w = st("2. перечень подтверждён")
s2 = w["steps"][1]["counters"]
check(w["steps"][2]["status"] == "work", "после подтверждения шаг 3 должен быть доступен")

m1 = call("POST", "/api/wizard/match")
w = st("3. автосопоставление")
c3 = m1["counters"]
print("  шаг 3:", json.dumps(c3, ensure_ascii=False))
check(w["steps"][3]["status"] == "work", "после шага 3 шаг 4 должен быть в работе")
check(w["steps"][3]["counters"]["left_works"] == c3["left_works"], "остатки шага 4 не совпадают со счётчиками шага 3")

# правка после подтверждения: объект → «другой подрядчик»
well = next(e for e in lst if e["kind"].startswith("Колодец"))
r = call("PUT", f"/api/elements/{well['id']}", {"scope": "other", "scope_note": "тест мастера"})
print(f"  правка: {well['code']} → другой подрядчик; {r.get('message')}")
w = st("2'. перечень изменён после подтверждения")
check(w["steps"][1]["status"] == "work" and w["steps"][1]["changed_after_confirm"], "шаг 2 должен показать «изменён после подтверждения»")
check(w["steps"][2]["status"] == "stale", "шаг 3 должен стать «устарело»")
check(w["steps"][3]["status"] == "locked", "шаг 4 должен быть недоступен, пока сопоставление устарело")
check(not any(x["element_id"] == well["id"] for row in A.alloc_list(S)["rows"] for x in row["alloc"]), "у исключённого объекта не должно остаться привязок")
fore = cl.get(f"/api/elements?section={S}").json()
check(not any(e.get("id") == well["id"] for e in (fore if isinstance(fore, list) else fore.get("elements", []))), "объект другого подрядчика не должен попадать прорабу")

call("POST", "/api/wizard/step", {"step": 2})
m2 = call("POST", "/api/wizard/match")
w = st("3'. повторное сопоставление")
c3b = m2["counters"]
print("  шаг 3':", json.dumps(c3b, ensure_ascii=False))
left = w["steps"][3]["counters"]
print("  шаг 4, осталось вручную:", json.dumps(left, ensure_ascii=False))
call("POST", "/api/wizard/step", {"step": 4})
w = st("4. завершено → передано прорабу")
check(w["handed_to_foreman"], "после «Завершить» перечень должен быть передан прорабу")

for p in ("/api/alloc", "/api/elements", "/api/suggest", "/api/overview", "/api/work-analysis"):
    r = cl.get(p + "?section=" + S)
    check(r.status_code == 200, f"прежний эндпоинт {p}: {r.status_code}")
r = cl.get("/")
check(r.status_code == 200 and "wizSteps" in r.text, "главная страница не отдаёт мастер")

out = {"steps_log": log, "step1": {"found": an["found"], "counters": s1}, "step2": s2, "step3": c3, "step3_after_edit": c3b, "step4_left": left, "fails": fails}
if a.json:
    json.dump(out, open(a.json, "w"), ensure_ascii=False, indent=1)
print("ОШИБКИ:" if fails else "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ", *fails, sep="\n  ")
sys.exit(1 if fails else 0)
