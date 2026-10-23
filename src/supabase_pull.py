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
        select event_no, waste_type, status, job_id, occurred_at,
               labor_hours, labor_cost, other_cost, total_cost,
               preventable, severity, title, description, source
        from waste_events
        where status not in ('voided', 'draft')
          and occurred_at >= %(start)s
          and (%(end)s::timestamptz is null or occurred_at < %(end)s)
        order by occurred_at"""
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

def count_jobs(start, end):
    """Jobs with labor logged in the window (a working definition of 'jobs this week')."""
    q = """select count(distinct job_id) as n from daily_labor
           where work_date between %(s)s and %(e)s and job_id is not null"""
    return int(pull(q, {"s": start, "e": end})["n"].iloc[0])