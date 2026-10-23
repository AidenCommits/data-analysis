import sqlite3, glob, datetime as dt
import openpyxl

# ---- CONFIG ----
BREAKAGE_DB = r"data\raw\breakage.db"
SEARCH_XLSX = glob.glob(r"data\**\Search_Report_*.xlsx", recursive=True)[0]
LIVE_DB     = r"incident_log.db"
KIND_BREAKAGE = "Breakage"
KIND_SEARCH   = "Lost Item"
CATEGORY = {
    "trophy": "Personal items",
    "x mas decor hanging star": "Art / decor",
    "wardrobe box-content from entertainment couch and coffee table": "Personal items",
    "wood chair with fabric seat": "Furniture",
    "side table": "Furniture",
    "desk": "Furniture",
    "work desk": "Furniture",
    "sewing machine": "Appliances",
    "vacuum": "Appliances",
}
# ----------------

EDT = dt.timezone(dt.timedelta(hours=-4))
now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
clean = lambda s: " ".join((s or "").split())
RES = {"REPAIR": "Repair", "REPLACE": "Replace"}

src = sqlite3.connect(f"file:{BREAKAGE_DB}?mode=ro", uri=True)
dst = sqlite3.connect(LIVE_DB)
RATE = dst.execute("select value from settings where key='labor_rate'").fetchone()[0]
MILE = dst.execute("select value from settings where key='mileage_rate'").fetchone()[0]
print("Using labor rate:", RATE)
print("Search file:", SEARCH_XLSX)

added_b = 0
for item_id, job, desc, room, ctype, cost, seen in src.execute(
        "select item_id, job_name, description, room, cost_type, current_cost, first_seen from damaged_items"):
    day = dt.datetime.fromisoformat(seen).astimezone(EDT).date().isoformat()
    cost = round(float(cost), 2)
    d = clean(desc)
    cat = CATEGORY.get(d.lower(), "Other")
    note = f"Baseline import. {d}. Room: {clean(room)}. Date is capture date, not loss date."
    cur = dst.execute(
        "insert or ignore into incidents (incident_date, kind, job_id, item, resolution, other_cost, "
        "labor_rate, mileage_rate, est_cost, notes, reported_by, created_at, voided, source_ref) "
        "values (?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
        (day, KIND_BREAKAGE, clean(job), cat, RES.get(ctype), cost,
         RATE, MILE, cost, note, "baseline import", now, item_id))
    added_b += cur.rowcount

added_s = 0
ws = openpyxl.load_workbook(SEARCH_XLSX, data_only=True)["Search History"]
for d, day, emp, rate, mins, hrs, cost, rid in ws.iter_rows(min_row=5, values_only=True):
    if not isinstance(d, dt.datetime):
        continue
    note = f"Baseline import. {emp} employees x {mins} min. Original rate ${rate}/hr, original cost ${cost}."
    cur = dst.execute(
        "insert or ignore into incidents (incident_date, kind, hours, labor_rate, mileage_rate, est_cost, "
        "notes, reported_by, created_at, voided, source_ref) values (?,?,?,?,?,?,?,?,?,0,?)",
        (d.date().isoformat(), KIND_SEARCH, hrs, RATE, MILE, round(hrs * RATE, 2),
         note, "baseline import", now, rid))
    added_s += cur.rowcount

dst.commit()
print("Added breakage:", added_b, " Added search:", added_s)
for r in dst.execute("select kind, count(*), round(sum(est_cost),2), round(sum(coalesce(hours,0)),2) "
                     "from incidents where voided=0 group by kind"):
    print(r)