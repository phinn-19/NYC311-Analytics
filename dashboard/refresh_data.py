from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import requests


# ============================================================
# PROJECT / SOURCE CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
METADATA_DIR = PROJECT_ROOT / "data" / "metadata"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"

STATE_FILE = METADATA_DIR / "refresh_state.json"
BUILD_CACHE_SCRIPT = PROJECT_ROOT / "dashboard" / "build_dashboard_cache.py"

DATASET_ID = "erm2-nwe9"
API_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"

# The analytical population remains requests CREATED in 2022–2025.
ANALYSIS_START = pd.Timestamp("2022-01-01 00:00:00")
ANALYSIS_END_EXCLUSIVE = pd.Timestamp("2026-01-01 00:00:00")

# Existing project snapshot used to bootstrap incremental refresh.
BOOTSTRAP_WATERMARK = pd.Timestamp("2026-10-06 23:59:59")

# Small overlap protects against slightly delayed source publication.
LOOKBACK_DAYS = 2

PAGE_SIZE = 50_000
MAX_RETRIES = 5
REQUEST_TIMEOUT = 90

RAW_COLUMNS = [
    "unique_key",
    "created_date",
    "closed_date",
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "location_type",
    "incident_zip",
    "community_board",
    "borough",
    "status",
    "open_data_channel_type",
    "latitude",
    "longitude",
]

API_COLUMNS = RAW_COLUMNS + [
    "resolution_action_updated_date",
]


# ============================================================
# STATE HELPERS
# ============================================================

def iso_no_tz(value: pd.Timestamp | datetime) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("America/New_York").tz_localize(None)
    return ts.floor("s").isoformat()


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "dataset_id": DATASET_ID,
        "analysis_start": ANALYSIS_START.isoformat(),
        "analysis_end_exclusive": ANALYSIS_END_EXCLUSIVE.isoformat(),
        "source_timezone": "America/New_York",
        "last_resolution_watermark": BOOTSTRAP_WATERMARK.isoformat(),
        "source_snapshot_at": BOOTSTRAP_WATERMARK.isoformat(),
        "last_check_at": None,
        "last_refresh_started_at": None,
        "last_refresh_completed_at": None,
        "last_refresh_status": "bootstrapped",
        "last_changed_rows": 0,
        "affected_partitions": [],
        "note": (
            "Bootstrap state represents the full project snapshot created "
            "on 2026-10-06. Incremental refresh uses "
            "resolution_action_updated_date with a short lookback window."
        ),
    }


def save_state(state: dict) -> None:
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE_FILE)


def now_new_york_naive() -> pd.Timestamp:
    return pd.Timestamp(
        datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    ).floor("s")


# ============================================================
# API HELPERS
# ============================================================

def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "NYC311-Analytics-Academic-Project/1.0",
            "Accept": "application/json",
        }
    )

    app_token = os.getenv("SOCRATA_APP_TOKEN")
    if app_token:
        session.headers["X-App-Token"] = app_token

    return session


