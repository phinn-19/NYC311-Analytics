"""Build exact, filter-ready Closure statistics for a lightweight Streamlit deploy.

Usage from project root: python dashboard/build_closure_cache.py

This script uses the 48 *local* processed partitions and saves two compact
Parquet tables. The processed partitions need NOT be uploaded to GitHub.

No median/percentile is ever obtained by averaging other medians: all
statistics are recalculated directly from valid request durations in each
requested filter group, just once on the local machine.
"""
from __future__ import annotations

from pathlib import Path
import sys
import time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"
TABLE_DIR = ROOT / "outputs" / "tables"
REQUEST_CUBE_FILE = TABLE_DIR / "dashboard_request_cube.parquet"
SUMMARY_FILE = TABLE_DIR / "dashboard_closure_filters.parquet"
MONTHLY_FILE = TABLE_DIR / "dashboard_closure_monthly.parquet"

YEAR_ALL = "All years"
BOROUGH_ALL = "All boroughs"
COMPLAINT_ALL = "All complaint types"


def write_atomic(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp.parquet")
    frame.to_parquet(tmp, index=False, compression="snappy")
    tmp.replace(destination)


def aggregate_exact(frame: pd.DataFrame, dimensions: list[str], probs: list[float]) -> pd.DataFrame:
    """Return exact quantiles/count for all nonempty groups (no roll-up of quantiles)."""
    names = {0.50: "median", 0.75: "p75", 0.90: "p90", 0.95: "p95"}
    if not dimensions:
        vals = frame["closure_hours"].to_numpy(dtype="float64", copy=False)
        if len(vals) == 0:
            return pd.DataFrame()
        q = np.quantile(vals, probs)
        return pd.DataFrame([{**{names[p]: float(v) for p, v in zip(probs, q)}, "valid_requests": len(vals)}])

    grouped = frame.groupby(dimensions, sort=False, observed=True, dropna=False)["closure_hours"]
    # groupby.quantile computes the desired probabilities within original samples,
    # not from pre-aggregated quantile values.
    out = grouped.quantile(probs).unstack(-1).reset_index().rename(columns=names)
    counts = grouped.size().rename("valid_requests").reset_index()
    out = out.merge(counts, on=dimensions, how="inner", validate="one_to_one")
    return out


def encode_display_labels(frame: pd.DataFrame, boroughs: list[str], complaints: list[str]) -> pd.DataFrame:
    # Missing borough / complaint values contribute to each All scope,
    # but cannot be selected as specific named filters in the UI.
    if "borough" in frame.columns:
        bcodes = frame["borough"].to_numpy(dtype="int32")
        frame["borough_filter"] = [BOROUGH_ALL if c == -2 else (boroughs[c] if c >= 0 else "__missing_borough__") for c in bcodes]
        frame = frame.drop(columns="borough")
    else:
        frame["borough_filter"] = BOROUGH_ALL
    if "complaint_type" in frame.columns:
        ccodes = frame["complaint_type"].to_numpy(dtype="int32")
        frame["complaint_filter"] = [COMPLAINT_ALL if c == -2 else (complaints[c] if c >= 0 else "__missing_complaint__") for c in ccodes]
        frame = frame.drop(columns="complaint_type")
    else:
        frame["complaint_filter"] = COMPLAINT_ALL
    if "year" in frame.columns:
        frame["year_filter"] = frame["year"].astype("int32").astype("string")
        frame = frame.drop(columns="year")
    else:
        frame["year_filter"] = YEAR_ALL
    return frame


def main() -> None:
    started = time.monotonic()
    files = sorted(PROCESSED_DIR.glob("nyc311_*_processed.parquet"))
    if not files:
        raise FileNotFoundError(f"No processed partitions in {PROCESSED_DIR}")
    if not REQUEST_CUBE_FILE.exists():
        raise FileNotFoundError(f"Run build_dashboard_cache.py first: {REQUEST_CUBE_FILE}")

    requests = pd.read_parquet(REQUEST_CUBE_FILE, columns=["borough", "complaint_type"])
    boroughs = sorted(str(s) for s in requests["borough"].dropna().unique())
    complaints = sorted(str(s) for s in requests["complaint_type"].dropna().unique())
    print(f"Closure cache: {len(files)} partitions, {len(complaints)} complaint types", flush=True)

    # Pack categorical values into small integer codes *before* concatenating:
    # approximately 14 bytes/valid request, rather than carrying millions of
    # repeated Python string objects in memory.
    parts = []
    for i, file in enumerate(files, start=1):
        source = pd.read_parquet(
            file,
            columns=["year", "month", "borough", "complaint_type", "closure_valid", "closure_hours"],
        )
        valid = source.loc[
            source["closure_valid"].fillna(False).astype(bool)
            & source["closure_hours"].notna()
            & (source["closure_hours"] >= 0),
            ["year", "month", "borough", "complaint_type", "closure_hours"],
        ]
        if not valid.empty:
            bc = pd.Categorical(valid["borough"], categories=boroughs).codes.astype("int16")
            cc = pd.Categorical(valid["complaint_type"], categories=complaints).codes.astype("int16")
            if (bc == -1).sum() != int(valid["borough"].isna().sum()):
                raise ValueError("Unknown Borough in processed data; rebuild request cube")
            if (cc == -1).sum() != int(valid["complaint_type"].isna().sum()):
                raise ValueError("Unknown Complaint Type in processed data; rebuild request cube")
            parts.append(pd.DataFrame({
                "year": valid["year"].to_numpy(dtype="int16"),
                "month": valid["month"].to_numpy(dtype="int8"),
                "borough": bc,
                "complaint_type": cc,
                "closure_hours": valid["closure_hours"].to_numpy(dtype="float64"),
            }))
        print(f"  [{i}/{len(files)}] {file.name}: {len(valid):,} valid", flush=True)
        del source, valid

    if not parts:
        raise ValueError("No valid closure records found; no cache was written")
    frame = pd.concat(parts, ignore_index=True, copy=False)
    del parts
    print(f"Computing exact quantiles for {len(frame):,} valid requests...", flush=True)

    # Every combination of the 3 global dropdowns (All / concrete value).
    # Overall and filtered statistics are exact, not estimates from histograms.
    summaries = []
    for has_y in (False, True):
        for has_b in (False, True):
            for has_c in (False, True):
                dims = (["year"] if has_y else []) + (["borough"] if has_b else []) + (["complaint_type"] if has_c else [])
                q = aggregate_exact(frame, dims, [0.50, 0.75, 0.90, 0.95])
                if q.empty:
                    continue
                q = encode_display_labels(q, boroughs, complaints)
                summaries.append(q[["year_filter", "borough_filter", "complaint_filter", "valid_requests", "median", "p75", "p90", "p95"]])
                print(f"  overall summary by {dims or ['ALL']}: {len(q):,} groups", flush=True)
    all_summary = pd.concat(summaries, ignore_index=True)
    del summaries

    # Monthly trends depend on the same Borough/Complaint dropdowns. Each
    # month already belongs to exactly one year, so no Year roll-up is needed.
    trends = []
    for has_b in (False, True):
        for has_c in (False, True):
            dims = ["year", "month"] + (["borough"] if has_b else []) + (["complaint_type"] if has_c else [])
            q = aggregate_exact(frame, dims, [0.50, 0.90])
            q = encode_display_labels(q, boroughs, complaints)
            q = q.rename(columns={"median": "median_closure", "p90": "p90_closure"})
            trends.append(q[["year_filter", "month", "borough_filter", "complaint_filter", "valid_requests", "median_closure", "p90_closure"]])
            print(f"  monthly summary by {dims}: {len(q):,} groups", flush=True)
    all_monthly = pd.concat(trends, ignore_index=True)
    del trends, frame

    # Skip groups that the user cannot select; they are nevertheless already
    # included in correct All scope aggregates.
    all_summary = all_summary[
        ~all_summary["borough_filter"].eq("__missing_borough__")
        & ~all_summary["complaint_filter"].eq("__missing_complaint__")
    ].reset_index(drop=True)
    all_monthly = all_monthly[
        ~all_monthly["borough_filter"].eq("__missing_borough__")
        & ~all_monthly["complaint_filter"].eq("__missing_complaint__")
    ].reset_index(drop=True)

    # Verify the all-scope valid count against the already-built official
    # closure summary (same cleaned-data validity rules).
    overall_file = TABLE_DIR / "closure_overall_summary_final.parquet"
    if overall_file.exists():
        metrics = pd.read_parquet(overall_file).set_index("metric")["value"]
        expected = int(metrics["Valid closure requests"])
        all_row = all_summary.loc[
            all_summary["year_filter"].eq(YEAR_ALL)
            & all_summary["borough_filter"].eq(BOROUGH_ALL)
            & all_summary["complaint_filter"].eq(COMPLAINT_ALL)
        ]
        actual = int(all_row.iloc[0]["valid_requests"])
        if actual != expected:
            raise ValueError(
                f"Closure cache mismatch: {actual:,} valid vs official {expected:,}. "
                "Rebuild standard dashboard caches from current processed data."
            )

    if all_summary.duplicated(["year_filter", "borough_filter", "complaint_filter"]).any():
        raise AssertionError("Duplicate closure summary keys")
    if all_monthly.duplicated(["year_filter", "month", "borough_filter", "complaint_filter"]).any():
        raise AssertionError("Duplicate monthly closure keys")

    # Commit only after BOTH complete; on failure the previous cache survives.
    write_atomic(all_summary, SUMMARY_FILE)
    write_atomic(all_monthly, MONTHLY_FILE)
    print(f"DONE: {SUMMARY_FILE.name} ({len(all_summary):,} rows); "
          f"{MONTHLY_FILE.name} ({len(all_monthly):,} rows); "
          f"elapsed {time.monotonic()-started:.1f}s", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
