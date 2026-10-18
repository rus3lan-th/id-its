"""Мастер 2.0, этап Ф0: миграция, публикация, снимки, полный и частичный откат, права — на КОПИИ базы.

    python tests/wizard_v2.py --db /workspace/idits-data/ro.sqlite3 --files /workspace/idits-data/data/files [--json out.json]
"""
import argparse, base64, json, os, shutil, sqlite3, sys, tempfile, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
ap.add_argument("--json", default="")
a = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="idits-wz2-")
DB = os.path.join(tmp, "db.sqlite3")
shutil.copy(a.db, DB)
os.environ.update(APP_USER="admin-test", APP_PASSWORD_HASH="1:00:00")
for k, v in dict(APP_DB_PATH=DB, APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "s"), APP_OVERRIDE_DIR=os.path.join(tmp, "o"), APP_BACKUP_DIR=os.path.join(tmp, "b"),
                 APP_PHOTOS_DIR=os.path.join(tmp, "p"), APP_INBOX_DIR=os.path.join(tmp, "i"), APP_DATA_BACKUP_DIR=os.path.join(tmp, "d")).items():
    os.environ[k] = v
pre = sqlite3.connect(DB)
before = {r[0]: (r[1], r[2], r[3]) for r in pre.execute("""SELECT s.id, (SELECT COUNT(*) FROM elements e WHERE e.section_id=s.id AND e.enabled=1),
  (SELECT COUNT(*) FROM operations o JOIN elements e ON e.id=o.element_id WHERE e.section_id=s.id AND e.enabled=1),
  (SELECT COUNT(DISTINCT block) FROM elements e WHERE e.section_id=s.id AND e.enabled=1 AND block<>'') FROM sections s""")}
ev_before = pre.execute("SELECT COUNT(*) FROM events").fetchone()[0]
pre.close()
sys.path.insert(0, os.path.abspath(a.app))
import app as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

A.init_db()
fails, log = [], []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    log.append(("ok" if cond else "FAIL") + " " + msg)
    if not cond:
        fails.append(msg)


def mk(login, role, pid):
    with sqlite3.connect(DB) as c:
        uid = str(uuid.uuid4())
        c.execute("INSERT INTO dict_users (id,name,login,app_role,active,created_at,pw_hash) VALUES (?,?,?,?,1,?,?)", (uid, login, login, role, A.now(), A._hash_pw("pw")))
        c.execute("INSERT INTO team (id,project_id,name,position,app_role,sort_order,created_at,login,user_id,valid_from,valid_to) VALUES (?,?,?,?,?,0,?,?,?,'','')",
                  (str(uuid.uuid4()), pid, login, role, role, A.now(), login, uid))
    cl = TestClient(A.app)
    cl.headers["Authorization"] = "Basic " + base64.b64encode(f"{login}:pw".encode()).decode()
    return cl


S = a.section
c0 = sqlite3.connect(DB)
c0.row_factory = sqlite3.Row
pid = c0.execute("SELECT project_id FROM sections WHERE id=?", (S,)).fetchone()[0]
P, F = mk("pto.wz2", "pto", pid), mk("prorab.wz2", "foreman", pid)
q = "?section=" + S
before_n_obj = before[S][0]

print("1. Миграция 6 разделов")
for sid, (n_el, n_op, n_bl) in before.items():
    if not n_el:
        continue
    o = c0.execute("SELECT COUNT(*) FROM wz_items WHERE section_id=? AND layer='object'", (sid,)).fetchone()[0]
    w = c0.execute("SELECT COUNT(*) FROM wz_items WHERE section_id=? AND layer='work'", (sid,)).fetchone()[0]
    g = c0.execute("SELECT COUNT(*) FROM wz_groups WHERE section_id=?", (sid,)).fetchone()[0]
    sn = c0.execute("SELECT COUNT(*) FROM wz_snapshots WHERE section_id=? AND kind='migration' AND step IN (1,2)", (sid,)).fetchone()[0]
    code = c0.execute("SELECT code FROM sections WHERE id=?", (sid,)).fetchone()[0]
    check(o == n_el and w == n_op and g == n_bl and sn == 2, f"{code}: объектов {o}/{n_el}, работ {w}/{n_op}, групп {g}/{n_bl}, снимков миграции {sn}")
A.init_db()
check(c0.execute("SELECT COUNT(*) FROM wz_items").fetchone()[0] == sum(v[0] + v[1] for v in before.values()), "повторный запуск миграции ничего не дублирует")
with A.db() as c:
    pubs = [A._wz2_publish(c, s, "t") for s in c.execute("SELECT * FROM sections").fetchall()]
check(all(not p for p in pubs), "публикация сразу после миграции ничего не меняет у прораба (все 6 разделов)")
st = P.get("/api/wz2" + q).json()
check([s["status"] for s in st["steps"]][:2] == ["done", "done"] and st["steps"][2]["status"] in ("work", "done"), f"статусы шагов после миграции: {[s['status'] for s in st['steps']]}")
els = F.get("/api/elements" + q).json()
N0 = len(els)
check(N0 == before_n_obj, f"прораб видит перечень как раньше: {N0}")

