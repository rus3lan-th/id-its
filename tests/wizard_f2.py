"""Мастер 2.0, этап Ф2: нормативы, материалы, сверка с ресурсами сметы, замечания, нормы трудоёмкости, уведомления прораба, откат шагов 3–4 — на КОПИИ базы.

    python tests/wizard_f2.py --db copy.sqlite3 --files data/files --gesn norm_records.sqlite3 [--json out.json]
    (norm_records.sqlite3 — таблица norm_records после загрузки ГЭСН.xml; см. README)
"""
import argparse, base64, json, os, shutil, sqlite3, sys, tempfile, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
ap.add_argument("--json", default="")
ap.add_argument("--gesn", required=True, help="sqlite с таблицей norm_records (индекс ГЭСН) — подгружается в копию")
a = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="idits-wzf2-")
DB = os.path.join(tmp, "db.sqlite3")
shutil.copy(a.db, DB)
os.environ.update(APP_USER="admin-test", APP_PASSWORD_HASH="1:00:00")
for k, v in dict(APP_DB_PATH=DB, APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "s"), APP_OVERRIDE_DIR=os.path.join(tmp, "o"), APP_BACKUP_DIR=os.path.join(tmp, "b"),
                 APP_PHOTOS_DIR=os.path.join(tmp, "p"), APP_INBOX_DIR=os.path.join(tmp, "i"), APP_DATA_BACKUP_DIR=os.path.join(tmp, "d")).items():
    os.environ[k] = v
pre = sqlite3.connect(DB)
if a.gesn:
    pre.execute("ATTACH ? AS g", (a.gesn,))
    pre.execute("DELETE FROM norm_records")
    pre.execute("INSERT INTO norm_records SELECT * FROM g.norm_records")
    pre.commit()
    pre.execute("DETACH g")
before = {r[0]: (r[1], r[2], r[3]) for r in pre.execute("""SELECT s.id, (SELECT COUNT(*) FROM elements e WHERE e.section_id=s.id AND e.enabled=1),
  (SELECT COUNT(*) FROM operations o JOIN elements e ON e.id=o.element_id WHERE e.section_id=s.id AND e.enabled=1),
  (SELECT COUNT(DISTINCT block) FROM elements e WHERE e.section_id=s.id AND e.enabled=1 AND block<>'') FROM sections s""")}
ev_before = pre.execute("SELECT COUNT(*) FROM events").fetchone()[0]
pre.close()
sys.path.insert(0, os.path.abspath(a.app))
import app as A  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

A.init_db()
A._wells_dxf = lambda fid: {"sheet": {}, "profile": {}}   # v.94: ведомость/профили DXF проверяет tests/wells_depth.py; здесь — шаблоны колодцев
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
P, F = mk("pto.f2", "pto", pid), mk("prorab.f2", "foreman", pid)
q = "?section=" + S
gesn_n = c0.execute("SELECT COUNT(*) FROM norm_records").fetchone()[0]

print("0. Обновление базы до v.91")
check(c0.execute("SELECT COUNT(*) FROM wz_norm_library").fetchone()[0] == 6, "библиотека нормативов по умолчанию: 6 документов")
al_secs = {r[0] for r in c0.execute("SELECT DISTINCT section_id FROM work_alloc")}
s3 = {r[0] for r in c0.execute("SELECT DISTINCT section_id FROM wz_snapshots WHERE step=3")}
check(al_secs <= s3, f"снимок сопоставления (шаг 3) для всех разделов со сметными привязками: {len(al_secs & s3)}/{len(al_secs)}")
A.init_db()
check(c0.execute("SELECT COUNT(*) FROM wz_snapshots WHERE step=3 AND kind='migration'").fetchone()[0] == len(al_secs), "повторный запуск не дублирует снимки")
st0 = P.get("/api/wz2" + q).json()
s1_0, s3_0 = st0["steps"][0]["status"], st0["steps"][2]["status"]
print(f"  исходно: шаг 1 {s1_0}, шаг 3 {s3_0}; индекс ГЭСН: {gesn_n} норм")

print("1. Права")
for m, path, body in (("POST", "/api/wz2/norms/fill" + q, None), ("POST", "/api/wz2/materials/fill" + q, None), ("POST", "/api/wz2/materials/compare" + q, None),
                      ("POST", "/api/wz2/remarks" + q, {"text": "x"}), ("POST", "/api/labor-norms", {"code": "x", "hours": 1}), ("POST", "/api/wz2/normlib", {"designation": "СП 1.1"}),
                      ("PUT", "/api/wz2/params" + q, {"threshold_pct": 5})):
    check(F.request(m, path, json=body).status_code == 403, f"прораб: {m} {path.split('?')[0]} → 403")
