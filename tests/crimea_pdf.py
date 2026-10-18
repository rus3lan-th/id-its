"""v.95: PDF-профили с гофр. трубами «DN/OD…», испорченный текстовый слой, объекты чужих сетей и двойной счёт между разделами — на КОПИИ базы (Озон Крым).

    python tests/crimea_pdf.py --db copy.sqlite3 --files data/files
"""
import argparse, base64, os, shutil, sqlite3, sys, tempfile, uuid

ap = argparse.ArgumentParser()
ap.add_argument("--db", required=True)
ap.add_argument("--files", required=True)
ap.add_argument("--app", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
a = ap.parse_args()
tmp = tempfile.mkdtemp(prefix="idits-cr-")
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
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


c0 = sqlite3.connect(DB)
pid = c0.execute("SELECT id FROM projects WHERE object_name='Озон Крым'").fetchone()
if not pid:
    print("нет проекта «Озон Крым» в базе — пропуск")
    sys.exit(0)
pid = pid[0]
secs = {r[0]: r[1] for r in c0.execute("SELECT code,id FROM sections WHERE project_id=?", (pid,))}
with c0:
    uid = str(uuid.uuid4())
    c0.execute("INSERT INTO dict_users (id,name,login,app_role,active,created_at,pw_hash) VALUES (?,?,?,?,1,?,?)", (uid, "pto", "pto.cr", "pto", A.now(), A._hash_pw("pw")))
    c0.execute("INSERT INTO team (id,project_id,name,position,app_role,sort_order,created_at,login,user_id,valid_from,valid_to) VALUES (?,?,?,?,?,0,?,?,?,'','')",
               (str(uuid.uuid4()), pid, "pto", "pto", "pto", A.now(), "pto.cr", uid))
P = TestClient(A.app)
P.headers["Authorization"] = "Basic " + base64.b64encode(b"pto.cr:pw").decode()

print("1. Разбор труб")
p = A._wz2_pipe("Гофрированная Труба гофрир.двустенная DN/ОD 160 SN8")      # кириллическая «О»
check(p and p["type"] == "гофр." and p["dn"] == 160, f"«DN/ОD» с кириллической О: {p}")
fid = c0.execute("SELECT id FROM source_files WHERE project_id=? AND name LIKE '%НК2+от%'", (pid,)).fetchone()
if fid:
    r = A._pdf_pipes(fid[0])
    dn = [x for x in r["pipes"] if x["dn"]]
    check(len(r["pipes"]) >= 40 and len(dn) == len(r["pipes"]) and all("DN/OD" in x["spec"] for x in dn), f"НК2: участков {len(r['pipes'])}, с диаметром {len(dn)} ({dn[0]['spec'] if dn else ''})")
fid2 = c0.execute("SELECT id FROM source_files WHERE project_id=? AND name LIKE '%НВ2+от%'", (pid,)).fetchone()
if fid2:
    check(A._pdf_pipes(fid2[0])["text_broken"], "НВ2: испорченный текстовый слой PDF распознан")
    check(not A._pdf_pipes(fid[0])["text_broken"], "НК2: нормальный текстовый слой")

print("2. Ведомость материалов по PDF")
if "НК2" in secs:
    f = P.post("/api/wz2/materials/fill?section=" + secs["НК2"]).json()
    mats = {m["key"]: m for m in P.get("/api/wz2/items?section=" + secs["НК2"] + "&layer=material").json()["items"]}
    g = [k for k in mats if k.startswith("труба|гофр.|")]
    check(len(g) >= 5 and "подписи профиля PDF" in (mats[g[0]].get("note") or ""), f"НК2: гофр. трубы по подписям профиля — {', '.join(k.split('|')[-1] for k in sorted(g))}")
    check("участков без длины или диаметра" not in f["message"], f"НК2: {f['message'][:160]}")
if "НВ2" in secs:
    f = P.post("/api/wz2/materials/fill?section=" + secs["НВ2"]).json()
    check("испорченным текстовым слоем" in f["message"], f"НВ2: предупреждение — {f['message'][-120:]}")

print("3. Чужие сети и двойной счёт")
for code in secs:
    if code.endswith("НВ1"):
        fd = P.post("/api/wizard/analyze?section=" + secs[code]).json()["found"]
        check(fd["list_other_nets"].get("К1", 0) > 0 and fd["dup_other_sections"].get("НК2", 0) > 0, f"{code}: чужие сети {fd['list_other_nets']}, те же шифры {fd['dup_other_sections']}")
if "НК1" in secs:
    fd = P.post("/api/wizard/analyze?section=" + secs["НК1"]).json()["found"]
    check(not fd["list_other_nets"], f"НК1: чужих сетей нет ({fd['list_other_nets']})")

print("\nИТОГ:", "OK" if not fails else f"{len(fails)} FAIL")
sys.exit(1 if fails else 0)
