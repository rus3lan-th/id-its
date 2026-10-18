"""v.94: глубины колодцев из DXF (ведомость колодцев + таблицы продольных профилей), ручная правка, пересчёт колец — на КОПИИ базы.

    python tests/wells_depth.py --db copy.sqlite3 --files data/files [--json out.json]
"""
import argparse, base64, json, os, shutil, sqlite3, sys, tempfile, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
ap.add_argument("--json", default="")
a = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="idits-wd-")
DB = os.path.join(tmp, "db.sqlite3")
shutil.copy(a.db, DB)
os.environ.update(APP_USER="admin-test", APP_PASSWORD_HASH="1:00:00")
for k, v in dict(APP_DB_PATH=DB, APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "s"), APP_OVERRIDE_DIR=os.path.join(tmp, "o"), APP_BACKUP_DIR=os.path.join(tmp, "b"),
                 APP_PHOTOS_DIR=os.path.join(tmp, "p"), APP_INBOX_DIR=os.path.join(tmp, "i"), APP_DATA_BACKUP_DIR=os.path.join(tmp, "d")).items():
    os.environ[k] = v
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
pid = c0.execute("SELECT project_id FROM sections WHERE id=?", (S,)).fetchone()[0]
P, F = mk("pto.wd", "pto", pid), mk("prorab.wd", "foreman", pid)
q = "?section=" + S

print("1. Разбор таблиц (синтетика)")
prof = [(0, 0, "Номер колодца, точки"), (3, 88, "Отметка низа или"), (2, 73, "Проектная отметка"), (3, 57, "Натурная отметка"),
        (100, 81.5, "60.900"), (104, 81.5, "60.700"), (102, 66.5, "62.000"), (102, 51.7, "62.400"), (95.5, -0.9, "К9-1"),
        (160, 81.5, "60.500"), (162, 66.5, "61.900"), (156, -0.9, "К9-2")]
pr = A.extract_profile_depths(prof)
check(pr.get("К9-1", {}).get("depth_m") == 1.3 and pr["К9-1"]["top_src"] == "проектная", f"профиль: К9-1 = 62,00 − 60,70 = {pr.get('К9-1', {}).get('depth_m')} м")
check(pr.get("К9-2", {}).get("depth_m") == 1.4, f"профиль: К9-2 = {pr.get('К9-2', {}).get('depth_m')} м")
sh = [(0, 0, "Полная глубина"), (-68, 1.5, "N колодца")] + [(x, -7.5, str(n)) for n, x in ((1, -70), (5, -15), (6, 0), (7, 15), (12, 76), (19, 126), (24, 161), (29, 195), (33, 226))] + \
     [(80.5, 0, "ПН-15"), (129.7, 0, "КС.15.9"), (165.1, -1.4, "2ПП15-1"), (200.4, 0, "КО-6"), (230.4, 0, "Тип люка"),
      (-74, -14, "К9-1"), (-4, -14.7, "2150"), (11, -14.7, "1800"), (-19, -14.7, "1500"), (77.8, -14, "1"), (127.6, -14, "2"), (162.6, -15, "1"), (197.6, -14, "1"), (225.4, -14.2, "ТМ")]
ws = A.extract_wells_sheet_dxf(sh)
w1 = ws.get("К9-1") or {}
check(w1.get("depth_m") == 2.15 and w1.get("comp") == {"ПН15": 1, "КС15.9": 2, "2ПП15-1": 1, "КО6": 1} and w1.get("comp_ok") and w1.get("hatch") == "ТМ", f"ведомость: {w1}")

print("2. Глубины Грушевской из DXF")
r = P.get("/api/wz2/wells/depths" + q).json()
W = {w["key"]: w for w in r["wells"]}
S_ = r["summary"]
ring = [k for k, w in W.items() if "Дк" not in k]
check(S_["with_depth"] >= S_["wells"] - 1 and S_["wells"] >= 40, f"с глубиной {S_['with_depth']} из {S_['wells']} (по источникам {S_['by_src']})")
for k, exp in (("К2-12", 2.15), ("К2-11", 2.0), ("К2-9", 2.09), ("К2-1", 1.75), ("К2-2", 3.05), ("К2-3", 2.48)):
    w = W.get(k)
    if w is None:
        continue
    check(w.get("depth_m") is not None and abs(w["depth_m"] - exp) <= 0.011, f"{k}: {w.get('depth_m')} м — {w.get('src')}; {w.get('where', '')[:90]}")
for k in ("К2-44", "К2-47", "К2-50", "К2.1-16"):
    w = W.get(k)
    if w is None:
        continue
    check(w.get("depth_m") and w["src"].startswith("продольный профиль") and "лоток" in w["where"], f"{k} (нет в ведомости): {w.get('depth_m')} м, {w.get('conf')}; {w.get('where', '')[-90:]}")
agree = [k for k, w in W.items() if w["src"].startswith("ведомость") and any(x["src"].startswith("продольный") and abs(x["depth_m"] - w["depth_m"]) <= 0.03 for x in w["alts"])]
both = [k for k, w in W.items() if w["src"].startswith("ведомость") and any(x["src"].startswith("продольный") for x in w["alts"])]
check(len(agree) >= 0.9 * len(both) > 0, f"профиль совпадает с ведомостью (±3 см): {len(agree)} из {len(both)}")
check(all(w["check"] for k, w in W.items() if w["conf"] == "low"), "низкая достоверность → «проверить»")

