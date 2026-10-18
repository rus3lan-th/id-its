"""Права прораба и передача перечня (v.89) на КОПИИ базы — личные входы ПТО и прораба (не администратор).

    python tests/foreman_access.py --db /workspace/idits-data/ro.sqlite3 --files /workspace/idits-data/data/files
"""
import argparse, base64, os, shutil, sqlite3, sys, tempfile, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
a = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="idits-fa-")
DB = os.path.join(tmp, "db.sqlite3")
shutil.copy(a.db, DB)
os.environ.update(APP_USER="admin-test", APP_PASSWORD_HASH="1:00:00")
for k, v in dict(APP_DB_PATH=DB, APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "seeds"), APP_OVERRIDE_DIR=os.path.join(tmp, "ov"),
                 APP_BACKUP_DIR=os.path.join(tmp, "ab"), APP_PHOTOS_DIR=os.path.join(tmp, "ph"), APP_INBOX_DIR=os.path.join(tmp, "in"),
                 APP_DATA_BACKUP_DIR=os.path.join(tmp, "bk")).items():
    os.environ[k] = v
sys.path.insert(0, os.path.abspath(a.app))
import app as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

A.init_db()
S = a.section
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


with sqlite3.connect(DB) as c:
    pid = c.execute("SELECT project_id FROM sections WHERE id=?", (S,)).fetchone()[0]
    for login, role in (("prorab.test", "foreman"), ("pto.test", "pto")):
        uid = str(uuid.uuid4())
        c.execute("INSERT INTO dict_users (id,name,login,app_role,active,created_at,pw_hash) VALUES (?,?,?,?,1,?,?)", (uid, login, login, role, A.now(), A._hash_pw("pw-" + login)))
        c.execute("INSERT INTO team (id,project_id,name,position,app_role,sort_order,created_at,login,user_id,valid_from,valid_to) VALUES (?,?,?,?,?,0,?,?,?,'','')",
                  (str(uuid.uuid4()), pid, login, "Прораб" if role == "foreman" else "Инженер ПТО", role, A.now(), login, uid))
    gf = c.execute("SELECT handed_at, handed_by FROM list_wizard WHERE section_id=?", (S,)).fetchone()
    n_gf = c.execute("SELECT COUNT(*) FROM list_wizard WHERE handed_at<>''").fetchone()[0]
    n_sec = c.execute("SELECT COUNT(DISTINCT section_id) FROM elements WHERE enabled=1").fetchone()[0]


def client(login):
    cl = TestClient(A.app)
    cl.headers["Authorization"] = "Basic " + base64.b64encode(f"{login}:pw-{login}".encode()).decode()
    return cl


F, P = client("prorab.test"), client("pto.test")
q = "?section=" + S
print("1. Миграция: разделы с готовым перечнем считаются переданными прорабу")
check(bool(gf and gf[0]), f"Грушевская передана при миграции ({gf[1] if gf else '—'})")
check(n_gf == n_sec, f"переданы все разделы с перечнем: {n_gf} из {n_sec}")
els = F.get("/api/elements" + q).json()
check(isinstance(els, list) and len(els) == 86, f"прораб видит перечень: {len(els)} объектов")
ov = F.get("/api/overview" + q).json()
check(ov.get("list_locked") is False, "в обзоре у прораба нет блокировки")
op = next(o for e in els for o in e["operations"] if not o["event_type"])
r = F.post("/api/events", json={"operation_id": op["operation_id"], "performed_on": "2026-10-18", "actor": "Прораб тест", "out_of_order": True, "note": "тест"})
check(r.status_code in (201, 409, 422) and r.status_code != 423, f"прораб может отмечать (ответ {r.status_code}: {r.text[:120]})")

print("2. Права прораба на мастер и перечень")
for m, path, body in (("POST", "/api/wizard/step", {"step": 2}), ("POST", "/api/wizard/match", None), ("POST", "/api/wizard/analyze", None),
                      ("PUT", f"/api/elements/{els[0]['id']}", {"scope": "other"}), ("POST", "/api/alloc/auto", None)):
    r = F.request(m, path + q, json=body)
    check(r.status_code == 403, f"прораб: {m} {path} → {r.status_code}")
check(F.get("/api/wizard" + q).status_code == 200, "прораб может смотреть статус мастера")

print("3. Новый раздел (перечень ещё не передан): прораб не видит и не отмечает")
with sqlite3.connect(DB) as c:
    c.execute("UPDATE list_wizard SET handed_at='', handed_by='', s2_at='', s3_at='', s4_at='' WHERE section_id=?", (S,))
els = F.get("/api/elements" + q).json()
check(els == [], f"прораб получает пустой перечень ({len(els)})")
ov = F.get("/api/overview" + q).json()
check(ov.get("list_locked") is True and "Перечень готовит ПТО" in ov.get("list_locked_msg", ""), "обзор сообщает «Перечень готовит ПТО»")
r = F.post("/api/events", json={"operation_id": op["operation_id"], "performed_on": "2026-10-18", "actor": "Прораб тест"})
check(r.status_code == 423 and "Перечень готовит ПТО" in r.text, f"отметка прораба отклонена: {r.status_code}")
pe = P.get("/api/elements" + q).json()
check(len(pe) == 86, f"ПТО (личный вход) видит перечень: {len(pe)}")

print("4. ПТО (личный вход, не администратор) проходит мастер")
check(P.post("/api/wizard/step" + q, json={"step": 2}).status_code == 200, "ПТО подтверждает перечень")
r = P.put(f"/api/elements/{pe[0]['id']}" + q, json={"scope": "other", "scope_note": "тест"})
check(r.status_code == 200, f"ПТО ставит «другой подрядчик» на {pe[0]['code']}")
check(P.post("/api/wizard/step" + q, json={"step": 2}).status_code == 200, "ПТО подтверждает заново")
r = P.post("/api/wizard/match" + q)
check(r.status_code == 200, f"ПТО сопоставляет: {r.json().get('counters', {}) if r.status_code == 200 else r.text[:150]}")
check(F.get("/api/elements" + q).json() == [], "до «Завершить» прораб всё ещё не видит перечень")
check(P.post("/api/wizard/step" + q, json={"step": 4}).status_code == 200, "ПТО нажимает «Завершить»")
els = F.get("/api/elements" + q).json()
check(len(els) == 85, f"после «Завершить» прораб видит перечень без объекта другого подрядчика: {len(els)}")
r = F.post("/api/events", json={"operation_id": op["operation_id"], "performed_on": "2026-10-18", "actor": "Прораб тест"})
check(r.status_code != 423, f"прораб снова может отмечать (ответ {r.status_code})")

print("5. Проценты: объект другого подрядчика не считается")
ov = P.get("/api/overview" + q).json()
check(ov["elements"] == 85, f"обзор: объектов {ov['elements']}, операций {ov['operations']}, выполнено {ov['completed']}")
g = P.get("/api/gantt" + q).json()
ge = sum(b.get("elements", 0) for b in g.get("blocks", g.get("rows", [])) if isinstance(b, dict))
check(ge == 85, f"график: объектов в участках {ge}")
print("ОШИБКИ:" if fails else "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ", *fails, sep="\n  ")
sys.exit(1 if fails else 0)
