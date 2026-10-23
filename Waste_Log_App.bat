@echo off & (where pyw >nul 2>&1 && start "" pyw -x "%~f0" || (where pythonw >nul 2>&1 && start "" pythonw -x "%~f0" || (echo Python was not found. Install it from python.org and tick Add Python to PATH. & pause))) & exit /b
"""
Waste Incident Log - Phase 1
Local SQLite app for logging waste events (recleans, repeat trips, lost items, breakage)
taken from coworkers. Data is stored in incident_log.db next to this file.
Run:  python waste_log_app.py   (tkinter and sqlite3 ship with standard Python)
"""
import csv
import os
import sqlite3
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "incident_log.db")

DEFAULT_SETTINGS = {"labor_rate": 45.00, "mileage_rate": 0.70}   # edit in the Settings tab
DEFAULT_LISTS = {
    "people": ["Tech 1", "Tech 2", "Tech 3"],          # use the exact Contents Track usernames
    "job_types": ["Water", "Fire", "Mold", "Cleaning", "Contents"],
    "reclean_reasons": ["Missed area", "Quality issue", "Customer complaint", "Wrong product", "Other"],
    "breakage_causes": ["Dropped / mishandled", "Packing / transport", "Cleaning process",
                        "Our equipment caused it", "Storage", "Pre-existing / disputed", "Other"],
    "damaged_items": ["Electronics", "Furniture", "Clothing / textiles", "Appliances", "Art / decor",
                      "Documents / photos", "Glassware / dishes", "Personal items", "Company equipment", "Other"],
    "items": ["Air mover", "Dehumidifier", "Extractor", "Hose", "Ladder", "PPE", "Chemicals", "Other"],
    "resolutions": ["Repair", "Replace", "Claim / reimbursement", "Pending"],
}
LIST_LABELS = {
    "people": "People (Contents Track usernames)",
    "job_types": "Job types", "reclean_reasons": "Reclean reasons",
    "breakage_causes": "Breakage / damage causes", "damaged_items": "Breakage: what was damaged (suggestions)",
    "items": "Items (forgotten / lost)", "resolutions": "Breakage resolutions",
}

TAB_LABELS = {"Breakage": "Breakage / Damage"}   # stored kind stays "Breakage"

# kind -> list of (column, label, widget, lookup list, required)
FIELDS = {
    "Reclean": [
        ("job_id", "Job ID", "entry", None, True),
        ("job_type", "Job type", "combo", "job_types", True),
        ("tech", "Tech / crew lead", "combo", "people", True),
        ("reason", "Reason code", "combo", "reclean_reasons", True),
        ("hours", "Extra labor hours", "number", None, True),
        ("supplies_cost", "Supplies cost ($)", "number", None, False),
        ("miles", "Extra miles", "number", None, False),
        ("notes", "Notes", "text", None, False),
    ],
    "Repeat Trip": [
        ("job_id", "Job ID", "entry", None, True),
        ("job_type", "Job type", "combo", "job_types", True),
        ("item", "Item forgotten", "combo", "items", True),
        ("tech", "Who packed the vehicle", "combo", "people", True),
        ("hours", "Extra drive + wait hours", "number", None, True),
        ("miles", "Extra miles", "number", None, True),
        ("notes", "Notes", "text", None, False),
    ],
    "Lost Item": [
        ("item", "Item", "combo", "items", True),
        ("last_location", "Last known location", "entry", None, True),
        ("hours", "Time spent searching (hours)", "number", None, True),
        ("repurchased", "Re-purchased?", "check", None, False),
        ("other_cost", "Re-purchase cost ($)", "number", None, False),
        ("notes", "Notes", "text", None, False),
    ],
    "Breakage": [
        ("job_id", "Job ID (if any)", "entry", None, False),
        ("item", "What was damaged (pick or type)", "combo_edit", "damaged_items", True),
        ("tech", "Tech / crew lead", "combo", "people", True),
        ("reason", "Cause", "combo", "breakage_causes", True),
        ("resolution", "How it was resolved", "combo", "resolutions", True),
        ("other_cost", "Cost to us ($: repair / replace / claim)", "number", None, True),
        ("hours", "Labor hours spent on it", "number", None, False),
        ("notes", "Notes (describe the item and the damage)", "text", None, False),
    ],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lookups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, list_name TEXT NOT NULL, value TEXT NOT NULL,
    UNIQUE (list_name, value));