check(F.get("/api/wz2/remarks" + q).status_code == 200, "прораб может читать замечания")

print("2. Подшаг 1.1 — нормативы")
r = P.post("/api/wz2/norms/fill" + q).json()
norms = P.get("/api/wz2/items" + q + "&layer=norm").json()["items"]
lib = [n for n in norms if n["origin"] == "library"]
dxf = [n for n in norms if n["origin"] == "auto"]
check(len(lib) == 6 and all(n["state"] == "accepted" for n in lib), f"библиотека подставлена и принята: {len(lib)}")
check(any(n["key"] == "СП 129.13330.2019" and "565256600" in n["note"] for n in lib), "СП 129.13330.2019 со ссылкой на docs.cntd.ru")
g8020 = next((n for n in dxf if n["key"] == "ГОСТ 8020-2016"), None)
check(g8020 is not None and json.loads(g8020["data_json"])["count"] >= 10, f"из DXF: ГОСТ 8020-2016 (упоминаний {json.loads(g8020['data_json'])['count'] if g8020 else 0}); всего ссылок в DXF {len(dxf)}")
check(all(n["state"] == "proposed" for n in dxf), "ссылки из DXF — «предложено», ждут решения ПТО")
print("     найдено в DXF:", ", ".join(f"{n['key']} ×{json.loads(n['data_json'])['count']}" for n in dxf[:12]))
P.post("/api/wz2/items/bulk" + q, json={"ids": [dxf[-1]["id"]], "state": "rejected"})
r2 = P.post("/api/wz2/norms/fill" + q).json()
norms2 = P.get("/api/wz2/items" + q + "&layer=norm").json()["items"]
check(len(norms2) == len(norms) and not r2["result"].get("dxf_added") and not r2["result"].get("library_added"), f"повторное заполнение не дублирует: {r2['result']}")
check(next(n for n in norms2 if n["id"] == dxf[-1]["id"])["state"] == "rejected", "решение ПТО «отклонено» сохраняется при повторном заполнении")
st = P.get("/api/wz2" + q).json()
check(st["steps"][0]["status"] == s1_0 and st["steps"][2]["status"] == s3_0, f"нормативы не сбивают шаг 1 и сопоставление (шаг 1 {st['steps'][0]['status']}, шаг 3 {st['steps'][2]['status']})")