print("2. Права")
for m, path, body in (("POST", "/api/wz2/confirm", {"step": "1"}), ("POST", "/api/wz2/rollback", {"snapshot_id": "x", "reason": "тест"}), ("POST", "/api/wz2/groups", {"name": "x"}),
                      ("POST", "/api/wz2/items/bulk", {"ids": ["x"], "state": "rejected"})):
    check(F.request(m, path + q, json=body).status_code == 403, f"прораб: {m} {path} → 403")

print("3. Правка шага 1 и публикация")
objs = P.get("/api/wz2/items" + q + "&layer=object").json()["items"]
marked = next(o for o in objs if o["marks"] > 0)
plain = next(o for o in objs if o["marks"] == 0)
snap_mig = next(x["id"] for x in st["snapshots"] if x["step"] == 1 and x["kind"] == "migration")
r = P.post("/api/wz2/items/bulk" + q, json={"ids": [marked["id"], plain["id"]], "state": "rejected"})
check(r.status_code == 200, f"отклонены {marked['key']} (отметок {marked['marks']}) и {plain['key']}")
P.put(f"/api/wz2/items/{objs[5]['id']}", json={"name": objs[5]["name"] + " (испр.)", "layer": "object"})
st = P.get("/api/wz2" + q).json()
check(st["steps"][0]["status"] == "work" and "изменён" in st["steps"][0]["note"], "шаг 1: «изменён после подтверждения»")
check(len(F.get("/api/elements" + q).json()) == N0, "до подтверждения прораб видит прежний перечень")
r = P.post("/api/wz2/confirm" + q, json={"step": "1"}).json()
check(r["published"].get("el_disabled") == 2 and r["published"].get("el_changed") == 1, f"публикация: {r['published']}")
check(r["state"]["steps"][1]["status"] == "stale", "шаг 2 → «устарело»")
check(r["state"]["steps"][2]["status"] in ("stale", "locked"), f"шаг 3 после изменения: {r['state']['steps'][2]['status']}")
check(len(F.get("/api/elements" + q).json()) == N0 - 2, f"прораб видит {N0 - 2} объекта")
ev_now = c0.execute("SELECT COUNT(*) FROM events").fetchone()[0]
check(ev_now == ev_before, f"отметки прораба не удалены: {ev_now}/{ev_before}")
check(P.get("/api/overview" + q).json()["elements"] == N0 - 2, "обзор: на 2 объекта меньше")

print("4. Полный откат шага 1 к снимку миграции")
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "dry_run": True}).json()
check(d["counts"]["change"] == 3 and d["counts"]["remove"] == 0 and d["marks"] == 0, f"предпросмотр: {d['counts']} (2 объекта вернутся, 1 правка отменится), отметок {d['marks']} — возврат отметки не затрагивает")
check(P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig}).status_code == 422, "без причины — отказ 422")
r = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "reason": "ошибка в перечне"}).json()
check(r.get("counts", {}).get("change") == 3, f"откат выполнен: {r.get('counts')}")
undo = r["undo_snapshot"]
P.post("/api/wz2/confirm" + q, json={"step": "1"})
els = F.get("/api/elements" + q).json()
check(len(els) == N0 and any(e["id"] == marked["element_id"] for e in els), "после подтверждения прораб снова видит все объекты, тот же объект с прежним id")
m_ops = [o for e in els if e["id"] == marked["element_id"] for o in e["operations"] if o["event_type"]]
check(len(m_ops) > 0, f"отметки вернувшегося объекта {marked['key']} на месте: {len(m_ops)}")

print("5. Откат, затрагивающий отметки прораба (отмена отката)")
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": undo, "dry_run": True}).json()
check(d["marks"] > 0 and d["counts"]["remove"] == 0, f"предпросмотр отмены отката: {d['counts']}, отметок {d['marks']}")
check(P.post("/api/wz2/rollback" + q, json={"snapshot_id": undo, "reason": "переделка по замечанию технадзора"}).status_code == 409, "без подтверждения отметок — отказ 409")
r = P.post("/api/wz2/rollback" + q, json={"snapshot_id": undo, "reason": "переделка по замечанию технадзора", "confirm_marks": True})
check(r.status_code == 200, "с подтверждением — выполнено")
st = P.get("/api/wz2/items" + q + "&layer=object&state=rejected").json()["items"]
check({x["id"] for x in st} == {marked["id"], plain["id"]}, "состояние «перед откатом» восстановлено (2 отклонённых)")

print("6. Частичный откат: только выделенные пункты")
mig_back = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "mode": "items", "ids": [plain["id"]], "reason": "вернуть один объект"}).json()
rej = {x["id"] for x in P.get("/api/wz2/items" + q + "&layer=object&state=rejected").json()["items"]}
check(rej == {marked["id"]}, f"восстановлен только {plain['key']}, {marked['key']} остался отклонённым")
nm = {x["id"]: x["name"] for x in P.get("/api/wz2/items" + q + "&layer=object").json()["items"]}
check(nm[objs[5]["id"]].endswith("(испр.)"), "правка другого пункта не затронута")