CREATE TABLE IF NOT EXISTS weekly_jobs (week_start TEXT PRIMARY KEY, total_jobs INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_date TEXT NOT NULL, kind TEXT NOT NULL,
    job_id TEXT, job_type TEXT, tech TEXT, reason TEXT, item TEXT, last_location TEXT, resolution TEXT,
    hours REAL, miles REAL, supplies_cost REAL, other_cost REAL, downtime_days REAL, repurchased INTEGER,
    notes TEXT, reported_by TEXT,
    labor_rate REAL, mileage_rate REAL, est_cost REAL,
    created_at TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0);
"""
INCIDENT_COLS = ["incident_date", "kind", "job_id", "job_type", "tech", "reason", "item", "last_location",
                 "resolution", "hours", "miles", "supplies_cost", "other_cost", "downtime_days", "repurchased",
                 "notes", "reported_by", "labor_rate", "mileage_rate", "est_cost", "created_at"]


class DB:
    def __init__(self, path):
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            self.con.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))
        for name, vals in DEFAULT_LISTS.items():
            n = self.con.execute("SELECT COUNT(*) FROM lookups WHERE list_name=?", (name,)).fetchone()[0]
            if n == 0:
                for v in vals:
                    self.con.execute("INSERT INTO lookups (list_name, value) VALUES (?, ?)", (name, v))
        self.con.commit()

    def get_setting(self, key):
        return self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]

    def set_setting(self, key, value):
        self.con.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
        self.con.commit()

    def list_values(self, name):
        rows = self.con.execute("SELECT value FROM lookups WHERE list_name=? ORDER BY id", (name,))
        return [r[0] for r in rows]

    def add_value(self, name, value):
        self.con.execute("INSERT OR IGNORE INTO lookups (list_name, value) VALUES (?, ?)", (name, value))
        self.con.commit()

    def remove_value(self, name, value):
        self.con.execute("DELETE FROM lookups WHERE list_name=? AND value=?", (name, value))
        self.con.commit()

    def save_weekly_jobs(self, week_start, total):
        self.con.execute("INSERT OR REPLACE INTO weekly_jobs VALUES (?, ?)", (week_start, total))
        self.con.commit()

    def weekly_jobs(self, limit=8):
        return self.con.execute("SELECT * FROM weekly_jobs ORDER BY week_start DESC LIMIT ?", (limit,)).fetchall()

    def insert_incident(self, rec):
        sql = f"INSERT INTO incidents ({', '.join(INCIDENT_COLS)}) VALUES ({', '.join('?' * len(INCIDENT_COLS))})"
        self.con.execute(sql, [rec.get(c) for c in INCIDENT_COLS])
        self.con.commit()

    def recent(self, limit=500):
        return self.con.execute(
            "SELECT * FROM incidents WHERE voided=0 ORDER BY incident_date DESC, id DESC LIMIT ?", (limit,)).fetchall()

    def void(self, incident_id):
        self.con.execute("UPDATE incidents SET voided=1 WHERE id=?", (incident_id,))
        self.con.commit()

    def export_csv(self, path):
        rows = self.con.execute("SELECT * FROM incidents ORDER BY id").fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(rows[0].keys() if rows else ["id"] + INCIDENT_COLS + ["voided"])
            for r in rows:
                w.writerow(list(r))
        return len(rows)


def to_float(s):
    s = str(s).strip().replace("$", "").replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def estimate_cost(kind, v, rate, mile_rate):
    """Playbook costing: hours x loaded labor rate + supplies + mileage (+ replacement cost)."""
    h, m = v.get("hours") or 0, v.get("miles") or 0
    if kind == "Reclean":
        return h * rate + (v.get("supplies_cost") or 0) + m * mile_rate
    if kind == "Repeat Trip":
        return h * rate + m * mile_rate
    if kind == "Lost Item":
        return h * rate + ((v.get("other_cost") or 0) if v.get("repurchased") else 0)
    if kind == "Breakage":
        return h * rate + (v.get("other_cost") or 0)
    return 0


def week_options(n=26):
    """[(label, monday_date), ...] newest first: this week, last week, ..."""
    monday = date.today() - timedelta(days=date.today().weekday())
    out = []
    for i in range(n):
        m = monday - timedelta(weeks=i)
        label = f"{m:%b %d} - {m + timedelta(days=6):%b %d, %Y}" + ("  (this week)" if i == 0 else "")
        out.append((label, m))
    return out


def day_options(monday):
    """[(label, date), ...] Monday..Sunday of that week, excluding days that haven't happened yet."""
    days = [monday + timedelta(days=k) for k in range(7)]
    return [(f"{d:%a %b %d}", d) for d in days if d <= date.today()]


