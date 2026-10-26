import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

###DATA###

def cost_by(df, column='kind'):
    """
    Event count and total cost by column, largest first.
    
    """
    
    return (
        df.groupby(column)
        .agg(events=('id', 'count'), total=('total_cost', 'sum'))
        .round(2)
        .sort_values('total', ascending=False)
    )


def per_100_jobs(df, total_jobs, column="kind"):
    """
    cost_by() plus rates per 100 jobs for a single week.
    
    """
    
    out = cost_by(df, column)
    out["events_per_100"] = (out["events"] / total_jobs * 100).round(1)
    out["cost_per_100"] = (out["total"] / total_jobs * 100).round(2)
    return out


def annualize(df, total_jobs, weeks=52, column="kind", data_weeks=1):
    """
    Per-100-job rates plus a projection if this week's pace continued for `weeks` weeks.
    total_jobs = jobs completed in the week the data covers.
    
    """
    
    out = cost_by(df, column)
    out["events_per_100"] = (out["events"] / total_jobs * 100).round(1)
    out["cost_per_100"] = (out["total"] / total_jobs * 100).round(2)

    annual_jobs = total_jobs / data_weeks * weeks
    # Calculated from the unrounded numbers so rounding doesn't creep into the projection
    out["annual_events"] = (out["events"] / total_jobs * annual_jobs).round(0)
    out["annual_cost"] = (out["total"] / total_jobs * annual_jobs).round(2)

    out.loc["Total"] = out.sum()
    return out


def annualize_weeks(weeks, weeks_per_year=52):
    """
    weeks = {"label": (dataframe, jobs_that_week), ...}
    
    """
    
    parts = []
    for label, (frame, jobs) in weeks.items():
        table = annualize(frame, jobs, weeks_per_year)
        table.insert(0, "week", label)
        parts.append(table)
    return pd.concat(parts)


def compare_weeks(baseline, current, base_jobs, cur_jobs,
                  base_label="Baseline", cur_label="Reporting week"):
    
    b = cost_by(baseline).rename(columns={"events": "b_events", "total": "b_cost"})
    c = cost_by(current).rename(columns={"events": "c_events", "total": "c_cost"})
    t = b.join(c, how="outer").fillna(0)
    t.loc["Total"] = t.sum()                       # totals row, always last
    t["b_p100"] = (t["b_cost"] / base_jobs * 100).round(2)
    t["c_p100"] = (t["c_cost"] / cur_jobs * 100).round(2)
    t["chg"] = (t["c_p100"] - t["b_p100"]).round(2)
    t["chg_pct"] = ((t["c_p100"] / t["b_p100"] - 1) * 100).round(0)
    t = t.replace([float("inf"), -float("inf")], float("nan"))  # kind with no baseline -> blank
    t = t[["b_events", "b_cost", "b_p100", "c_events", "c_cost", "c_p100", "chg", "chg_pct"]]
    t.columns = pd.MultiIndex.from_tuples([
        (base_label, "Events"), (base_label, "Cost ($)"), (base_label, "Per 100 jobs ($)"),
        (cur_label, "Events"), (cur_label, "Cost ($)"), (cur_label, "Per 100 jobs ($)"),
        ("Change", "$ per 100"), ("Change", "%")])
    
    return t


def split_baseline(df, first_id=3, last_id=16):
    baseline = df[df["id"].between(first_id, last_id)]
    return baseline, df.drop(baseline.index)


def run_checks(df, checks):
    """checks = {"label": boolean Series, ...} -> one row per check."""
    out = pd.DataFrame({
        "rows_flagged": {k: int(v.fillna(False).sum()) for k, v in checks.items()},
    })
    out["of_total"] = len(df)
    out["pct"] = (out["rows_flagged"] / len(df) * 100).round(1)
    return out.sort_values("rows_flagged", ascending=False)


def reconcile(label, db_value, source_value, tolerance_pct=2):
    diff = db_value - source_value
    pct = diff / source_value * 100 if source_value else float("nan")
    return pd.Series({"item": label, "database": round(db_value, 2), "source": source_value,
                      "difference": round(diff, 2), "pct": round(pct, 1), "ok": abs(pct) <= tolerance_pct})

###CHARTS###

def cost_events_chart(summary, title=None, filename=None):
    """
    Side-by-side horizontal bars from a cost_by() result:
    total cost on the left, number of events on the right.
    
    """
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4), sharey=True)

    summary["total"].plot(kind="barh", ax=ax1, color="#2b6cb0", width=0.7)
    ax1.set_title("Total cost ($)")
    ax1.bar_label(ax1.containers[0], fmt="$%.2f", padding=3)
    ax1.set_xlim(0, summary["total"].max() * 1.4)
    ax1.spines["right"].set_visible(False)

    summary["events"].plot(kind="barh", ax=ax2, color="#dd6b20", width=0.7)
    ax2.set_title("Number of events")
    ax2.bar_label(ax2.containers[0], padding=3)
    ax2.set_xlim(0, summary["events"].max() * 1.12)
    ax2.xaxis.set_major_locator(MaxNLocator(integer=True))

    ax1.invert_yaxis()
    ax1.set_ylabel("")

    if title:
        fig.suptitle(title, fontsize=13)
    plt.tight_layout()
    fig.subplots_adjust(wspace=0.12)

    if filename:
        plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.show()

def weekly_cost_chart(weeks, jobs, title="Waste cost per 100 jobs", filename=None):
    """
    Line chart of cost per 100 jobs by waste type across any number of weeks.

    weeks: {"label": dataframe, ...} in the order they should appear
    jobs:  {"label": total_jobs_that_week, ...}, same labels as weeks
    
    """
    
    missing = set(weeks) - set(jobs)
    if missing:
        raise ValueError(f"No job count given for: {sorted(missing)}")

    table = pd.DataFrame(
        {label: frame.groupby("kind")["total_cost"].sum() for label, frame in weeks.items()}
    ).fillna(0).T
    table = table.div(pd.Series(jobs).reindex(table.index), axis=0) * 100
    table["Total"] = table.sum(axis=1)

    ax = table.plot(kind="line", marker="o", figsize=(9, 4.5), linewidth=2)
    colors = {line.get_label(): line.get_color() for line in ax.lines}

    n = len(table)
    ax.set_xticks(range(n))
    ax.set_xticklabels(table.index)
    ax.set_xlim(-0.4, n - 0.6)
    ax.set_ylim(0, table.values.max() * 1.2)

    # Labels: left of the first week, right of the last, above the ones between.
    # Labels at the same week are nudged apart so they never overlap.
    min_gap = table.values.max() * 0.06
    for i in range(n):
        last_y = None
        for col, y in table.iloc[i].sort_values().items():
            ty = y if last_y is None else max(y, last_y + min_gap)
            last_y = ty
            if i == n - 1:
                offset, ha, va = (7, 0), "left", "center"
            elif i == 0:
                offset, ha, va = (-7, 0), "right", "center"
            else:
                offset, ha, va = (0, 7), "center", "bottom"
            ax.annotate(f"${y:,.0f}", (i, ty), textcoords="offset points", xytext=offset,
                        ha=ha, va=va, color=colors[col], fontsize=9, fontweight="bold")

    ax.set_title(title)
    ax.set_ylabel("Cost per 100 jobs ($)")
    ax.set_xlabel("")
    ax.yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
    ax.legend(title="", loc="center left", bbox_to_anchor=(1.02, 0.5))
    plt.tight_layout()
    if filename:
        plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.show()