print("7. Шаг 2: группы, публикация, откат группы")
P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "reason": "чистое состояние"})
P.post("/api/wz2/confirm" + q, json={"step": "1"})
blk0 = {e["id"]: e["block"] for e in F.get("/api/elements" + q).json()}
g = P.post("/api/wz2/groups" + q, json={"name": "Узел К2-10 (тест)"}).json()["id"]
three = [o["id"] for o in objs[:3]]
P.post("/api/wz2/items/bulk" + q, json={"ids": three, "group_id": g})
r = P.post("/api/wz2/confirm" + q, json={"step": "2"}).json()
check(r["published"].get("el_changed") == 3, f"публикация групп: {r['published']}")
blk = {e["id"]: e["block"] for e in F.get("/api/elements" + q).json()}
check(all(blk[o["element_id"]] == "Узел К2-10 (тест)" for o in objs[:3]), "у прораба 3 объекта в новой группе")
check(r["state"]["steps"][2]["status"] in ("work", "stale"), f"шаг 3 доступен после подтверждения шага 2: {r['state']['steps'][2]['status']}")
snap2 = next(x["id"] for x in r["state"]["snapshots"] if x["step"] == 2 and x["kind"] == "migration")
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap2, "dry_run": True}).json()
check(d["counts"]["groups_remove"] == 1 and d["counts"]["members_remove"] == 3, f"предпросмотр отката шага 2: {d['counts']}")
P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap2, "reason": "группа не нужна"})
r = P.post("/api/wz2/confirm" + q, json={"step": "2"}).json()
blk2 = {e["id"]: e["block"] for e in F.get("/api/elements" + q).json()}
check(all(blk2[o["element_id"]] == blk0[o["element_id"]] for o in objs[:3]), f"после отката и подтверждения объекты вернулись на прежние участки: {[blk2[o['element_id']] for o in objs[:3]]}")

print("8. Частичный откат по файлу-источнику")
fid = c0.execute("SELECT id FROM source_files WHERE section_id=? AND kind='dxf_project' LIMIT 1", (S,)).fetchone()[0]
with sqlite3.connect(DB) as c:
    par = objs[0]
    for i in range(3):
        c.execute("INSERT INTO wz_items (id,section_id,layer,key,parent_id,name,kind,origin,state,source_file_id,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (f"auto{i}", S, "work", f"{par['key']}/auto{i}", par["id"], f"Авто-работа {i}", f"auto{i}", "auto", "proposed" if i < 2 else "edited", fid, A.now()))
d = P.post("/api/wz2/rollback" + q, json={"mode": "file", "file_id": fid, "dry_run": True}).json()
check(d["counts"]["remove"] == 2 and d["kept_edited"] == 1, f"предпросмотр по файлу: убрать {d['counts']['remove']}, оставить изменённых вручную {d['kept_edited']}")
P.post("/api/wz2/rollback" + q, json={"mode": "file", "file_id": fid, "reason": "файл заменён новой редакцией"})
left = {r[0] for r in c0.execute("SELECT id FROM wz_items WHERE source_file_id=?", (fid,))}
check(left == {"auto2"}, f"осталась только изменённая вручную: {sorted(left)}")

print("9а. Объект, добавленный прежним способом, не теряется")
r = P.post("/api/elements" + q, json={"code": "К2-ТЕСТ", "name": "Колодец тестовый", "kind": "Колодец", "block": "Уч.1 К2-12…К2-7", "planned_note": ""})
check(r.status_code == 201, "добавлен объект через прежнюю форму")
st = P.get("/api/wz2" + q).json()
check(st["steps"][0]["status"] == "work", f"шаг 1 просит подтверждения: {st['steps'][0]['note']}")
r = P.post("/api/wz2/confirm" + q, json={"step": "1"}).json()
check(not r["published"].get("el_disabled") and any(e["code"] == "К2-ТЕСТ" for e in F.get("/api/elements" + q).json()), f"после подтверждения объект на месте, публикация: {r['published']}")

print("9. Журнал и прежние страницы")
h = P.get("/api/wz2/history" + q).json()["history"]
check(len(h) > 10 and any(x["reason"] == "переделка по замечанию технадзора" for x in h), f"журнал изменений: {len(h)} записей, причины откатов записаны")
for pth in ("/api/alloc", "/api/overview", "/api/gantt", "/api/wizard", "/api/elements"):
    check(P.get(pth + q).status_code == 200, f"{pth} → 200")
r = P.get("/")
check(r.status_code == 200 and "wz2Journal" in r.text, "главная страница отдаёт мастер 2.0")
print("ОШИБКИ:" if fails else "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ", *fails, sep="\n  ")
if a.json:
    json.dump({"log": log, "fails": fails}, open(a.json, "w"), ensure_ascii=False, indent=1)
sys.exit(1 if fails else 0)