print("3. Подшаг 1.3 — материалы")
r = P.post("/api/wz2/materials/fill" + q).json()
print("    ", r["message"])
mats = {m["key"]: m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"]}
objs = [o for o in P.get("/api/wz2/items" + q + "&layer=object").json()["items"] if o["state"] != "rejected" and not o["scope"]]
import re as _re
n1500 = sum(1 for o in objs if _re.search(r"(?i)колод.*(1500|1,5)", o["kind"] + " " + o["name"])) + sum(v["assumed"] for k, v in r.get("rings", {}).items() if k == "КС15")  # + колодцы без типа, принятые Ø1500 по сети (v.92)
check("труба|гофр.|800" in mats and (mats["труба|гофр.|800"]["qty"] or 0) > 100, f"труба гофр. Ø800 по длинам участков: {mats.get('труба|гофр.|800', {}).get('qty')} м")
check(mats.get("жби|ПН15", {}).get("qty") == n1500, f"плиты днища ПН15 = колодцев Ø1500 (с принятыми по сети): {mats.get('жби|ПН15', {}).get('qty')} / {n1500}")
check(any(k.startswith("dxf|") or mats[k]["qty"] is None for k in mats), f"упоминания в DXF без количества тоже в ведомости ({sum(1 for k in mats if mats[k]['qty'] is None)})")
m800 = mats["труба|гофр.|800"]
P.put(f"/api/wz2/items/{m800['id']}", json={"layer": "material", "qty": 500.0})
P.post("/api/wz2/materials/fill" + q)
m800b = next(m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"] if m["key"] == "труба|гофр.|800")
check(m800b["qty"] == 500.0 and m800b["state"] == "edited", "ручная правка количества сохраняется при повторном заполнении")
P.put(f"/api/wz2/items/{m800['id']}", json={"layer": "material", "qty": m800["qty"]})
st = P.get("/api/wz2" + q).json()
check(st["steps"][0]["status"] == s1_0 and st["steps"][2]["status"] == s3_0, "материалы не сбивают шаг 1 и сопоставление")

print("4. Сверка материалов проект–смета (вариант А)")
d = P.post("/api/wz2/materials/compare" + q + "&dry_run=true").json()
rows = {x["key"]: x for x in d["rows"]}
x800 = rows.get("труба|гофр.|800")
check(x800 is not None and abs(x800["estimate"] - (317.34 + 146.47)) < 0.01, f"гофр. Ø800: проект {x800 and x800['project']} м, смета {x800 and x800['estimate']} м (317,34 + 146,47), итог «{x800 and d['statuses'][x800['status']]}»")
check(rows.get("жби|ПН15", {}).get("estimate") == 52, f"ПН15 по смете: {rows.get('жби|ПН15', {}).get('estimate')} шт (37 + 15)")
check(not P.get("/api/wz2/remarks" + q).json()["remarks"], "предварительная сверка замечаний не создаёт")
print("     " + d["message"])
for x in d["rows"]:
    print(f"     {x['name'][:40]:40} проект {x['project']!s:>8} смета {x['estimate']!s:>8} {d['statuses'][x['status']]}")
r = P.post("/api/wz2/materials/compare" + q).json()
rem = P.get("/api/wz2/remarks" + q).json()
bad = [x for x in d["rows"] if x["status"] != "ok"]
check(r["remarks"].get("added") == len(bad) and len(rem["remarks"]) == len(bad), f"замечаний по расхождениям: {len(rem['remarks'])} (расхождений {len(bad)})")
check(rem["remarks"][0]["code"] == "З-001", "нумерация З-001…")
pipe_r = [x for x in rem["remarks"] if x["key"].startswith("mat|труба") and x["labor_hours"]]
check(bool(pipe_r) and "ГЭСН 23-01-0" in pipe_r[0]["labor_basis"], f"трудоёмкость по ГЭСН: {pipe_r[0]['labor_basis'] if pipe_r else '—'}")
r = P.post("/api/wz2/materials/compare" + q).json()
check(not r["remarks"].get("added") and len(P.get("/api/wz2/remarks" + q).json()["remarks"]) == len(bad), "повторная сверка не дублирует замечания")
P.put("/api/wz2/params" + q, json={"threshold_pct": 100})
r = P.post("/api/wz2/materials/compare" + q).json()
check(r["remarks"].get("closed", 0) > 0, f"порог 100 %: расхождения в пределах — снято замечаний {r['remarks'].get('closed')}")
P.put("/api/wz2/params" + q, json={"threshold_pct": 3, "losses": {"труба": 2, "жби": 0, "люк": 0, "решетка": 0, "прочее": 0}})
d2 = P.post("/api/wz2/materials/compare" + q + "&dry_run=true").json()
x800b = next(x for x in d2["rows"] if x["key"] == "труба|гофр.|800")
check(abs(x800b["need"] - round(x800b["project"] * 1.02, 3)) < 0.01, f"потери 2 % для труб: нужно {x800b['need']} м при проекте {x800b['project']} м")
r = P.post("/api/wz2/materials/compare" + q).json()
rem = P.get("/api/wz2/remarks" + q).json()
check(sum(1 for x in rem["remarks"] if x["status"] == "open") >= 1, f"при пороге 3 % замечания снова открыты: {sum(1 for x in rem['remarks'] if x['status'] == 'open')}")

print("5. Нормы трудоёмкости")
lab0 = P.get("/api/labor-norms").json()
r = P.post("/api/labor-norms/seed").json()
check(r["result"].get("added", 0) > 100 or len(lab0.get("norms", [])) + r["result"].get("added", 0) > 100, f"заполнено из ГЭСН: {r['message']} (было норм: {len(lab0.get('norms', []))})")
n = next(x for x in P.get("/api/labor-norms?q=23-01-033-03").json()["norms"] if x["code"] == "23-01-033-03")
check(abs(n["hours"] - 55.67) < 1e-6 and n["unit"] == "100 м", f"23-01-033-03: {n['hours']} чел.-ч на {n['unit']} (затраты труда рабочих)")
P.put(f"/api/labor-norms/{n['id']}", json={"code": n["code"], "name": n["name"], "unit": n["unit"], "hours": 60.0})
r = P.post("/api/labor-norms/seed").json()
n2 = next(x for x in P.get("/api/labor-norms?q=23-01-033-03").json()["norms"] if x["code"] == "23-01-033-03")
check(n2["hours"] == 60.0 and n2["edited"] == 1 and r["result"].get("kept_edited", 0) >= 1, "исправленная ПТО норма не перезаписывается при обновлении из ГЭСН")
n3 = next(x for x in P.get("/api/labor-norms?q=23-03-001-06").json()["norms"] if x["code"] == "23-03-001-06")
check(abs(n3["hours"] - 148.27) < 1e-6, f"23-03-001-06 (колодцы Ø1,5 м): {n3['hours']} чел.-ч на {n3['unit']} — итог по строке «1», разряды не суммируются дважды")
rem_w = next((x for x in P.get("/api/wz2/remarks" + q).json()["remarks"] if x["key"] == "mat|жби|ПН15"), None)
check(rem_w is not None and rem_w["labor_hours"] is None and "пересчёт единиц не выполняется" in rem_w["labor_basis"], f"шт против 10 м3 — трудоёмкость не считается молча: {rem_w and rem_w['labor_basis'][:90]}")
check(P.post("/api/labor-norms", json={"code": "ИТС-1-1", "name": "Засыпка песком с уплотнением", "unit": "м3", "hours": 0.5}).status_code == 201, "своя норма ИТС-1-1: 0,5 чел.-ч/м3")
r = P.post("/api/wz2/remarks" + q, json={"type": "volume", "text": "Объём засыпки песком занижен на 100 м3", "impact_qty": "+100 м3", "labor_code": "ИТС-1-1", "labor_qty": 100})
check(r.status_code == 201, f"ручное замечание {r.json().get('code')}")
rr = next(x for x in P.get("/api/wz2/remarks" + q).json()["remarks"] if x["id"] == r.json()["id"])
check(rr["labor_hours"] == 50.0 and "своя ИТС-1-1" in rr["labor_basis"], f"трудоёмкость по своей норме: {rr['labor_hours']} чел.-ч ({rr['labor_basis']})")
r = P.post("/api/wz2/remarks" + q, json={"text": "x", "labor_code": "99-99-999-99", "labor_qty": 1})
check(r.status_code == 422, "неизвестный шифр нормы — понятная ошибка 422")
P.put(f"/api/wz2/remarks/{rr['id']}", json={"status": "accepted", "decision": "добавлено в перечень"})
check(next(x for x in P.get("/api/wz2/remarks" + q).json()["remarks"] if x["id"] == rr["id"])["status"] == "accepted", "статус замечания меняется (учтено в перечне)")
csv = P.get("/api/wz2/remarks/export.csv" + q)
check(csv.status_code == 200 and "Справка по замечаниям" in csv.text and "З-001" in csv.text and "Σ чел.-ч" in csv.text, "справка замечаний CSV")

print("6. Частичный откат по подшагу (слою)")
st = P.get("/api/wz2" + q).json()
r = P.post("/api/wz2/confirm" + q, json={"step": "1"}).json()
check(not r["published"], f"подтверждение шага 1 с нормативами и материалами — перечень прораба не меняется: {r['published']}")
snap_c = r["state"]["snapshots"][0]["id"]
mat_n = len(P.get("/api/wz2/items" + q + "&layer=material").json()["items"])
P.put(f"/api/wz2/items/{m800['id']}", json={"layer": "material", "qty": 1.0})
objs_before = P.get("/api/wz2/items" + q + "&layer=object").json()["items"]
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_c, "mode": "layer", "layer": "material", "dry_run": True}).json()
check(d["counts"] == {"add": 0, "remove": 0, "change": 1}, f"предпросмотр отката материалов: {d['counts']}")
P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_c, "mode": "layer", "layer": "material", "reason": "ошибка в количестве"})
m = next(x for x in P.get("/api/wz2/items" + q + "&layer=material").json()["items"] if x["key"] == "труба|гофр.|800")
check(m["qty"] == m800["qty"], f"количество материала вернулось: {m['qty']}")
snap_mig = next(x["id"] for x in st["snapshots"] if x["step"] == 1 and x["kind"] == "migration")
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "mode": "layer", "layer": "norm", "dry_run": True}).json()
check(d["counts"]["remove"] == len(norms2) and d["marks"] == 0, f"откат нормативов к миграции: уберётся {d['counts']['remove']}, отметок 0")
P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_mig, "mode": "layer", "layer": "norm", "reason": "заполним заново"})
check(not P.get("/api/wz2/items" + q + "&layer=norm").json()["items"] and len(P.get("/api/wz2/items" + q + "&layer=material").json()["items"]) == mat_n, "нормативы убраны, материалы не тронуты")
check([(o["id"], o["state"], o["name"]) for o in P.get("/api/wz2/items" + q + "&layer=object").json()["items"]] == [(o["id"], o["state"], o["name"]) for o in objs_before], "объекты не тронуты")

