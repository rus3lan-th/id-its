"""Мастер 2.0, v.92: потери по сметным нормативам, кольца колодцев по глубине, шаблоны колодцев без типа, справка в Google Таблицу — на КОПИИ базы.

    python tests/wizard_f3.py --db copy.sqlite3 --files data/files --gesn norm_records.sqlite3 [--json out.json]
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
tmp = tempfile.mkdtemp(prefix="idits-wzf3-")
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
A._wells_dxf = lambda fid: {"sheet": {}, "profile": {}}   # v.94: глубины из DXF проверяет tests/wells_depth.py; здесь — путь «глубина только из перечня»
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
P, F = mk("pto.f3", "pto", pid), mk("prorab.f3", "foreman", pid)
q = "?section=" + S
gesn_n = c0.execute("SELECT COUNT(*) FROM norm_records").fetchone()[0]


print("1. Кольца по глубине (расчёт)")
for d, top, exp in ((2.15, 0.34, {"9": 2}), (2.24, 0.34, {"9": 2, "3": 1}), (2.5, 0.34, {"9": 2, "6": 1}), (2.9, 0.34, {"9": 3}), (1.2, 0.26, {"9": 1}), (0.5, 0.34, {"3": 1})):
    got = A._wz2_rings(d, top, 0.05, 0.0)
    check(got == exp, f"глубина {d} м, верх {top} м → {got}")

print("2. Ведомость материалов: кольца, колодцы без типа, таблица колодцев")
r = P.post("/api/wz2/materials/fill" + q).json()
print("    ", r["message"])
mats = {m["key"]: m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"]}
els = {e[0]: e[1] for e in c0.execute("SELECT code, planned_note FROM elements WHERE section_id=? AND kind LIKE 'Колод%' AND enabled=1", (S,))}
import re as _re
deep = {k: int(_re.search(r"глубина (\d+) мм", n).group(1)) for k, n in els.items() if n and _re.search(r"глубина (\d+) мм", n)}
exp = {}
for k, d in deep.items():
    top = 0.26 if "Дк" in k else 0.34
    for h, n in A._wz2_rings(d / 1000, top).items():
        ser = "КС10" if "Дк" in k else "КС15"
        exp[f"жби|{ser}.{h}"] = exp.get(f"жби|{ser}.{h}", 0) + n
check(bool(exp) and all((mats.get(k, {}).get("qty") or 0) == v for k, v in exp.items()), f"кольца по глубинам {len(deep)} колодцев: " + ", ".join(f"{k.split('|')[1]} {v}" for k, v in sorted(exp.items())))
check(r["rings"]["КС15"]["no_depth"] > 0 and "глубина не указана" in r["message"], f"колодцы без глубины отмечены: КС15 — {r['rings']['КС15']['no_depth']} из {r['rings']['КС15']['wells']}")
gen = [k for k in els if c0.execute("SELECT kind FROM elements WHERE section_id=? AND code=?", (S, k)).fetchone()[0] == "Колодец"]
pn = mats["жби|ПН15"]
d = json.loads(pn["data_json"]) if isinstance(pn.get("data_json"), str) else (pn.get("data") or {})
by = {b[0] for b in d.get("by", [])}
check(not gen or (all(g in by for g in gen) and "тип не указан — принят" in d.get("how", "")), f"колодцы без типа ({len(gen)}) получили шаблон Ø1500 по сети: {d.get('how', '')[:110]}")
check(r["rings"]["КС15"]["assumed"] == len(gen), f"принятых по сети: {r['rings']['КС15']['assumed']}")
check("жби|КО6" in mats and mats["жби|КО6"]["qty"] == pn["qty"], f"опорное кольцо КО6 в шаблоне Ø1500: {mats.get('жби|КО6', {}).get('qty')}")
# раздел с таблицей колодцев проекта (НК1)
S2 = "ec38b934-9337-49ca-8f61-efc36682c7bc"
P2 = mk("pto2.f3", "pto", c0.execute("SELECT project_id FROM sections WHERE id=?", (S2,)).fetchone()[0])
r2 = P2.post("/api/wz2/materials/fill?section=" + S2).json()
m2 = {m["key"]: m for m in P2.get("/api/wz2/items?section=" + S2 + "&layer=material").json()["items"]}
tab = {}
for (n,) in c0.execute("SELECT note FROM wz_items WHERE section_id=? AND layer='object' AND note LIKE '%Состав:%' AND state<>'rejected' AND scope=''", (S2,)):
    for mk_, qn in _re.findall(r"([A-Za-zА-Яа-я0-9. -]+?)×(\d+)", n.split("Состав:")[-1].split("·")[0]):
        k = A._wz2_mat_key(mk_.strip())
        if k:
            tab[k[0]] = tab.get(k[0], 0) + int(qn)
check(bool(tab) and all((m2.get(k, {}).get("qty") or 0) >= v for k, v in tab.items()), f"НК1: состав колодцев из таблицы проекта — " + ", ".join(f"{k.split('|')[-1]} {v}" for k, v in sorted(tab.items())))
check("КС10.6" in str(sorted(m2)) and "по таблице колодцев проекта" in (m2.get("жби|КС10.6", {}).get("note") or ""), "кольца по таблице, без пересчёта по глубине")

print("3. Потери по сметным нормативам")
d = P.post("/api/wz2/materials/compare" + q + "&dry_run=true").json()
rows = {x["key"]: x for x in d["rows"]}
x8 = rows["труба|гофр.|800"]
check(x8["loss_pct"] == 2.5 and "ГЭСН 23-01-033-03" in x8["loss_src"] and abs(x8["need"] - round(x8["project"] * 1.025, 3)) < 0.01, f"гофр. Ø800: {x8['loss_pct']} % — {x8['loss_src']}; нужно {x8['need']} м")
x110 = rows.get("труба|ПЭ|110")
check(x110 and x110["loss_pct"] == 2.5 and "РДС 82-202-96" in x110["loss_src"], f"ПЭ Ø110 (в смете нет): {x110 and x110['loss_pct']} % — {x110 and x110['loss_src']}")
x32 = rows.get("труба|ПЭ|32")
print(f"     ПЭ Ø32 (работа 16-04-002-03): {x32 and x32['loss_pct']} % — {x32 and x32['loss_src']}")
xj = rows["жби|ПН15"]
check(xj["loss_pct"] == 0 and "РДС 82-202-96 п. 5.2" in xj["loss_src"], f"ЖБИ 0 %: {xj['loss_src'][:80]}")
xl = rows["люк"]
check(xl["loss_pct"] == 0 and "ГЭСН 23-03" in xl["loss_src"], "люки 0 % (ГЭСН 23-03 «П»)")
p0 = P.get("/api/wz2/params" + q).json()
check(len(p0["losses"]) == len(d["rows"]) and all(x["src"] for x in p0["losses"]), f"у каждого значения потерь есть источник: {len(p0['losses'])} позиций")
r = P.put("/api/wz2/params" + q, json={"losses": {"труба|гофр.|800": 3}, "loss_src": {"труба|гофр.|800": "решение ПТО: раструбные отрезки"}})
check(r.status_code == 200, "исправление потерь 3 %")
x8b = {x["key"]: x for x in P.post("/api/wz2/materials/compare" + q + "&dry_run=true").json()["rows"]}["труба|гофр.|800"]
lp = {x["key"]: x for x in P.get("/api/wz2/params" + q).json()["losses"]}["труба|гофр.|800"]
check(x8b["loss_pct"] == 3 and x8b["loss_edited"] and lp["src"].startswith("решение ПТО") and lp["default_pct"] == 2.5 and lp["by"] == "pto.f3", f"исправленное помечено, норматив сохранён для справки: {lp['src']} / норматив {lp['default_pct']} %")
check(P.put("/api/wz2/params" + q, json={"losses": {"труба|гофр.|800": 150}}).status_code == 422, "потери больше 100 % отклоняются")
check(F.put("/api/wz2/params" + q, json={"losses": {"труба|гофр.|800": 1}}).status_code == 403, "прораб не меняет потери")
P.put("/api/wz2/params" + q, json={"losses": {"труба|гофр.|800": None}})
x8c = {x["key"]: x for x in P.post("/api/wz2/materials/compare" + q + "&dry_run=true").json()["rows"]}["труба|гофр.|800"]
check(x8c["loss_pct"] == 2.5 and not x8c["loss_edited"], "«норматив» возвращает 2,5 % по ГЭСН")

print("4. Сверка: проект неполный, замечания")
r = P.post("/api/wz2/materials/compare" + q).json()
rem = {x["key"]: x for x in P.get("/api/wz2/remarks" + q).json()["remarks"]}
k9 = rem.get("mat|жби|КС15.9")
check(k9 and k9["type"] == "missing" and "глубина в проекте не указана" in k9["text"] and k9["labor_hours"] is None, f"КС15.9: {k9 and k9['text'][:150]}")
r8 = rem.get("mat|труба|гофр.|800")
check(r8 and "ГЭСН 23-01-033-03" in r8["text"], f"в тексте замечания основание потерь: {r8 and r8['text'][:170]}")
for x in r["rows"]:
    print(f"     {x['name'][:34]:34} проект {x['project']!s:>8} +{x['loss_pct']}% смета {x['estimate']!s:>8} {r['statuses'][x['status']]}")

print("5. Шаблоны и параметры колодцев")
p = P.get("/api/wz2/params" + q).json()
bad = [dict(p["mat_templates"][0], match="(")]
check(P.put("/api/wz2/params" + q, json={"mat_templates": bad}).status_code == 422, "ошибка в условии шаблона отклоняется")
tp = [dict(t) for t in p["mat_templates"]]
for t in tp:
    if t["label"] == "Колодец ж/б Ø1500":
        t["top_m"] = 0.9
check(P.put("/api/wz2/params" + q, json={"mat_templates": tp, "well_cfg": {"tol_m": 0.05, "tray_m": 0.0}}).status_code == 200, "шаблон Ø1500: верх 0,9 м")
P.post("/api/wz2/materials/fill" + q)
m3 = {m["key"]: m for m in P.get("/api/wz2/items" + q + "&layer=material").json()["items"]}
check((m3.get("жби|КС15.9", {}).get("qty") or 0) < (mats.get("жби|КС15.9", {}).get("qty") or 0), f"кольца пересчитаны: КС15.9 {mats.get('жби|КС15.9', {}).get('qty')} → {m3.get('жби|КС15.9', {}).get('qty')}")
check(P.put("/api/wz2/params" + q, json={"well_cfg": {"tol_m": 5}}).status_code == 422, "допуск больше 1 м отклоняется")

print("6. Справка → Google Таблица")
t = P.get("/api/wz2/remarks/export.tsv" + q)
lines = t.text.split("\n")
check(t.status_code == 200 and lines[1].split("\t")[0] == "№" and len(lines[1].split("\t")) == 12 and lines[2].startswith("З-001\t"), f"TSV для вставки в Google Таблицу: {len(lines)} строк, 12 колонок")
nrem = len(P.get("/api/wz2/remarks" + q).json()["remarks"])
check(len(lines) == nrem + 4 and all(l.count("\t") <= 11 for l in lines), f"строк по замечаниям {len(lines) - 4} = {nrem}; переносы и табуляции внутри ячеек убраны")
check(F.get("/api/wz2/remarks/export.tsv" + q).status_code in (200, 403), "прораб: справка — только чтение")
check(P.get("/").status_code == 200 and "wz2ToGoogle" in P.get("/").text, "кнопка «Справка → в Google Таблицу» на странице")
ev_now = c0.execute("SELECT COUNT(*) FROM events").fetchone()[0]
check(ev_now == ev_before, f"отметки прораба не тронуты: {ev_now}/{ev_before}")

print()
print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else "ОШИБКИ:\n  " + "\n  ".join(fails))
if a.json:
    json.dump({"log": log, "fails": fails}, open(a.json, "w"), ensure_ascii=False, indent=1)
sys.exit(1 if fails else 0)