class IncidentForm(ttk.Frame):
    def __init__(self, parent, db, kind, on_saved):
        super().__init__(parent, padding=12)
        self.db, self.kind, self.on_saved = db, kind, on_saved
        self.vars, self.widgets, self.combos = {}, {}, {}

        head = ttk.LabelFrame(self, text="When did it happen?", padding=8)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.week_var, self.day_var = tk.StringVar(), tk.StringVar()
        ttk.Label(head, text="Week *").grid(row=0, column=0, sticky="w")
        self.week_box = ttk.Combobox(head, textvariable=self.week_var, state="readonly", width=30)
        self.week_box.grid(row=0, column=1, padx=6)
        self.week_box.bind("<<ComboboxSelected>>", lambda e: self.pick_week())
        ttk.Label(head, text="Day *").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.day_box = ttk.Combobox(head, textvariable=self.day_var, state="readonly", width=16)
        self.day_box.grid(row=0, column=3, padx=6)
        self.weeks, self.days = [], []
        self.reset_date()

        body = ttk.LabelFrame(self, text=f"{kind} details", padding=8)
        body.grid(row=1, column=0, sticky="nsew")
        for r, (key, label, wtype, lst, req) in enumerate(FIELDS[kind]):
            ttk.Label(body, text=label + (" *" if req else "")).grid(row=r, column=0, sticky="w", pady=3)
            if wtype == "check":
                var = tk.BooleanVar()
                w = ttk.Checkbutton(body, variable=var, command=self.refresh_cost)
            elif wtype == "text":
                var, w = None, tk.Text(body, width=34, height=3)
            elif wtype in ("combo", "combo_edit"):
                var = tk.StringVar()
                state = "readonly" if wtype == "combo" else "normal"      # combo_edit lets you type anything
                w = ttk.Combobox(body, textvariable=var, state=state, width=32)
                self.combos[key] = (w, lst)
            else:
                var = tk.StringVar()
                w = ttk.Entry(body, textvariable=var, width=34)
                if wtype == "number":
                    var.trace_add("write", lambda *_: self.refresh_cost())
            w.grid(row=r, column=1, sticky="w", padx=8, pady=3)
            self.vars[key], self.widgets[key] = var, w

        side = ttk.LabelFrame(self, text="Cost", padding=12)
        side.grid(row=1, column=1, sticky="n", padx=(12, 0))
        self.rate_var = tk.StringVar()
        self.rate_var.trace_add("write", lambda *_: self.refresh_cost())
        ttk.Label(side, text="Hourly rate ($)").pack(anchor="w")
        ttk.Entry(side, textvariable=self.rate_var, width=10).pack(anchor="w", pady=(0, 10))
        self.cost_lbl = ttk.Label(side, text="$0.00", font=("Segoe UI", 20, "bold"))
        self.cost_lbl.pack()
        self.note_lbl = ttk.Label(side, text="", foreground="gray")
        self.note_lbl.pack(pady=(6, 0))

        btns = ttk.Frame(self)
        btns.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Clear", command=lambda: self.clear(reset_date=True)).pack(side="left", padx=4)
        ttk.Button(btns, text="Save entry", command=self.submit).pack(side="left")
        self.reload_settings()
        self.refresh_lists()

    def reset_date(self):
        """Back to this week, today."""
        self.weeks = week_options()
        self.week_box["values"] = [label for label, _ in self.weeks]
        self.week_box.current(0)
        self.pick_week()
        today_label = f"{date.today():%a %b %d}"
        self.day_var.set(today_label)

    def pick_week(self):
        monday = self.weeks[self.week_box.current()][1]
        self.days = day_options(monday)
        self.day_box["values"] = [label for label, _ in self.days]
        if self.day_var.get() not in [label for label, _ in self.days]:
            self.day_var.set("")          # different week: make you pick the day on purpose

    def selected_date(self):
        for label, d in self.days:
            if label == self.day_var.get():
                return d
        return None

    def refresh_lists(self):
        for w, lst in self.combos.values():
            w["values"] = self.db.list_values(lst)

    def reload_settings(self):
        self.rate_var.set(f"{self.db.get_setting('labor_rate'):.2f}")
        self.note_lbl.config(text=f"Mileage @ ${self.db.get_setting('mileage_rate'):.2f}/mi\n(default rate is set in Settings)")

    def collect(self):
        values, errors = {}, []
        for key, label, wtype, _, req in FIELDS[self.kind]:
            if wtype == "text":
                val = self.widgets[key].get("1.0", "end").strip()
            elif wtype == "check":
                val = int(self.vars[key].get())
            elif wtype == "number":
                raw = self.vars[key].get().strip()
                val = to_float(raw)
                if val is None:
                    errors.append(f"{label} must be a number")
                    continue
                if req and not raw:
                    errors.append(f"{label} is required")
                val = val if raw else None
            else:
                val = self.vars[key].get().strip()
                if req and not val:
                    errors.append(f"{label} is required")
            values[key] = val
        return values, errors

    def refresh_cost(self):
        values, _ = self.collect()
        rate = to_float(self.rate_var.get()) or 0
        cost = estimate_cost(self.kind, values, rate, self.db.get_setting("mileage_rate"))
        self.cost_lbl.config(text=f"${cost:,.2f}")

    def clear(self, reset_date=True):
        for key, var in self.vars.items():
            if var is None:
                self.widgets[key].delete("1.0", "end")
            elif isinstance(var, tk.BooleanVar):
                var.set(False)
            else:
                var.set("")
        if reset_date:
            self.reset_date()
        self.reload_settings()
        self.refresh_cost()

    def submit(self):
        values, errors = self.collect()
        when = self.selected_date()
        if when is None:
            errors.append("Pick the week and the day")
        rate = to_float(self.rate_var.get())
        if rate is None:
            errors.append("Hourly rate must be a number")
        if errors:
            messagebox.showwarning("Fix these first", "\n".join(errors))
            return
        mile = self.db.get_setting("mileage_rate")
        rec = {"incident_date": when.isoformat(), "kind": self.kind,
               "labor_rate": rate, "mileage_rate": mile,
               "est_cost": estimate_cost(self.kind, values, rate, mile),
               "created_at": datetime.now().isoformat(timespec="seconds"), **values}
        self.db.insert_incident(rec)
        self.on_saved(rec)
        self.clear(reset_date=False)      # keep the week/day: you usually enter several from the same day