print("7. Откат шагов 3–4 и уведомления прорабу")
for nn in F.get("/api/overview" + q).json().get("wz_notices", []):
    F.post(f"/api/wz2/notices/{nn['id']}/seen" + q)
r = P.post("/api/wizard/match" + q)
check(r.status_code == 200, f"автосопоставление: {r.json().get('counters') if r.status_code == 200 else r.text[:200]}")
st = P.get("/api/wz2" + q).json()
k3 = [x["kind"] for x in st["snapshots"] if x["step"] == 3]
check("before-auto" in k3 and "confirm" in k3, f"снимки шага 3: {k3[:4]}")
snap3 = next(x["id"] for x in st["snapshots"] if x["step"] == 3 and x["kind"] == "confirm")
n_alloc = c0.execute("SELECT COUNT(*) FROM work_alloc WHERE section_id=?", (S,)).fetchone()[0]
with sqlite3.connect(DB) as cx:
    cx.execute("DELETE FROM work_alloc WHERE id IN (SELECT id FROM work_alloc WHERE section_id=? LIMIT 5)", (S,))
d = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap3, "dry_run": True}).json()
check(d["step"] == 3 and d["counts"]["alloc_add"] == 5, f"предпросмотр отката шага 3: {d['counts']}")
check(P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap3}).status_code == 422, "без причины — 422")
r = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap3, "reason": "ошибочно удалены привязки"}).json()
check(c0.execute("SELECT COUNT(*) FROM work_alloc WHERE section_id=?", (S,)).fetchone()[0] == n_alloc, f"привязки сметы восстановлены: {n_alloc}")
st = P.get("/api/wz2" + q).json()
check(st["steps"][3]["status"] in ("work", "stale"), f"шаг 4 нужно завершить заново: {st['steps'][3]['status']}")
ov = F.get("/api/overview" + q).json()
nt = ov.get("wz_notices", [])
check(any("шаг 3" in x["title"] and "ошибочно удалены привязки" in x["text"] for x in nt), f"прораб получил уведомление об откате шага 3: {[x['title'] for x in nt]}")
check(not ov["list_locked"], "прораб по-прежнему может отмечать (перечень не закрыт)")
for x in nt:
    check(F.post(f"/api/wz2/notices/{x['id']}/seen" + q).status_code == 200, "прораб отметил уведомление прочитанным")
