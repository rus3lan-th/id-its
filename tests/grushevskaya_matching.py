"""Проверка автосопоставления сметы на копии базы (Грушевская, раздел РК-ДП-812-25-26-НВК).

Запуск (только на КОПИИ базы, рабочую базу не трогает):
    python tests/grushevskaya_matching.py --db /workspace/idits-data/ro.sqlite3 --files /workspace/idits-data/data/files [--app /path/to/dir_with_app.py]

Скрипт копирует базу во временный каталог, запускает «Предложить автоматически» (_alloc_auto_core)
и печатает: работы x/55, материалы x/60, объекты x/86, колодцы, двойной учёт, межсетевые привязки.
"""
import argparse, collections, json, os, re, shutil, sys, tempfile

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
ap.add_argument("--section", default="bf3b0d8d-6d68-4b78-821f-1fc72292a478")
ap.add_argument("--json", default="")
ap.add_argument("--all", action="store_true", help="сводка по всем разделам")
a = ap.parse_args()

tmp = tempfile.mkdtemp(prefix="idits-test-")
shutil.copy(a.db, os.path.join(tmp, "db.sqlite3"))
for k, v in dict(APP_DB_PATH=os.path.join(tmp, "db.sqlite3"), APP_FILES_DIR=a.files, APP_SEEDS_DIR=os.path.join(tmp, "seeds"),
                 APP_OVERRIDE_DIR=os.path.join(tmp, "ov"), APP_BACKUP_DIR=os.path.join(tmp, "ab"), APP_PHOTOS_DIR=os.path.join(tmp, "ph"),
                 APP_INBOX_DIR=os.path.join(tmp, "in"), APP_DATA_BACKUP_DIR=os.path.join(tmp, "bk")).items():
    os.environ[k] = v
sys.path.insert(0, os.path.abspath(a.app))
import app as A  # noqa: E402

A.init_db()


def net_of(code: str) -> str:
    m = re.match(r"\s*(К\d+(?:\.\d+)?)\s?[-–]", code or "")
    return m.group(1) if m else ""


def chap_net(ch: str) -> str:
    m = re.findall(r"(?<![\w.])(К\d+(?:\.\d+)?)(?![\w.])", ch or "")
    return m[-1] if m else ""


def run(sec_id: str) -> dict:
    with A.db() as c:
        sec = A._section(c, sec_id)
        res = A._alloc_auto_core(c, sec, "test", 0)
        kinds = {r[0]: r[1] for r in c.execute("SELECT id, kind FROM elements WHERE section_id=?", (sec_id,))}
        codes = {r[0]: r[1] for r in c.execute("SELECT id, code FROM elements WHERE section_id=?", (sec_id,))}
    d = A.alloc_list(sec_id)
    rows, els = d["rows"], d["elements"]
    W = [r for r in rows if not r["mat"]]
    M = [r for r in rows if r["mat"]]
    full = lambda L: sum(1 for r in L if r["alloc"] and abs(r["free"]) <= 0.001)
    part = lambda L: sum(1 for r in L if r["alloc"] and abs(r["free"]) > 0.001)
    by_el = collections.defaultdict(list)
    for r in rows:
        for x in r["alloc"]:
            by_el[x["element_id"]].append(r)
    wells = [e for e in els if kinds.get(e["id"], "").startswith("Колодец")]
    # двойной учёт: один объект получил одну и ту же работу (шифр + наименование + ед.) из разных глав/подразделов сметы
    dbl = 0
    for eid, rs in by_el.items():
        grp = collections.defaultdict(set)
        for r in rs:
            if not r["mat"]:
                grp[(r["code"], r["name"], r["units"])].add((r["chapter"], r.get("sub", "")))
        dbl += sum(len(v) - 1 for v in grp.values() if len(v) > 1)
    cross = 0
    for eid, rs in by_el.items():
        en = net_of(codes.get(eid, ""))
        cross += sum(1 for r in rs if en and chap_net(r["chapter"]) and chap_net(r["chapter"]) != en)
    out = {"works": f"{full(W)}/{len(W)}", "works_partial": part(W), "materials": f"{full(M)}/{len(M)}", "materials_partial": part(M),
           "objects": f"{len(by_el)}/{len(els)}", "wells": f"{sum(1 for e in wells if e['id'] in by_el)}/{len(wells)}",
           "double_count_pairs": dbl, "cross_net_links": cross, "needs_pto": res.get("needs_pto"), "message": res.get("message", "")[:300]}
    return out, d


if a.all:
    with A.db() as c:
        secs = [(r[0], r[1]) for r in c.execute("SELECT id, code FROM sections")]
    for sid, code in secs:
        try:
            o, _ = run(sid)
            print(code, json.dumps({k: v for k, v in o.items() if k != "message"}, ensure_ascii=False))
        except Exception as ex:  # noqa: BLE001
            print(code, "ERR", ex)
else:
    o, d = run(a.section)
    print(json.dumps(o, ensure_ascii=False, indent=1))
    if a.json:
        json.dump(d, open(a.json, "w"), ensure_ascii=False, indent=1, default=str)
shutil.rmtree(tmp, ignore_errors=True)
