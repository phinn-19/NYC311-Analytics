"""Auditable NYC311 address profiling/cleaning, retaining compatibility with old pilot inputs.

Usage (repository root):
    python src/rental_quality.py
    python src/rental_quality.py --input outputs/tables/rental_sample.csv

Source date fields are Socrata floating timestamps in NYC local clock; no UTC conversion.
BBL identifies a lot, not necessarily one building. NYC311 closure != physical resolution.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_DEFAULT = ROOT / "data" / "rental" / "raw" / "nyc311_rental_pilot_raw.parquet"
FALLBACK = ROOT / "outputs" / "tables" / "rental_sample.csv"
PROCESSED = ROOT / "data" / "rental" / "processed"
OUTPUT = ROOT / "outputs" / "tables"
ALLOWED = {"HEAT/HOT WATER", "Noise - Residential", "UNSANITARY CONDITION", "PLUMBING"}
REQUIRED = {"unique_key", "created_date", "complaint_type", "incident_address", "bbl", "borough", "status"}
OPTIONAL = ("descriptor", "closed_date", "resolution_description", "street_name", "incident_zip", "resolution_action_updated_date")


def normalize_text(x: pd.Series) -> pd.Series:
    s = x.astype("string").str.strip()
    return s.mask(s.eq("") | s.str.upper().isin(["N/A", "NA", "UNSPECIFIED", "NULL"]))


def normalize_address(x: pd.Series) -> pd.Series:
    # Only whitespace/case normalization; no guessed street equivalence.
    s = normalize_text(x).str.upper()
    return s.str.replace(r"\s+", " ", regex=True)


def read_input(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype="string", keep_default_na=False)
    if path.suffix.lower() in (".parquet", ".pq"):
        return pd.read_parquet(path).astype("string")
    raise ValueError("Only CSV and Parquet are supported")


def clean_profile(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    missing = REQUIRED - set(raw.columns)
    if missing:
        raise ValueError("Missing required input columns: " + ", ".join(sorted(missing)))
    src = raw.copy()
    missing_source_columns = sorted((REQUIRED | set(OPTIONAL)) - set(raw.columns))
    for col in OPTIONAL:
        if col not in src.columns:
            src[col] = pd.Series(pd.NA, index=src.index, dtype="string")
    for col in REQUIRED | set(OPTIONAL):
        src[col] = normalize_text(src[col])

    # Keep identifiers as text, including leading zeros.
    src["unique_key"] = src["unique_key"].astype("string")
    src["bbl"] = src["bbl"].astype("string")
    src["address_normalized"] = normalize_address(src["incident_address"])
    src["borough_normalized"] = src["borough"].str.upper().str.replace(r"\s+", " ", regex=True)
    src["created_date"] = pd.to_datetime(src["created_date"], errors="coerce")
    src["closed_date"] = pd.to_datetime(src["closed_date"], errors="coerce")
    src["resolution_action_updated_date"] = pd.to_datetime(
        src["resolution_action_updated_date"], errors="coerce"
    )

    null_key = src["unique_key"].isna()
    duplicates = src["unique_key"].duplicated(keep=False) & ~null_key
    duplicate_keys = int(src.loc[duplicates, "unique_key"].nunique())
    conflicting_keys = 0
    if duplicates.any():
        # If same unique_key has divergent content, stop: silent overwrite would hide data conflicts.
        check_cols = sorted(REQUIRED | set(OPTIONAL))
        for _, g in src.loc[duplicates].groupby("unique_key"):
            if g[check_cols].astype("string").drop_duplicates().shape[0] > 1:
                conflicting_keys += 1
    if conflicting_keys:
        raise ValueError(f"KHONG DAT: {conflicting_keys} conflicting unique_key(s). Resolve at source.")

    invalid_created = src["created_date"].isna()
    invalid_type = ~src["complaint_type"].isin(ALLOWED)
    # Treat missing/invalid keys or timestamps as unusable for the main analysis, retain counts in audit.
    invalid_mask = null_key | invalid_created | invalid_type.fillna(True)
    df = src.loc[~invalid_mask].drop_duplicates("unique_key", keep="first").copy()

    df["closure_valid"] = (
        df["closed_date"].notna()
        & (df["closed_date"] >= df["created_date"])
    )
    df["closure_hours"] = (
        (df["closed_date"] - df["created_date"]).dt.total_seconds() / 3600
    ).where(df["closure_valid"])
    df["has_address"] = df["address_normalized"].notna()
    df["has_bbl"] = df["bbl"].str.fullmatch(r"[1-5]\d{9}", na=False)
    # Usable address means a recorded house number + street and a known borough.
    # This is a syntax/identity check, not geocoding or proof of a physical building.
    address_syntax = df["address_normalized"].str.match(r"^\d+[A-Z]?(?:[-/]\d+[A-Z]?)?\s+\S+", na=False)
    known_borough = df["borough_normalized"].isin(["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"])
    df["has_valid_address"] = address_syntax & known_borough
    df["has_location_key"] = df["has_valid_address"]
    # For a building comparison, never group by BBL alone.
    # BBL+address is a location identifier, not a verified building ID.
    df["location_key"] = pd.Series(pd.NA, index=df.index, dtype="string")
    both = df["has_valid_address"] & df["has_bbl"]
    df.loc[both, "location_key"] = "BBL:" + df.loc[both, "bbl"] + "|" + df.loc[both, "address_normalized"]
    df.loc[both, "location_key"] += "|BOROUGH:" + df.loc[both, "borough_normalized"].fillna("<MISSING>")
    df.loc[both, "location_key"] += "|ZIP:" + df.loc[both, "incident_zip"].fillna("<MISSING>")
    addr_only = df["has_valid_address"] & ~df["has_bbl"]
    df.loc[addr_only, "location_key"] = (
        "ADDR:" + df.loc[addr_only, "borough_normalized"] + "|ZIP:"
        + df.loc[addr_only, "incident_zip"].fillna("<MISSING>") + "|" + df.loc[addr_only, "address_normalized"]
    )
    df["location_confidence"] = "not_linked"
    df.loc[addr_only, "location_confidence"] = "address_only"
    df.loc[both, "location_confidence"] = "bbl_and_address"
    df["complaint_day"] = df["created_date"].dt.normalize()
    df["month"] = df["created_date"].dt.to_period("M").astype("string")
    # No guess about physical building quality, incidents, unique reporters, or actual repair time.

    bbl_map = (
        df.loc[df["bbl"].notna()]
        .groupby("bbl", dropna=False)
        .agg(requests=("unique_key", "nunique"),
             distinct_addresses=("address_normalized", "nunique"),
             first_request=("created_date", "min"),
             last_request=("created_date", "max"))
        .reset_index()
        .sort_values("requests", ascending=False)
    )
    locations = (
        df.loc[df["location_key"].notna()]
        .groupby(["location_key", "bbl", "borough_normalized", "address_normalized", "location_confidence"],
                 dropna=False)
        .agg(requests=("unique_key", "nunique"),
             complaint_days=("complaint_day", "nunique"),
             active_months=("month", "nunique"),
             first_request=("created_date", "min"),
             last_request=("created_date", "max"))
        .reset_index().sort_values("requests", ascending=False)
    )
    audit = {
        "missing_source_columns": missing_source_columns,
        "normalization_rule": "case_and_whitespace_only_v1; no alias merging or inferred BBL/address",
        "source_rows": len(src), "clean_rows": len(df),
        "removed_unusable_rows": int(invalid_mask.sum()),
        "missing_unique_key": int(null_key.sum()),
        "duplicate_unique_keys": duplicate_keys,
        "invalid_created_date": int(invalid_created.sum()),
        "out_of_scope_complaint_type": int(invalid_type.fillna(True).sum()),
        "missing_bbl_raw": int(src["bbl"].isna().sum()),
        "invalid_bbl_raw": int((src["bbl"].notna() & ~src["bbl"].str.fullmatch(r"[1-5]\d{9}", na=False)).sum()),
        "missing_incident_zip_raw": int(src["incident_zip"].isna().sum()),
        "invalid_optional_dates": {
            col: int((normalize_text(raw[col]).notna() & pd.to_datetime(raw[col], errors="coerce").isna()).sum())
            for col in ("closed_date", "resolution_action_updated_date") if col in raw
        },
        "missing_incident_address_raw": int(src["address_normalized"].isna().sum()),
        "missing_resolution_description_raw": int(src["resolution_description"].isna().sum()),
        "unlinked_location_rows": int((~df["has_location_key"]).sum()),
        "usable_address_rows": int(df["has_valid_address"].sum()),
        "excluded_address_rows": int((~df["has_valid_address"]).sum()),
        "address_exclusion_reasons": {
            "missing_source_address": int((~df["has_address"]).sum()),
            "missing_house_number_or_street": int((df["has_address"] & ~address_syntax).sum()),
            "missing_or_unknown_borough": int((address_syntax & ~known_borough).sum()),
        },
        "address_validation_rule": "recorded_house_number_and_street_plus_known_borough_v1; no geocoding",
        "address_only_location_rows": int(addr_only.sum()),
        "bbl_and_address_location_rows": int(both.sum()),
        "missing_closed_date_clean": int(df["closed_date"].isna().sum()),
        "closed_before_created_clean": int(
            (df["closed_date"].notna() & (df["closed_date"] < df["created_date"])).sum()
        ),
        "bbl_with_multiple_addresses": int((bbl_map["distinct_addresses"] > 1).sum()),
        "unique_bbl": int(df["bbl"].nunique()),
        "unique_location_keys": int(df["location_key"].nunique()),
        "percent_missing_bbl_raw": round(100 * src["bbl"].isna().mean(), 2) if len(src) else None,
        "percent_missing_incident_address_raw": round(100 * src["address_normalized"].isna().mean(), 2) if len(src) else None,
        "percent_rows_unlinked_clean": round(100 * (~df["has_location_key"]).mean(), 2) if len(df) else None,
        "first_created_date_clean": str(df["created_date"].min()) if len(df) else None,
        "last_created_date_clean": str(df["created_date"].max()) if len(df) else None,
        "important_note": (
            "Counts are recorded 311 requests, not unique incidents or verified building problems. "
            "BBL is a lot, not necessarily a single building; 'closed' is administrative closure. "
            "Noise - Residential address does not prove noise originated at that building."
        ),
    }
    # Evidence candidates are displayed separately; neither spelling is replaced.
    evidence = df.loc[df["bbl"].notna() & df["borough_normalized"].notna(),
                      ["bbl", "borough_normalized", "address_normalized"]].drop_duplicates()
    evidence["candidate"] = evidence["address_normalized"].str.replace(
        r"\bNY AVENUE$", "NEW YORK AVENUE", regex=True)
    aliases = []
    for (bbl, borough, candidate), group in evidence.groupby(["bbl", "borough_normalized", "candidate"]):
        forms = sorted(group["address_normalized"].dropna().unique().tolist())
        if len(forms) > 1:
            aliases.append({"bbl": str(bbl), "borough": str(borough), "candidate": candidate,
                            "observed_forms": forms, "decision": "separate_pending_verification"})
    audit["address_alias_candidates"] = aliases
    return df, audit, bbl_map, locations


def run_pipeline(input_path: Path, processed_dir: Path = PROCESSED, output_dir: Path = OUTPUT) -> dict:
    if not input_path.is_file():
        raise FileNotFoundError(f"KHONG - Missing source file: {input_path}")
    raw = read_input(input_path)
    df, audit, bbl_map, locations = clean_profile(raw)
    if df.empty:
        raise ValueError("KHONG DAT - No valid rows for analysis")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    processed_dir = processed_dir / run_id
    output_dir = output_dir / "rental_runs" / run_id
    processed_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)
    clean_path = processed_dir / "nyc311_rental_pilot_clean.parquet"
    tmp = clean_path.with_name("nyc311_rental_pilot_clean.tmp.parquet")
    df.to_parquet(tmp, index=False)
    tmp.replace(clean_path)
    bbl_map.to_csv(output_dir / "rental_bbl_address_quality.csv", index=False, encoding="utf-8-sig")
    locations.to_csv(output_dir / "rental_location_summary.csv", index=False, encoding="utf-8-sig")
    df.groupby(["complaint_type", "status"], dropna=False).size().reset_index(name="requests").to_csv(
        output_dir / "rental_type_status_summary.csv", index=False, encoding="utf-8-sig"
    )
    audit["input_file"] = str(input_path)
    audit["processed_file"] = str(clean_path)
    audit["processed_at_utc"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "rental_quality_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("=== CHAT LUONG DU LIEU ===")
    for key, val in audit.items():
        if key not in ("important_note", "input_file", "processed_file", "processed_at_utc"):
            print(f"{key}: {val}")
    print("\n=== BBL VA SO DIA CHI ===")
    print(bbl_map.to_string(index=False))
    print("\n=== TOP 10 DIA DIEM THEO BBL + DIA CHI (CHUA XAC MINH LA TOA NHA) ===")
    print(locations.head(10).to_string(index=False))
    print("\nCO - Luu raw nguyen ven, da tao clean Parquet va audit JSON.")
    if audit["bbl_with_multiple_addresses"]:
        print("CANH BAO - Co BBL co nhieu dia chi: CHUA duoc coi moi BBL la 1 toa nha.")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=RAW_DEFAULT if RAW_DEFAULT.exists() else FALLBACK)
    args = parser.parse_args()
    run_pipeline(args.input)


if __name__ == "__main__":
    main()
