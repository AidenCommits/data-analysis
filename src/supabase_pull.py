import os
from datetime import date
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

load_dotenv()
_engine = create_engine(URL.create(
    "postgresql+psycopg2",
    username=os.environ["SUPABASE_USER"],
    password=os.environ["SUPABASE_PASSWORD"],     # special characters are fine here
    host=os.environ["SUPABASE_HOST"],
    port=int(os.environ["SUPABASE_PORT"]),
    database=os.environ["SUPABASE_DB"],
    query={"sslmode": "require"},
))

def pull(sql, params=None, snapshot=None):
    """Run a read-only query; optionally save a dated snapshot to data/raw."""
    df = pd.read_sql(sql, _engine, params=params)
    if snapshot:
        out = Path("../data/raw") / f"{date.today().isoformat()}_{snapshot}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)   # data/ isn't in git, so a fresh clone won't have it
        df.to_csv(out, index=False)
    return df


WASTE_LABELS = {
    "breakage": "Breakage", "lost_item": "Lost Item", "search": "Item Search",
    "repeat_trip": "Repeat Trip", "reclean": "Reclean", "idle_time": "Idle Time",
    "equipment_down": "Equipment Down", "material_waste": "Material Waste",
    "vehicle": "Vehicle Incident", "other": "Other",
}

def load_waste(start, end=None, snapshot="waste_events"):
    """Waste events in the shape the reusable functions expect."""
    sql = """
        select w.event_no, w.waste_type, w.status, w.job_id, w.occurred_at,
               w.labor_hours, w.labor_cost, w.other_cost, w.total_cost,
               w.preventable, w.severity, w.title, w.description, w.source,
               coalesce(j.job_number, j.property_address) as job,
               j.service_type as job_type
        from waste_events w
        left join jobs j on j.id = w.job_id
        where w.status not in ('voided', 'draft')
          and w.occurred_at >= %(start)s
          and (%(end)s::timestamptz is null or w.occurred_at < %(end)s)
        order by w.occurred_at, w.event_no"""
    df = pull(sql, {"start": start, "end": end}, snapshot=snapshot)
    if df.empty:
        return df.assign(id=[], kind=[], incident_date=[], hours=[])
    for c in ["labor_hours", "labor_cost", "other_cost", "total_cost"]:
        df[c] = pd.to_numeric(df[c]).fillna(0.0)
    df["kind"] = df["waste_type"].map(WASTE_LABELS).fillna(df["waste_type"])
    df["incident_date"] = pd.to_datetime(df["occurred_at"], utc=True).dt.tz_convert("America/New_York").dt.date
    df["hours"] = df["labor_hours"]
    df["id"] = range(1, len(df) + 1)
    return df


# Historical baseline imported from the Phase 1 local db (its records #3-16).
BASELINE_EVENTS = ("W-2026-0001", "W-2026-0014")

def split_baseline_events(df, first=BASELINE_EVENTS[0], last=BASELINE_EVENTS[1]):
    """(baseline, everything else), split on event_no instead of local row ids."""
    is_base = df["event_no"].between(first, last)
    return df[is_base], df[~is_base]


#Needs review# ##Should not require distinct job ID##
def count_jobs(start, end):
    """Jobs with labor logged in the window (a working definition of 'jobs this week')."""
    q = """select count(distinct job_id) as n from daily_labor
           where work_date between %(s)s and %(e)s and job_id is not null"""
    return int(pull(q, {"s": start, "e": end})["n"].iloc[0])