check(not F.get("/api/overview" + q).json().get("wz_notices"), "после «Понятно» уведомление у прораба исчезает")
check(any(x["title"].startswith("ПТО откатил") for x in P.get("/api/overview" + q).json().get("wz_notices", [])), "у ПТО своё «прочитано» (уведомление видно)")
objs = P.get("/api/wz2/items" + q + "&layer=object").json()["items"]
victim = next(o for o in objs if o["marks"] > 0 and o["state"] != "rejected")
P.post("/api/wz2/items/bulk" + q, json={"ids": [victim["id"]], "state": "rejected"})
P.post("/api/wz2/confirm" + q, json={"step": "1"})
nt = F.get("/api/overview" + q).json().get("wz_notices", [])
check(any(x["kind"] == "publish" and "выведено из перечня 1" in x["text"] for x in nt), f"уведомление «Перечень обновлён»: {[x['text'][:80] for x in nt]}")
st = P.get("/api/wz2" + q).json()
snap_prev = next(x["id"] for x in st["snapshots"] if x["step"] == 1 and x["kind"] == "before-rollback" or x["step"] == 1 and x["kind"] == "confirm" and x["id"] != st["snapshots"][0]["id"])
r = P.post("/api/wz2/rollback" + q, json={"snapshot_id": snap_prev, "mode": "items", "ids": [victim["id"]], "reason": "переделка по требованию технадзора", "confirm_marks": True})
check(r.status_code == 200, f"откат пункта с отметками: {r.status_code}")
nt = F.get("/api/overview" + q).json().get("wz_notices", [])
check(any(x["kind"] == "rollback" and "технадзора" in x["text"] for x in nt), "уведомление прорабу об откате шага 1 с причиной")
ev_now = c0.execute("SELECT COUNT(*) FROM events").fetchone()[0]
check(ev_now == ev_before, f"отметки прораба не удалены: {ev_now}/{ev_before}")

print("8. Прежние сценарии")
check(F.get("/api/elements" + q).status_code == 200 and P.get("/api/wizard" + q).status_code == 200, "прежние экраны отвечают")
check(P.get("/").status_code == 200, "главная страница")

print()
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else "ОШИБКИ:\n  " + "\n  ".join(fails))
if a.json:
    json.dump({"log": log, "fails": fails}, open(a.json, "w"), ensure_ascii=False, indent=1)
sys.exit(1 if fails else 0)