class RecentTab(ttk.Frame):
    COLS = ("id", "date", "kind", "job_id", "tech", "detail", "cost")

    def __init__(self, parent, db):
        super().__init__(parent, padding=12)
        self.db = db
        self.tree = ttk.Treeview(self, columns=self.COLS, show="headings", height=15)
        for c, w in zip(self.COLS, (40, 85, 90, 80, 90, 220, 80)):
            self.tree.heading(c, text=c.replace("_", " ").title())
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True)
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Void selected", command=self.void_selected).pack(side="left")
        ttk.Button(bar, text="Export all to CSV", command=self.export).pack(side="left", padx=6)
        self.total = ttk.Label(bar, text="", font=("Segoe UI", 10, "bold"))
        self.total.pack(side="right")
        self.reload()

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.db.recent()
        for r in rows:
            detail = " / ".join(x for x in (r["item"], r["reason"]) if x)
            self.tree.insert("", "end", values=(r["id"], r["incident_date"], r["kind"], r["job_id"] or "",
                                                r["tech"] or "", detail, f"${r['est_cost'] or 0:,.2f}"))
        self.total.config(text=f"{len(rows)} entries   |   Total ${sum(r['est_cost'] or 0 for r in rows):,.2f}")

    def void_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.tree.item(sel[0])["values"]
        if messagebox.askyesno("Void entry", f"Void entry #{row[0]} ({row[2]}, {row[1]})?\nIt stays in the database but is excluded from totals."):
            self.db.void(row[0])
            self.reload()

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="incident_log_export.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            n = self.db.export_csv(path)
            messagebox.showinfo("Exported", f"Wrote {n} rows (including voided) to\n{path}")


