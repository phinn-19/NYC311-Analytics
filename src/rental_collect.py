"""Geographic NYC311 backfill planning and collection; historical Parquet is untouched."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
API = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
METADATA = "https://data.cityofnewyork.us/api/views/erm2-nwe9.json"
DEFAULT_BOROUGHS = ("QUEENS",)
DEFAULT_ZIPS = ("11377",)
BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}
TYPES = ("HEAT/HOT WATER", "Noise - Residential", "UNSANITARY CONDITION", "PLUMBING")
COLUMNS = (
    "unique_key", "created_date", "closed_date", "complaint_type", "descriptor",
    "incident_address", "street_name", "incident_zip", "bbl", "borough", "status",
    "resolution_description", "resolution_action_updated_date",
)
DATE_COLUMNS = ("created_date", "closed_date", "resolution_action_updated_date")
MAX_RECORDS = 50_000


def literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def nyc_now() -> str:
    return pd.Timestamp.now(tz="America/New_York").tz_localize(None).floor("s").isoformat()


@dataclass(frozen=True)
class CompareScope:
    start: str = "2022-01-01T00:00:00"
    end_exclusive: str = ""
    bbls: tuple[str, ...] = ()
    boroughs: tuple[str, ...] = DEFAULT_BOROUGHS
    incident_zips: tuple[str, ...] = DEFAULT_ZIPS
    complaint_types: tuple[str, ...] = TYPES

    def __post_init__(self):
        if not self.end_exclusive:
            object.__setattr__(self, "end_exclusive", nyc_now())
        for name in ("start", "end_exclusive"):
            ts = pd.Timestamp(getattr(self, name))
            if ts.tzinfo is not None or pd.isna(ts):
                raise ValueError("Scope dates must be valid NYC local timestamps without timezone")
            object.__setattr__(self, name, ts.isoformat())
        if pd.Timestamp(self.start) >= pd.Timestamp(self.end_exclusive):
            raise ValueError("Empty or reversed scope dates")
        if any(not re.fullmatch(r"[1-5]\d{9}", b) for b in self.bbls):
            raise ValueError("BBLs must contain 10 NYC digits")
        if not set(self.boroughs).issubset(BOROUGHS):
            raise ValueError("Unsupported borough")
        if any(not re.fullmatch(r"\d{5}", z) for z in self.incident_zips):
            raise ValueError("ZIP selectors must contain 5 digits")
        if not (self.bbls or (self.boroughs and self.incident_zips)):
            raise ValueError("Choose explicit BBLs or an explicit borough and ZIP area")
        if not self.complaint_types or not set(self.complaint_types).issubset(TYPES):
            raise ValueError("Unsupported complaint types")
        object.__setattr__(self, "bbls", tuple(sorted(set(self.bbls))))
        object.__setattr__(self, "boroughs", tuple(sorted(set(self.boroughs))))
        object.__setattr__(self, "incident_zips", tuple(sorted(set(self.incident_zips))))
        object.__setattr__(self, "complaint_types", tuple(sorted(set(self.complaint_types))))

    @property
    def scope_id(self) -> str:
        config = asdict(self)
        config.pop("end_exclusive")
        return "geo-v2-" + hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]

    def where(self) -> str:
        parts = [f"created_date >= {literal(self.start)}", f"created_date < {literal(self.end_exclusive)}",
                 f"complaint_type IN ({','.join(map(literal, self.complaint_types))})"]
        for column, values in (("bbl", self.bbls), ("borough", self.boroughs), ("incident_zip", self.incident_zips)):
            if values:
                parts.append(f"{column} IN ({','.join(map(literal, values))})")
        return " AND ".join(parts)

    def contains(self, frame: pd.DataFrame) -> pd.Series:
        valid = frame.created_date.ge(pd.Timestamp(self.start)) & frame.created_date.lt(pd.Timestamp(self.end_exclusive))
        valid &= frame.complaint_type.isin(self.complaint_types)
        for col, values in (("bbl", self.bbls), ("borough_normalized", self.boroughs), ("incident_zip", self.incident_zips)):
            if values:
                valid &= frame[col].isin(values)
        return valid


def session_with_retry() -> requests.Session:
    retry = Retry(total=4, connect=4, read=4, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"],
                  respect_retry_after_header=True)
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "NYC311-Analytics/2.0", "Accept": "application/json"})
    if os.getenv("SOCRATA_APP_TOKEN"):
        session.headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    return session


class NYCClient:
    def __init__(self, session=None):
        self.session = session or session_with_retry()
        self.key_type = "text"

    def metadata(self) -> dict:
        response = self.session.get(METADATA, timeout=(10, 65))
        response.raise_for_status()
        meta = response.json()
        schema = {c["fieldName"]: c["dataTypeName"] for c in meta["columns"]}
        missing = set(COLUMNS) - set(schema)
        if missing:
            raise ValueError("Source schema missing: " + ", ".join(sorted(missing)))
        for col in COLUMNS:
            allowed = {"floating_timestamp", "calendar_date"} if col in DATE_COLUMNS else {"text", "number"}
            if schema[col] not in allowed:
                raise ValueError("Incompatible source column: " + col)
        self.key_type = schema["unique_key"]
        if not meta.get("rowsUpdatedAt"):
            raise ValueError("Source generation marker unavailable")
        return {"generation": meta["rowsUpdatedAt"], "schema": {c: schema[c] for c in COLUMNS}}

    def query(self, params):
        response = self.session.get(API, params=params, timeout=(10, 65))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("API returned an unexpected payload")
        return payload

    def count(self, scope: CompareScope) -> int:
        return int(self.query({"$select": "count(*) AS n", "$where": scope.where()})[0]["n"])

    def page(self, scope: CompareScope, cursor=None, page_size=1000) -> list[dict]:
        if not 1 <= page_size <= 5000:
            raise ValueError("page_size must be between 1 and 5000")
        where = scope.where()
        if cursor:
            date, key = cursor
            if self.key_type == "number":
                if not re.fullmatch(r"\d+", str(key)):
                    raise ValueError("Invalid numeric source key")
                key_value = str(key)
            else:
                key_value = literal(key)
            where += (f" AND (created_date > {literal(date)} OR "
                      f"(created_date = {literal(date)} AND unique_key > {key_value}))")
        return self.query({"$select": ",".join(COLUMNS), "$where": where,
                           "$order": "created_date ASC,unique_key ASC", "$limit": page_size})

    def plan(self, scope: CompareScope, max_records=MAX_RECORDS) -> dict:
        before = self.metadata()
        expected = self.count(scope)
        probe = self.page(scope, page_size=min(expected, 100)) if expected else []
        raw_bytes = sum(len(json.dumps(r, ensure_ascii=False).encode()) for r in probe)
        avg = raw_bytes / len(probe) if probe else 1024
        # Peak detail + raw staging + indexes/TOAST/MVCC; this is an estimate.
        row_bytes = max(8192, int(avg * 8 + 2048))
        if self.metadata()["generation"] != before["generation"]:
            raise RuntimeError("Source changed during planning; retry")
        return {"dataset_id": "erm2-nwe9", "scope_id": scope.scope_id, "scope": asdict(scope),
                "source": before, "expected_rows": expected, "probe_rows": len(probe),
                "estimated_peak_additional_bytes": expected * row_bytes,
                "estimated_bytes_per_row": row_bytes,
                "max_records": min(max_records, MAX_RECORDS),
                "within_record_limit": expected <= min(max_records, MAX_RECORDS),
                "planned_at_utc": datetime.now(timezone.utc).isoformat(),
                "strategy": "geographic_full_reconciliation; full-history overlap every run"}


def collect(client: NYCClient, scope: CompareScope, plan: dict) -> tuple[pd.DataFrame, dict]:
    if not plan["within_record_limit"]:
        raise ValueError("Scope exceeds the record limit; narrow the explicit scope")
    items, cursor = [], None
    while True:
        batch = client.page(scope, cursor)
        if not batch:
            break
        items.extend(batch)
        if len(items) > plan["expected_rows"]:
            raise RuntimeError("Source count increased during collection")
        next_cursor = (batch[-1]["created_date"], str(batch[-1]["unique_key"]))
        if next_cursor == cursor:
            raise RuntimeError("Pagination cursor did not advance")
        cursor = next_cursor
    df = pd.DataFrame(items, columns=COLUMNS)
    after = client.count(scope)
    if (len(df) != plan["expected_rows"] or after != len(df)
            or df.unique_key.nunique() != len(df)
            or client.metadata()["generation"] != plan["source"]["generation"]):
        raise RuntimeError("Incomplete or changing source; acquisition not published")
    return df, dict(plan, downloaded_rows=len(df), source_count_after=after,
                    collected_at_utc=datetime.now(timezone.utc).isoformat())


def add_scope_arguments(parser):
    parser.add_argument("--start", default="2022-01-01T00:00:00")
    parser.add_argument("--end", help="Exclusive NYC local bound; defaults to run start")
    parser.add_argument("--bbl", action="append", help="Optional explicit BBL selector; no pilot BBL default")
    parser.add_argument("--borough", action="append", help="Repeat for explicit boroughs; default QUEENS")
    parser.add_argument("--zip", dest="incident_zips", action="append", help="Repeat for explicit ZIPs; default 11377")
    parser.add_argument("--max-records", type=int, default=MAX_RECORDS)


def scope_from_args(args):
    return CompareScope(start=args.start, end_exclusive=args.end or "", bbls=tuple(args.bbl or ()),
                        boroughs=tuple(args.borough or (() if args.bbl else DEFAULT_BOROUGHS)),
                        incident_zips=tuple(args.incident_zips or (() if args.bbl else DEFAULT_ZIPS)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_scope_arguments(parser)
    parser.add_argument("--collect", action="store_true", help="Explicit raw-only collection after planning")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/rental/raw")
    args = parser.parse_args()
    client, scope = NYCClient(), scope_from_args(args)
    plan = client.plan(scope, args.max_records)
    print(json.dumps(plan, indent=2, ensure_ascii=True))
    if not args.collect:
        return
    df, manifest = collect(client, scope, plan)
    out = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    df.to_parquet(out / "raw.parquet", index=False)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Collection failed ({type(exc).__name__}); no coverage published.") from None