def get_json(session: requests.Session, params: dict) -> list[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        response = session.get(
            API_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 403:
            raise RuntimeError(
                "NYC Open Data returned HTTP 403. "
                "No local files were changed. "
                "Run the same refresh script from an environment that can "
                "reach data.cityofnewyork.us (for this project, Kaggle worked)."
            )

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_RETRIES:
                response.raise_for_status()

            wait_seconds = min(2 ** attempt, 30)
            print(
                f"HTTP {response.status_code}; retry "
                f"{attempt}/{MAX_RETRIES} in {wait_seconds}s..."
            )
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError("Unexpected retry loop exit.")


def soql_ts(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%S.000")


def build_delta_where(
    query_start: pd.Timestamp,
    query_end: pd.Timestamp,
) -> str:
    return (
        f"created_date >= '{soql_ts(ANALYSIS_START)}' "
        f"AND created_date < '{soql_ts(ANALYSIS_END_EXCLUSIVE)}' "
        f"AND resolution_action_updated_date IS NOT NULL "
        f"AND resolution_action_updated_date > '{soql_ts(query_start)}' "
        f"AND resolution_action_updated_date <= '{soql_ts(query_end)}'"
    )


def count_delta(
    session: requests.Session,
    where_clause: str,
) -> int:
    payload = get_json(
        session,
        {
            "$select": "count(*) AS n",
            "$where": where_clause,
        },
    )

    if not payload:
        return 0

    return int(payload[0]["n"])


def fetch_delta(
    session: requests.Session,
    where_clause: str,
    expected_count: int,
) -> pd.DataFrame:
    pages = []
    offset = 0

    while True:
        payload = get_json(
            session,
            {
                "$select": ",".join(API_COLUMNS),
                "$where": where_clause,
                "$order": (
                    "resolution_action_updated_date ASC, "
                    "unique_key ASC"
                ),
                "$limit": PAGE_SIZE,
                "$offset": offset,
            },
        )

        if not payload:
            break

        page = pd.DataFrame(payload)
        pages.append(page)

        offset += len(page)

        print(
            f"Fetched {offset:,}"
            + (
                f" / {expected_count:,}"
                if expected_count
                else ""
            )
            + " changed source rows"
        )

        if len(page) < PAGE_SIZE:
            break

    if not pages:
        return pd.DataFrame(columns=API_COLUMNS)

    delta = pd.concat(pages, ignore_index=True)

    for col in API_COLUMNS:
        if col not in delta.columns:
            delta[col] = pd.NA

    delta = delta.reindex(columns=API_COLUMNS)

    # Source endpoint represents current row state; protect against
    # accidental duplicate keys across paginated retrieval.
    delta["resolution_action_updated_date"] = pd.to_datetime(
        delta["resolution_action_updated_date"],
        errors="coerce",
    )

    delta = (
        delta
        .sort_values(
            [
                "resolution_action_updated_date",
                "unique_key",
            ]
        )
        .drop_duplicates(
            subset=["unique_key"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    return delta


# ============================================================
# CLEANING / FEATURE ENGINEERING
# Kept aligned with 02_data_cleaning_revised.ipynb
# ============================================================

def clean_partition(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reindex(columns=RAW_COLUMNS)

    string_cols = [
        "unique_key",
        "agency",
        "agency_name",
        "complaint_type",
        "descriptor",
        "location_type",
        "incident_zip",
        "community_board",
        "borough",
        "status",
        "open_data_channel_type",
    ]

    for col in string_cols:
        df[col] = (
            df[col]
            .astype("string")
            .str.strip()
        )

    df["complaint_type"] = df["complaint_type"].replace(
        {"Unspecified": pd.NA}
    )

    df["descriptor"] = df["descriptor"].replace(
        {
            "N/A": pd.NA,
            "Unspecified": pd.NA,
        }
    )

    df["location_type"] = df["location_type"].replace(
        {
            "N/A": pd.NA,
            "Unknown": pd.NA,
            "Unspecified": pd.NA,
        }
    )

    df["incident_zip"] = df["incident_zip"].replace(
        {
            "N/A": pd.NA,
            "NA": pd.NA,
        }
    )

    df["borough"] = df["borough"].replace(
        {"Unspecified": pd.NA}
    )

    df["status"] = df["status"].replace(
        {"Unspecified": pd.NA}
    )

    df["borough"] = df["borough"].str.upper()

    df["open_data_channel_type"] = (
        df["open_data_channel_type"]
        .str.upper()
    )

    df["created_date"] = pd.to_datetime(
        df["created_date"],
        errors="coerce",
    )

    df["closed_date"] = pd.to_datetime(
        df["closed_date"],
        errors="coerce",
    )

    df["latitude"] = pd.to_numeric(
        df["latitude"],
        errors="coerce",
    )

    df["longitude"] = pd.to_numeric(
        df["longitude"],
        errors="coerce",
    )

    return df


def process_partition(
    df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    df = clean_partition(df)

    df["year"] = df["created_date"].dt.year
    df["month"] = df["created_date"].dt.month
    df["weekday"] = df["created_date"].dt.day_name()
    df["hour"] = df["created_date"].dt.hour
    df["is_weekend"] = (
        df["created_date"].dt.dayofweek >= 5
    )

    df["closure_valid"] = (
        df["created_date"].notna()
        & df["closed_date"].notna()
        & (df["closed_date"] >= df["created_date"])
        & (df["closed_date"] <= snapshot_date)
    )

    df["closure_hours"] = np.nan
    valid_mask = df["closure_valid"]

    df.loc[
        valid_mask,
        "closure_hours",
    ] = (
        (
            df.loc[valid_mask, "closed_date"]
            - df.loc[valid_mask, "created_date"]
        )
        .dt.total_seconds()
        / 3600
    )

    df["closure_hours"] = pd.to_numeric(
        df["closure_hours"],
        errors="coerce",
    )

    df["has_coordinates"] = (
        df["latitude"].notna()
        & df["longitude"].notna()
    )

    return df


# ============================================================
# LOCAL UPSERT
# ============================================================

def atomic_write_parquet(
    df: pd.DataFrame,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(
        destination.name + ".tmp.parquet"
    )
    df.to_parquet(
        tmp,
        index=False,
        compression="snappy",
    )
    tmp.replace(destination)


def upsert_raw_partitions(
    delta: pd.DataFrame,
) -> list[tuple[int, int]]:
    if delta.empty:
        return []

    incoming = delta.copy()
    incoming["created_date_dt"] = pd.to_datetime(
        incoming["created_date"],
        errors="coerce",
    )

    incoming = incoming[
        incoming["created_date_dt"].notna()
    ].copy()

    incoming["partition_year"] = (
        incoming["created_date_dt"].dt.year
    )

    incoming["partition_month"] = (
        incoming["created_date_dt"].dt.month
    )

    incoming = incoming[
        incoming["partition_year"].between(2022, 2025)
    ].copy()

    affected = []

    for (
        year,
        month,
    ), part_delta in incoming.groupby(
        [
            "partition_year",
            "partition_month",
        ],
        sort=True,
    ):
        year = int(year)
        month = int(month)

        raw_file = (
            RAW_DIR
            / f"nyc311_{year}_{month:02d}_raw.parquet"
        )

        new_rows = (
            part_delta[RAW_COLUMNS]
            .copy()
        )

        new_rows["unique_key"] = (
            new_rows["unique_key"]
            .astype("string")
        )

        if raw_file.exists():
            old = pd.read_parquet(raw_file)
            old = old.reindex(columns=RAW_COLUMNS)
            old["unique_key"] = (
                old["unique_key"]
                .astype("string")
            )

            merged = pd.concat(
                [
                    old,
                    new_rows,
                ],
                ignore_index=True,
            )

        else:
            merged = new_rows.copy()

        before = len(merged)

        merged = (
            merged
            .drop_duplicates(
                subset=["unique_key"],
                keep="last",
            )
            .reset_index(drop=True)
        )

        merged["created_date_sort"] = pd.to_datetime(
            merged["created_date"],
            errors="coerce",
        )

        merged = (
            merged
            .sort_values(
                [
                    "created_date_sort",
                    "unique_key",
                ],
                na_position="last",
            )
            .drop(
                columns=["created_date_sort"]
            )
            .reindex(columns=RAW_COLUMNS)
            .reset_index(drop=True)
        )

        if merged["unique_key"].duplicated().any():
            raise RuntimeError(
                f"Duplicate unique_key remains in {raw_file.name}"
            )

        atomic_write_parquet(
            merged,
            raw_file,
        )

        affected.append(
            (
                year,
                month,
            )
        )

        print(
            f"Upserted {raw_file.name}: "
            f"{len(new_rows):,} source rows, "
            f"{len(merged):,} total rows "
            f"({before - len(merged):,} duplicate versions collapsed)"
        )

    return affected


def rebuild_processed_partitions(
    affected: list[tuple[int, int]],
    snapshot_date: pd.Timestamp,
) -> None:
    for year, month in affected:
        raw_file = (
            RAW_DIR
            / f"nyc311_{year}_{month:02d}_raw.parquet"
        )

        processed_file = (
            PROCESSED_DIR
            / f"nyc311_{year}_{month:02d}_processed.parquet"
        )

        raw = pd.read_parquet(raw_file)

        processed = process_partition(
            raw,
            snapshot_date=snapshot_date,
        )

        if processed["unique_key"].duplicated().any():
            raise RuntimeError(
                f"Duplicate unique_key after processing "
                f"{processed_file.name}"
            )

        invalid_future = (
            processed["closure_valid"]
            & (
                processed["closed_date"]
                > snapshot_date
            )
        ).sum()

        if invalid_future:
            raise RuntimeError(
                f"{processed_file.name}: "
                f"{invalid_future} future closures marked valid"
            )

        atomic_write_parquet(
            processed,
            processed_file,
        )

        print(
            f"Rebuilt {processed_file.name}: "
            f"{len(processed):,} rows"
        )


def rebuild_dashboard_caches() -> None:
    if not BUILD_CACHE_SCRIPT.exists():
        raise FileNotFoundError(
            f"Missing {BUILD_CACHE_SCRIPT}"
        )

    print("\nRebuilding dashboard caches...")

    subprocess.run(
        [
            sys.executable,
            str(BUILD_CACHE_SCRIPT),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


# ============================================================
# MAIN
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally refresh the fixed 2022–2025 NYC 311 "
            "analysis population from NYC Open Data."
        )
    )

    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Only query how many source rows changed; "
            "do not modify local files."
        ),
    )

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Create refresh metadata from the existing "
            "2026-10-06 project snapshot and exit."
        ),
    )

    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help=(
            "Override the saved incremental watermark, "
            "e.g. 2026-10-06T23:59:59."
        ),
    )

    parser.add_argument(
        "--no-lookback",
        action="store_true",
        help="Disable the default late-arrival lookback window.",
    )

    parser.add_argument(
        "--skip-cache",
        action="store_true",
        help="Refresh raw/processed partitions but skip dashboard cache rebuild.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()

    if args.bootstrap:
        save_state(state)
        print(f"Refresh state created: {STATE_FILE}")
        print(
            "Bootstrap watermark:",
            state["last_resolution_watermark"],
        )
        return 0

    watermark = pd.Timestamp(
        args.since
        or state.get(
            "last_resolution_watermark",
            BOOTSTRAP_WATERMARK.isoformat(),
        )
    )

    query_start = watermark

    if not args.no_lookback:
        query_start = (
            query_start
            - pd.Timedelta(days=LOOKBACK_DAYS)
        )

    source_cutoff = now_new_york_naive()

    # Never query beyond the current source clock.
    if query_start >= source_cutoff:
        query_start = watermark

    where_clause = build_delta_where(
        query_start=query_start,
        query_end=source_cutoff,
    )

    state["last_check_at"] = iso_no_tz(
        source_cutoff
    )

    session = build_session()

    print("Dataset:", DATASET_ID)
    print(
        "Analysis population:",
        "2022-01-01 <= created_date < 2026-01-01",
    )
    print(
        "Saved watermark:",
        iso_no_tz(watermark),
    )
    print(
        "Query start:",
        iso_no_tz(query_start),
        (
            f"(includes {LOOKBACK_DAYS}-day lookback)"
            if not args.no_lookback
            else ""
        ),
    )
    print(
        "Query cutoff:",
        iso_no_tz(source_cutoff),
        "America/New_York",
    )

    try:
        changed_count = count_delta(
            session,
            where_clause,
        )

        print(
            "Source rows in refresh window:",
            f"{changed_count:,}",
        )

        if args.check_only:
            print(
                "\nCHECK ONLY — no local data was modified."
            )
            return 0

        state["last_refresh_started_at"] = (
            iso_no_tz(source_cutoff)
        )
        state["last_refresh_status"] = "running"
        save_state(state)

        if changed_count == 0:
            state["source_snapshot_at"] = (
                iso_no_tz(source_cutoff)
            )
            state["last_refresh_completed_at"] = (
                iso_no_tz(now_new_york_naive())
            )
            state["last_refresh_status"] = "success_no_changes"
            state["last_changed_rows"] = 0
            state["affected_partitions"] = []
            save_state(state)

            print(
                "\nNo source changes found. "
                "Raw, processed, and cache files were left unchanged."
            )
            return 0

        delta = fetch_delta(
            session=session,
            where_clause=where_clause,
            expected_count=changed_count,
        )

        if delta.empty:
            raise RuntimeError(
                "Count query reported changes but fetch returned no rows."
            )

        affected = upsert_raw_partitions(
            delta
        )

        rebuild_processed_partitions(
            affected=affected,
            snapshot_date=source_cutoff,
        )

        if not args.skip_cache:
            rebuild_dashboard_caches()

        max_update = (
            delta[
                "resolution_action_updated_date"
            ]
            .dropna()
            .max()
        )

        if pd.notna(max_update):
            state["last_resolution_watermark"] = (
                iso_no_tz(max_update)
            )

        state["source_snapshot_at"] = (
            iso_no_tz(source_cutoff)
        )
        state["last_refresh_completed_at"] = (
            iso_no_tz(now_new_york_naive())
        )
        state["last_refresh_status"] = "success"
        state["last_changed_rows"] = int(
            len(delta)
        )
        state["affected_partitions"] = [
            f"{year}-{month:02d}"
            for year, month in affected
        ]

        save_state(state)

        print("\nREFRESH COMPLETE")
        print(
            "Changed unique requests:",
            f"{len(delta):,}",
        )
        print(
            "Affected monthly partitions:",
            ", ".join(
                state["affected_partitions"]
            )
            or "None",
        )
        print(
            "New incremental watermark:",
            state[
                "last_resolution_watermark"
            ],
        )
        print(
            "Dashboard source snapshot:",
            state[
                "source_snapshot_at"
            ],
        )

        return 0

    except Exception as exc:
        state["last_refresh_completed_at"] = (
            iso_no_tz(now_new_york_naive())
        )
        state["last_refresh_status"] = "failed"
        state["last_error"] = str(exc)
        save_state(state)

        print(
            f"\nREFRESH FAILED: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