class SettingsTab(ttk.Frame):
    def __init__(self, parent, db, on_change):
        super().__init__(parent, padding=12)
        self.db, self.on_change = db, on_change

        rates = ttk.LabelFrame(self, text="Default rates", padding=10)
        rates.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.labor_var = tk.StringVar(value=f"{db.get_setting('labor_rate'):.2f}")
        self.mile_var = tk.StringVar(value=f"{db.get_setting('mileage_rate'):.2f}")
        ttk.Label(rates, text="Loaded hourly labor rate ($, wages + burden)").grid(row=0, column=0, sticky="w")
        ttk.Entry(rates, textvariable=self.labor_var, width=10).grid(row=0, column=1, padx=8, pady=3)
        ttk.Label(rates, text="Mileage rate ($ per mile)").grid(row=1, column=0, sticky="w")
        ttk.Entry(rates, textvariable=self.mile_var, width=10).grid(row=1, column=1, padx=8, pady=3)
        ttk.Button(rates, text="Save rates", command=self.save_rates).grid(row=0, column=2, rowspan=2, padx=8)
        ttk.Label(rates, text="Each entry stores the rate it used, so changing this later does not alter past entries.",
                  foreground="gray").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        jobs = ttk.LabelFrame(self, text="Total jobs per week (needed for rates per 100 jobs)", padding=10)
        jobs.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.weeks = week_options()
        self.jobs_var = tk.StringVar()
        ttk.Label(jobs, text="Week").grid(row=0, column=0)
        self.week_box = ttk.Combobox(jobs, state="readonly", width=30, values=[l for l, _ in self.weeks])
        self.week_box.current(1)          # default to last week, the one you usually report on
        self.week_box.grid(row=0, column=1, padx=6)
        ttk.Label(jobs, text="Total jobs").grid(row=0, column=2)
        ttk.Entry(jobs, textvariable=self.jobs_var, width=8).grid(row=0, column=3, padx=6)
        ttk.Button(jobs, text="Save", command=self.save_jobs).grid(row=0, column=4)
        self.jobs_lbl = ttk.Label(jobs, text="", foreground="gray")
        self.jobs_lbl.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.show_jobs()

        lists = ttk.LabelFrame(self, text="Dropdown lists", padding=10)
        lists.grid(row=2, column=0, sticky="ew")
        self.list_var = tk.StringVar(value="people")
        names = list(LIST_LABELS)
        pick = ttk.Combobox(lists, state="readonly", width=34, values=[LIST_LABELS[n] for n in names])
        pick.current(0)
        pick.grid(row=0, column=0, sticky="w")
        pick.bind("<<ComboboxSelected>>", lambda e: (self.list_var.set(names[pick.current()]), self.show_list()))
        self.listbox = tk.Listbox(lists, height=6, width=36)
        self.listbox.grid(row=1, column=0, rowspan=3, pady=6, sticky="w")
        self.new_var = tk.StringVar()
        ttk.Entry(lists, textvariable=self.new_var, width=24).grid(row=1, column=1, padx=8, sticky="s")
        ttk.Button(lists, text="Add", command=self.add_item).grid(row=2, column=1, padx=8, sticky="w")
        ttk.Button(lists, text="Remove selected", command=self.remove_item).grid(row=3, column=1, padx=8, sticky="nw")
        self.show_list()

    def save_rates(self):
        labor, mile = to_float(self.labor_var.get()), to_float(self.mile_var.get())
        if not labor or mile is None:
            messagebox.showwarning("Rates", "Enter valid numbers for both rates.")
            return
        self.db.set_setting("labor_rate", labor)
        self.db.set_setting("mileage_rate", mile)
        self.on_change()
        messagebox.showinfo("Saved", "Rates updated. New entries will use them.")

    def save_jobs(self):
        try:
            total = int(self.jobs_var.get())
        except ValueError:
            messagebox.showwarning("Weekly jobs", "Enter the total jobs as a whole number.")
            return
        self.db.save_weekly_jobs(self.weeks[self.week_box.current()][1].isoformat(), total)
        self.jobs_var.set("")
        self.show_jobs()

    def show_jobs(self):
        rows = self.db.weekly_jobs()
        self.jobs_lbl.config(text="Recent: " + (", ".join(f"{r['week_start']}: {r['total_jobs']}" for r in rows) or "none yet"))

    def show_list(self):
        self.listbox.delete(0, "end")
        for v in self.db.list_values(self.list_var.get()):
            self.listbox.insert("end", v)

    def add_item(self):
        v = self.new_var.get().strip()
        if v:
            self.db.add_value(self.list_var.get(), v)
            self.new_var.set("")
            self.show_list()
            self.on_change()

    def remove_item(self):
        sel = self.listbox.curselection()
        if sel:
            self.db.remove_value(self.list_var.get(), self.listbox.get(sel[0]))
            self.show_list()
            self.on_change()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Waste Incident Log - Phase 1")
        self.geometry("780x600")
        self.db = DB(DB_PATH)
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.recent = RecentTab(nb, self.db)
        self.forms = [IncidentForm(nb, self.db, k, self.saved) for k in FIELDS]
        for f in self.forms:
            nb.add(f, text=TAB_LABELS.get(f.kind, f.kind))
        nb.add(self.recent, text="Recent entries")
        nb.add(SettingsTab(nb, self.db, self.settings_changed), text="Settings")
        self.status = ttk.Label(self, text=f"Data file: {DB_PATH}   (* = required)", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 6))

    def saved(self, rec):
        self.recent.reload()
        self.status.config(text=f"Saved {rec['kind']} entry (${rec['est_cost']:,.2f})")

    def settings_changed(self):
        for f in self.forms:
            f.refresh_lists()
            f.reload_settings()
            f.refresh_cost()


if __name__ == "__main__":
    App().mainloop()