print("3. Ведомость материалов: состав по ведомости колодцев, кольца по глубине")
f = P.post("/api/wz2/materials/fill" + q).json()
print("    ", f["message"])
mats = {m["key"]: m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"]}
check(f["depth"]["sheet_comp"] >= 20 and "состав по ведомости колодцев DXF" in f["message"], f"состав по ведомости: {f['depth']['sheet_comp']}")
check(all(v["no_depth"] == 0 for v in f["rings"].values()), "колец без глубины нет: " + ", ".join(f"{k} {v['wells']}" for k, v in f["rings"].items()))
ko = mats.get("жби|КО6", {})
check((ko.get("qty") or 0) > f["depth"]["sheet_comp"], f"КО6 по ведомости: {ko.get('qty')} (шаблон давал 1 на колодец)")
check("по ведомости колодцев проекта (DXF)" in (mats.get("жби|КС15.9", {}).get("note") or ""), "КС15.9: источник — ведомость колодцев")
p = P.get("/api/wz2/params" + q).json()
check(p["depth_info"].get("with_depth") == S_["with_depth"], "параметры: сводка глубин")

print("4. Ручная правка глубины")
gen = next(k for k, w in W.items() if not w["sheet"] and w["src"].startswith("продольный") and "Дк" not in k)
k9 = mats.get("жби|КС15.9", {}).get("qty") or 0
check(F.put("/api/wz2/wells/depths" + q, json={"key": gen, "depth_m": 5}).status_code == 403, "прораб не меняет глубину")
check(P.put("/api/wz2/wells/depths" + q, json={"key": gen, "depth_m": 50}).status_code == 422, "глубина 50 м отклоняется")
check(P.put("/api/wz2/wells/depths" + q, json={"key": "НЕТ-1", "depth_m": 2}).status_code == 404, "чужой колодец — 404")
r = P.put("/api/wz2/wells/depths" + q, json={"key": gen, "depth_m": 5.0, "note": "исполнительная съёмка"})
check(r.status_code == 200, f"{gen}: 5 м вручную — {r.json().get('message')}")
w = {x["key"]: x for x in P.get("/api/wz2/wells/depths" + q).json()["wells"]}[gen]
check(w["depth_m"] == 5.0 and w["src"] == "вручную" and w["conf"] == "manual" and w["alts"] and "исполнительная съёмка" in w["where"], f"ручная правка главнее чертежа: {w['where'][:80]}")
P.post("/api/wz2/materials/fill" + q)
m2 = {m["key"]: m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"]}
check((m2.get("жби|КС15.9", {}).get("qty") or 0) > k9, f"кольца пересчитаны: КС15.9 {k9} → {m2.get('жби|КС15.9', {}).get('qty')}")
P.put("/api/wz2/wells/depths" + q, json={"key": gen, "depth_m": None})
w = {x["key"]: x for x in P.get("/api/wz2/wells/depths" + q).json()["wells"]}[gen]
check(w["src"] != "вручную" and not w["manual"], "ручная правка снята — снова по чертежу")
aud = c0.execute("SELECT COUNT(*) FROM audit WHERE action='well_depth'").fetchone()[0]
check(aud == 2, f"в журнале аудита: {aud} записи (правка и снятие)")

print("5. Колодец без данных — отмечен")
c0.execute("INSERT INTO wz_items (id,section_id,layer,key,name,kind,unit,qty,note,data_json,origin,state,scope,sort,updated_by,updated_at) "
            "SELECT ?,section_id,'object','К2-999','Колодец К2-999','Колодец',unit,qty,'','{}',origin,state,scope,sort,updated_by,updated_at FROM wz_items WHERE section_id=? AND layer='object' AND key=?",
            (str(uuid.uuid4()), S, gen))
c0.commit()
r = P.get("/api/wz2/wells/depths" + q).json()
check("К2-999" in r["summary"]["no_depth_keys"], "К2-999 без глубины — в списке «без глубины»")
f = P.post("/api/wz2/materials/fill" + q).json()
check(f["rings"]["КС15"]["no_depth"] == 1 and "глубина не указана у 1" in f["message"], f"в ведомости отмечено: {f['rings']['КС15']}")
check(P.put("/api/wz2/params" + q, json={"well_cfg": {"tol_m": 0.05, "tray_m": 0.0, "use_sheet_comp": 0}}).status_code == 200, "состав по ведомости можно выключить")
f = P.post("/api/wz2/materials/fill" + q).json()
check(f["depth"]["sheet_comp"] == 0 and f["rings"]["КС15"]["wells"] >= len([k for k in W if "Дк" not in k and "КГ" not in k]), f"выключено: все Ø1500 по шаблону, кольца по глубине ({f['rings']['КС15']['wells']})")

print("\nИТОГ:", "OK" if not fails else f"{len(fails)} FAIL")
if a.json:
    json.dump({"fails": fails, "log": log}, open(a.json, "w"), ensure_ascii=False, indent=1)
sys.exit(1 if fails else 0)
