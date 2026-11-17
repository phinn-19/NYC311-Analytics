"""Capacity-gated geographic backfill and resumable Compare reconciliation.

Default: plan only. --backfill initializes coverage; --sync requires completed backfill.
No historical citywide Parquet is read or modified. All checkpoints live in PostgreSQL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    from .rental_collect import COLUMNS, DATE_COLUMNS, NYCClient, CompareScope, add_scope_arguments, scope_from_args, nyc_now
    from .rental_quality import clean_profile
except ImportError:
    from rental_collect import COLUMNS, DATE_COLUMNS, NYCClient, CompareScope, add_scope_arguments, scope_from_args, nyc_now
    from rental_quality import clean_profile

ROOT = Path(__file__).resolve().parents[1]
TABLE = "public.nyc311_rental_demo"
RUNS = "public.nyc311_rental_sync_runs"
COVERAGE = "public.nyc311_rental_coverage"
STAGE = "public.nyc311_rental_stage"
MEMBERS = "public.nyc311_rental_membership"
SAFE_BYTES = 400_000_000  # headroom below the verified 500 MB Free allowance
COLS = list(COLUMNS) + ["address_normalized", "location_key"]
# Whole ZIPs, all four complaint types, since 2022; never a row sample.
MVP_ZIPS = (("QUEENS", "11377"), ("MANHATTAN", "10044"), ("BROOKLYN", "11231"),
            ("BRONX", "10464"), ("STATEN ISLAND", "10307"))


class CapacityLimitError(ValueError):
    """Contains only measured byte counts and the configured ceiling, never credentials."""


def connect(url):
    return psycopg.connect(url, sslmode="require", connect_timeout=20,
                          prepare_threshold=None, autocommit=True, row_factory=dict_row)


def capacity(conn, estimate=0, budget=SAFE_BYTES):
    used = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]
    if not 0 < budget <= SAFE_BYTES or used + estimate >= budget:
        raise CapacityLimitError(f"Capacity gate: used={used}, estimated_extra={estimate}, safe_limit={budget}; "
                                 "scope was not truncated. Narrow geography explicitly or free database capacity.")
    return int(used)


def ensure_schema(conn):
    # Inspect the existing table, then add only compatible missing columns.
    with conn.transaction():
        existing = {r["column_name"]: r["data_type"] for r in conn.execute(
            "SELECT column_name,data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='nyc311_rental_demo'")}
        for col, typ in existing.items():
            if col in COLS:
                wanted = {"timestamp without time zone"} if col in DATE_COLUMNS else {"text", "character varying"}
                if typ not in wanted:
                    raise ValueError("Existing table has incompatible column: " + col)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (unique_key TEXT PRIMARY KEY)")
        for col in COLS:
            typ = "TIMESTAMP" if col in DATE_COLUMNS else "TEXT"
            conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {col} {typ}")
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS raw_source JSONB")
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS source_hash TEXT")
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS synced_at TIMESTAMPTZ")
        conn.execute(f"ALTER TABLE {TABLE} ALTER COLUMN synced_at SET DEFAULT now()")
        conn.execute(f"ALTER TABLE {TABLE} ALTER COLUMN unique_key SET NOT NULL")
        unique = conn.execute("SELECT 1 FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid "
                              "AND a.attnum=i.indkey[0] WHERE i.indrelid='public.nyc311_rental_demo'::regclass "
                              "AND i.indisunique AND i.indisvalid AND i.indnkeyatts=1 AND i.indpred IS NULL "
                              "AND a.attname='unique_key'").fetchone()
        if not unique:
            conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_rental_unique ON {TABLE}(unique_key)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_rental_demo_location_date ON {TABLE}(location_key,created_date)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_rental_demo_bbl_date ON {TABLE}(bbl,created_date)")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {COVERAGE} (
            scope_id TEXT PRIMARY KEY, scope JSONB NOT NULL, status TEXT NOT NULL DEFAULT 'not_collected',
            coverage_start TIMESTAMP, coverage_end TIMESTAMP, last_success_at TIMESTAMPTZ,
            expected_rows BIGINT, actual_rows BIGINT, address_count BIGINT, missing_address_rows BIGINT,
            min_created_date TIMESTAMP, max_created_date TIMESTAMP, quality JSONB,
            lease_owner TEXT, lease_until TIMESTAMPTZ)""")
        conn.execute(f"ALTER TABLE {COVERAGE} ADD COLUMN IF NOT EXISTS backfill_completed_at TIMESTAMPTZ")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {RUNS} (
            run_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, scope JSONB NOT NULL, plan JSONB NOT NULL,
            status TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ,
            expected_rows BIGINT NOT NULL, downloaded_rows BIGINT NOT NULL DEFAULT 0,
            inserted_rows BIGINT NOT NULL DEFAULT 0, updated_rows BIGINT NOT NULL DEFAULT 0,
            unchanged_rows BIGINT NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT, cursor JSONB, db_bytes_before BIGINT, db_bytes_after BIGINT)""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_rental_runs_scope ON {RUNS}(scope_id,started_at DESC)")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {STAGE} (
            run_id TEXT NOT NULL REFERENCES {RUNS}(run_id), unique_key TEXT NOT NULL,
            payload JSONB NOT NULL, PRIMARY KEY(run_id,unique_key))""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {MEMBERS} (
            scope_id TEXT NOT NULL REFERENCES {COVERAGE}(scope_id),
            unique_key TEXT NOT NULL REFERENCES {TABLE}(unique_key), PRIMARY KEY(scope_id,unique_key))""")
        # No anonymous HTTP exposure of raw addresses or sync control tables.
        for table in (TABLE, COVERAGE, RUNS, STAGE, MEMBERS):
            conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def claim(conn, scope, owner):
    with conn.transaction():
        conn.execute(f"INSERT INTO {COVERAGE}(scope_id,scope) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                     (scope.scope_id, Jsonb(asdict(scope))))
        row = conn.execute(f"SELECT *,lease_until > now() AS busy FROM {COVERAGE} WHERE scope_id=%s FOR UPDATE",
                           (scope.scope_id,)).fetchone()
        if row["busy"]:
            raise RuntimeError("Another ETL worker owns this scope; retry after its lease expires")
        if row["lease_owner"]:
            conn.execute(f"UPDATE {RUNS} SET status='failed',finished_at=now(),error_count=error_count+1,"
                         "error_code='LeaseExpired' WHERE scope_id=%s AND status='running'", (scope.scope_id,))
        conn.execute(f"UPDATE {COVERAGE} SET lease_owner=%s,lease_until=now()+interval '15 minutes' WHERE scope_id=%s",
                     (owner, scope.scope_id))


def fence(conn, scope_id, owner):
    row = conn.execute(f"UPDATE {COVERAGE} SET lease_until=now()+interval '15 minutes' "
                       "WHERE scope_id=%s AND lease_owner=%s AND lease_until>now() RETURNING scope_id",
                       (scope_id, owner)).fetchone()
    if not row:
        raise RuntimeError("ETL scope lease was lost; refusing to publish")


def can_resume(old, plan):
    return (old.get("plan", {}).get("source") == plan["source"]
            and old.get("expected_rows") == plan["expected_rows"]
            and json.dumps(old.get("scope"), sort_keys=True) == json.dumps(plan["scope"], sort_keys=True))


def require_backfill(conn, scope_id):
    exists = conn.execute("SELECT to_regclass('public.nyc311_rental_coverage') AS table_name").fetchone()["table_name"]
    if not exists:
        raise ValueError("Initial geographic backfill required; run with --backfill before scheduled --sync")
    has_column = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                              "AND table_name='nyc311_rental_coverage' AND column_name='backfill_completed_at'").fetchone()
    if not has_column:
        raise ValueError("Initial geographic backfill required for this schema version")
    row = conn.execute(f"SELECT scope_id FROM {COVERAGE} WHERE scope_id=%s AND status='complete' "
                       "AND backfill_completed_at IS NOT NULL", (scope_id,)).fetchone()
    if not row:
        raise ValueError("No completed initial backfill for this exact scope; scheduled collection blocked")


def initialized_scopes(conn):
    """Read the persisted backfill configuration; Actions never falls back to pilot BBLs."""
    exists = conn.execute("SELECT to_regclass('public.nyc311_rental_coverage') AS table_name").fetchone()["table_name"]
    if not exists:
        raise ValueError("No initialized geographic scope; run --backfill first")
    has_column = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                              "AND table_name='nyc311_rental_coverage' AND column_name='backfill_completed_at'").fetchone()
    if not has_column:
        raise ValueError("No geographic backfill has been completed with this version")
    rows = conn.execute(f"SELECT scope FROM {COVERAGE} WHERE status='complete' AND backfill_completed_at IS NOT NULL "
                        "AND scope_id LIKE 'geo-v2-%' ORDER BY scope_id").fetchall()
    if not rows:
        raise ValueError("No completed geographic backfill; scheduled collection blocked")
    now = nyc_now()
    return [CompareScope(**dict(r["scope"], end_exclusive=now)) for r in rows]


def prepare_run(conn, client, scope, max_records, budget, owner):
    old = conn.execute(f"SELECT * FROM {RUNS} WHERE scope_id=%s AND status IN ('running','failed') "
                       "ORDER BY started_at DESC LIMIT 1", (scope.scope_id,)).fetchone()
    if old:
        frozen = CompareScope(**old["scope"])
        resumed_plan = client.plan(frozen, max_records)
        if can_resume(old, resumed_plan):
            if not resumed_plan["within_record_limit"]:
                raise ValueError("Resumed scope exceeds record limit")
            capacity(conn, resumed_plan["estimated_peak_additional_bytes"], budget)
            with conn.transaction():
                fence(conn, scope.scope_id, owner)
                conn.execute(f"UPDATE {RUNS} SET status='running',finished_at=NULL,error_code=NULL WHERE run_id=%s",
                             (old["run_id"],))
            return frozen, old["plan"], old["run_id"], old["cursor"]
        with conn.transaction():
            fence(conn, scope.scope_id, owner)
            conn.execute(f"UPDATE {RUNS} SET status='superseded',finished_at=now() WHERE run_id=%s", (old["run_id"],))
            conn.execute(f"DELETE FROM {STAGE} WHERE run_id=%s", (old["run_id"],))
    plan = client.plan(scope, max_records)
    print(json.dumps(plan, ensure_ascii=True))
    if not plan["within_record_limit"]:
        raise ValueError("Record limit exceeded; choose a smaller explicit scope")
    used = capacity(conn, plan["estimated_peak_additional_bytes"], budget)
    plan["database_rows_before"] = conn.execute(f"SELECT count(*) AS n FROM {TABLE}").fetchone()["n"]
    run_id = str(uuid4())
    with conn.transaction():
        fence(conn, scope.scope_id, owner)
        conn.execute(f"INSERT INTO {RUNS}(run_id,scope_id,scope,plan,status,expected_rows,db_bytes_before) "
                     "VALUES(%s,%s,%s,%s,'running',%s,%s)",
                     (run_id, scope.scope_id, Jsonb(asdict(scope)), Jsonb(plan), plan["expected_rows"], used))
    return scope, plan, run_id, None


def store_page(conn, scope, owner, run_id, batch, cursor, expected, budget):
    keys = [str(r["unique_key"]) for r in batch]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate source keys within a page")
    with conn.transaction():
        fence(conn, scope.scope_id, owner)
        capacity(conn, sum(len(json.dumps(r).encode()) * 4 + 2048 for r in batch), budget)
        existing = {r["unique_key"]: r["payload"] for r in conn.execute(
            f"SELECT unique_key,payload FROM {STAGE} WHERE run_id=%s AND unique_key=ANY(%s)", (run_id, keys))}
        for key, payload in zip(keys, batch):
            if key in existing and existing[key] != payload:
                raise ValueError("Conflicting source payload during acquisition")
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO {STAGE}(run_id,unique_key,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                            [(run_id, key, Jsonb(payload)) for key, payload in zip(keys, batch)])
        n = conn.execute(f"SELECT count(*) AS n FROM {STAGE} WHERE run_id=%s", (run_id,)).fetchone()["n"]
        if n > expected:
            raise ValueError("Staged rows exceed planned count")
        conn.execute(f"UPDATE {RUNS} SET cursor=%s,downloaded_rows=%s WHERE run_id=%s", (Jsonb(cursor), n, run_id))
        capacity(conn, budget=budget)


def convert(value):
    if pd.isna(value):
        return None
    return value.to_pydatetime() if isinstance(value, pd.Timestamp) else str(value)


def source_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save_raw_parquet(raw, path, owner):
    frame = pd.DataFrame.from_records(raw, columns=COLUMNS)
    if path.exists():
        # A resumed publication must preserve the exact already-saved generation.
        pd.testing.assert_frame_equal(pd.read_parquet(path), frame, check_dtype=False)
        return
    temporary = path.with_name(f".raw.{owner}.tmp.parquet")
    frame.to_parquet(temporary, index=False, compression="snappy")
    temporary.replace(path)


def publish(conn, scope, owner, run_id, raw, plan, budget, initial_backfill=False):
    df, audit, _, _ = clean_profile(pd.DataFrame.from_records(raw, columns=COLUMNS))
    if (audit["removed_unusable_rows"] or audit["duplicate_unique_keys"]
            or any(audit["invalid_optional_dates"].values())
            or len(df) != plan["expected_rows"]):
        raise ValueError("Quality validation failed; no partial publication")
    if len(df) and not scope.contains(df).all():
        raise ValueError("Source returned rows outside the frozen scope")
    audit["validation_passed"] = True
    audit["source_consistency"] = "generation_and_count_checked; not a transactional source snapshot"
    df = df.loc[df.has_valid_address & df.location_key.notna()].copy()
    audit["published_rows"] = len(df)
    audit["published_address_count"] = int(df.location_key.nunique())
    audit["published_first_created"] = convert(df.created_date.min())
    audit["published_last_created"] = convert(df.created_date.max())
    # JSON audit timestamps are represented as strings, source date columns remain timestamps.
    for field in ("published_first_created", "published_last_created"):
        if audit[field] is not None:
            audit[field] = str(audit[field])
    valid_keys = set(df.unique_key.astype(str))
    payloads = {str(r["unique_key"]): r for r in raw if str(r["unique_key"]) in valid_keys}
    keys = list(payloads)
    all_cols = COLS + ["raw_source", "source_hash"]
    sql = (f"INSERT INTO {TABLE} AS stored ({','.join(all_cols)}) VALUES ({','.join(['%s']*len(all_cols))}) "
           "ON CONFLICT(unique_key) DO UPDATE SET "
           + ",".join(f"{c}=EXCLUDED.{c}" for c in all_cols if c != "unique_key")
           + ",synced_at=now() WHERE stored.source_hash IS DISTINCT FROM EXCLUDED.source_hash")
    with conn.transaction():
        fence(conn, scope.scope_id, owner)
        # A global transaction lock prevents concurrent scopes changing the same detail during publication.
        conn.execute("SELECT pg_advisory_xact_lock(3112026)")
        capacity(conn, sum(len(json.dumps(r).encode()) * 4 + 2048 for r in payloads.values()), budget)
        prior = {r["unique_key"]: r["source_hash"] for r in conn.execute(
            f"SELECT unique_key,source_hash FROM {TABLE} WHERE unique_key=ANY(%s)", (keys,))}
        inserted = sum(k not in prior for k in keys)
        updated = sum(k in prior and prior[k] != source_hash(payloads[k]) for k in keys)
        with conn.cursor() as cur:
            cur.executemany(sql, [tuple(convert(v) for v in row) +
                                  (Jsonb(payloads[str(row[0])]), source_hash(payloads[str(row[0])]))
                                  for row in df[COLS].itertuples(index=False, name=None)])
        # Update only membership differences, avoiding daily churn of every row.
        conn.execute(f"DELETE FROM {MEMBERS} WHERE scope_id=%s AND NOT(unique_key=ANY(%s))", (scope.scope_id, keys))
        conn.execute(f"INSERT INTO {MEMBERS}(scope_id,unique_key) SELECT %s,unique_key FROM {STAGE} "
                     "WHERE run_id=%s AND unique_key=ANY(%s) ON CONFLICT DO NOTHING", (scope.scope_id, run_id, keys))
        confirmed = conn.execute(f"SELECT count(*) AS n FROM {MEMBERS} WHERE scope_id=%s", (scope.scope_id,)).fetchone()["n"]
        if confirmed != len(df):
            raise ValueError("PostgreSQL reconciliation count mismatch")
        used = capacity(conn, budget=budget)
        conn.execute(f"""UPDATE {COVERAGE} SET scope=%s,status='complete',coverage_start=%s,coverage_end=%s,
            last_success_at=now(),expected_rows=%s,actual_rows=%s,address_count=%s,missing_address_rows=%s,
            min_created_date=%s,max_created_date=%s,quality=%s,
            backfill_completed_at=CASE WHEN %s THEN now() ELSE backfill_completed_at END WHERE scope_id=%s""",
            (Jsonb(asdict(scope)), scope.start, scope.end_exclusive, plan["expected_rows"], confirmed,
             audit["published_address_count"], audit["missing_incident_address_raw"],
             convert(df.created_date.min()), convert(df.created_date.max()), Jsonb(audit), initial_backfill, scope.scope_id))
        conn.execute(f"""UPDATE {RUNS} SET status='complete',finished_at=now(),inserted_rows=%s,
            updated_rows=%s,unchanged_rows=%s,db_bytes_after=%s,plan=%s WHERE run_id=%s""",
            (inserted, updated, confirmed-inserted-updated, used, Jsonb(plan), run_id))
        conn.execute(f"DELETE FROM {STAGE} WHERE run_id=%s", (run_id,))
    return audit


def sync(conn, client, scope, max_records=50_000, budget=SAFE_BYTES, initial_backfill=False):
    if not initial_backfill:
        # This gate runs before any source API request or database schema mutation.
        require_backfill(conn, scope.scope_id)
    capacity(conn, budget=budget)
    ensure_schema(conn)
    owner, run_id = str(uuid4()), None
    claim(conn, scope, owner)
    try:
        scope, plan, run_id, cursor = prepare_run(conn, client, scope, max_records, budget, owner)
        plan["operation"] = "initial_backfill" if initial_backfill else "scheduled_full_reconciliation"
        while True:
            batch = client.page(scope, cursor)
            if not batch:
                break
            next_cursor = [batch[-1]["created_date"], str(batch[-1]["unique_key"])]
            if cursor == next_cursor:
                raise ValueError("Pagination did not advance")
            store_page(conn, scope, owner, run_id, batch, next_cursor, plan["expected_rows"], budget)
            cursor = next_cursor
        raw = [r["payload"] for r in conn.execute(f"SELECT payload FROM {STAGE} WHERE run_id=%s ORDER BY unique_key", (run_id,))]
        if (len(raw) != plan["expected_rows"] or client.count(scope) != len(raw)
                or client.metadata() != plan["source"]):
            raise ValueError("Source changed or download incomplete; checkpoint retained, coverage unchanged")
        # Immutable raw artifact; Actions uploads it even if publication later fails.
        out = ROOT / "data/rental/runs" / run_id
        out.mkdir(parents=True, exist_ok=True)
        raw_path = out / "raw.parquet"
        save_raw_parquet(raw, raw_path, owner)
        plan["raw_parquet_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        plan["raw_parquet_bytes"] = raw_path.stat().st_size
        plan["downloaded_rows"] = len(raw)
        plan["acquisition_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        (out / "manifest.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        audit = publish(conn, scope, owner, run_id, raw, plan, budget, initial_backfill)
        (out / "quality.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        return report(conn)
    except Exception as exc:
        if run_id:
            with conn.transaction():
                fence(conn, scope.scope_id, owner)
                conn.execute(f"UPDATE {RUNS} SET status='failed',finished_at=now(),error_count=error_count+1,error_code=%s WHERE run_id=%s AND status='running'",
                             (type(exc).__name__, run_id))
        else:
            with conn.transaction():
                fence(conn, scope.scope_id, owner)
                conn.execute(f"INSERT INTO {RUNS}(run_id,scope_id,scope,plan,status,expected_rows,error_count,error_code,finished_at) "
                             "VALUES(%s,%s,%s,%s,'failed',0,1,%s,now())",
                             (str(uuid4()), scope.scope_id, Jsonb(asdict(scope)), Jsonb({}), type(exc).__name__))
        raise
    finally:
        conn.execute(f"UPDATE {COVERAGE} SET lease_owner=NULL,lease_until=NULL WHERE scope_id=%s AND lease_owner=%s",
                     (scope.scope_id, owner))


def report(conn):
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return report_snapshot(conn)


def report_snapshot(conn):
    coverage = conn.execute(f"SELECT scope_id,scope,status,coverage_start,coverage_end,last_success_at,expected_rows,"
                            f"actual_rows,address_count,missing_address_rows,min_created_date,max_created_date,backfill_completed_at,quality FROM {COVERAGE} ORDER BY scope_id").fetchall()
    latest = conn.execute(f"SELECT run_id,scope_id,status,started_at,finished_at,expected_rows,downloaded_rows,plan,"
                          f"inserted_rows,updated_rows,unchanged_rows,error_count,error_code,db_bytes_before,db_bytes_after FROM {RUNS} ORDER BY started_at DESC LIMIT 10").fetchall()
    totals = conn.execute(f"SELECT count(*) AS requests,count(DISTINCT location_key) AS addresses,"
                          f"count(*) FILTER(WHERE address_normalized IS NULL) AS missing_addresses,"
                          f"min(created_date) AS first_created,max(created_date) AS last_created FROM {TABLE} "
                          f"WHERE unique_key IN (SELECT unique_key FROM {MEMBERS})").fetchone()
    metrics = {r["scope_id"]: r for r in conn.execute(
        f"SELECT m.scope_id,count(*) AS requests,count(DISTINCT d.location_key) AS addresses "
        f"FROM {MEMBERS} m JOIN {TABLE} d ON d.unique_key=m.unique_key GROUP BY m.scope_id")}
    checks = []
    for row in coverage:
        if row["status"] != "complete" or not row["backfill_completed_at"]:
            continue
        actual = metrics.get(row["scope_id"], {"requests": 0, "addresses": 0})
        quality = row["quality"] or {}
        checks.append({"scope_id": row["scope_id"],
                       "source_count_verified": quality.get("source_rows") == row["expected_rows"],
                       "eligible_plus_excluded_equals_source": (quality.get("published_rows", -1)
                           + quality.get("excluded_address_rows", -1) == row["expected_rows"]),
                       "postgres_count_verified": actual["requests"] == row["actual_rows"],
                       "address_count_verified": actual["addresses"] == row["address_count"]})
    verification_passed = bool(checks) and all(all(v for k, v in check.items() if k != "scope_id") for check in checks)
    return {"coverage": coverage, "latest_runs": latest, "published_totals": totals,
            "verification": {"passed": verification_passed, "scopes": checks},
            "completed_geographic_backfills": sum(bool(r["backfill_completed_at"]) and r["status"] == "complete" for r in coverage),
            "total_stored_rows_including_legacy": conn.execute(f"SELECT count(*) AS n FROM {TABLE}").fetchone()["n"],
            "database_bytes": conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]}


def mvp_scopes(end=None):
    cutoff = end or nyc_now()
    return [CompareScope(end_exclusive=cutoff, boroughs=(borough,), incident_zips=(zipcode,))
            for borough, zipcode in MVP_ZIPS]


def mvp_status(snapshot):
    """Only reconciled PostgreSQL coverage can establish a completed borough ZIP."""
    coverage = {row["scope_id"]: row for row in snapshot.get("coverage", [])}
    checks = {row["scope_id"]: row for row in snapshot.get("verification", {}).get("scopes", [])}
    results = []
    for scope in mvp_scopes():
        row = coverage.get(scope.scope_id, {})
        check = checks.get(scope.scope_id, {})
        try:
            matching_scope = CompareScope(**row["scope"]).scope_id == scope.scope_id
        except (KeyError, TypeError, ValueError):
            matching_scope = False
        required = ("source_count_verified", "eligible_plus_excluded_equals_source",
                    "postgres_count_verified", "address_count_verified")
        verified = (matching_scope and row.get("status") == "complete" and bool(row.get("backfill_completed_at"))
                    and bool(row.get("last_success_at"))
                    and (row.get("quality") or {}).get("validation_passed") is True
                    and (row.get("actual_rows") or 0) > 0 and (row.get("address_count") or 0) > 0
                    and all(check.get(field) is True for field in required))
        results.append({"borough": scope.boroughs[0], "zip": scope.incident_zips[0],
                        "scope_id": scope.scope_id, "verified_complete": verified,
                        "status": row.get("status", "not_collected"),
                        "source_rows": row.get("expected_rows"), "published_rows": row.get("actual_rows"),
                        "addresses": row.get("address_count"), "coverage_start": row.get("coverage_start"),
                        "missing_address_rows": row.get("missing_address_rows"),
                        "first_created": row.get("min_created_date"), "last_created": row.get("max_created_date"),
                        "coverage_end_exclusive": row.get("coverage_end"),
                        "last_success_at": row.get("last_success_at")})
    return {"all_five_verified": all(row["verified_complete"] for row in results), "scopes": results}


def plan_mvp(conn, client, max_records=50_000, budget=SAFE_BYTES, end=None):
    """Read-only preflight: count every ZIP, reserve for all unfinished scopes together."""
    if not 0 < budget <= SAFE_BYTES:
        raise CapacityLimitError(f"Invalid safe_limit={budget}; maximum={SAFE_BYTES}")
    snapshot = report(conn) if conn is not None else {}
    status = mvp_status(snapshot)
    completed = {row["scope_id"] for row in status["scopes"] if row["verified_complete"]}
    plans = []
    for scope in mvp_scopes(end):
        item = client.plan(scope, max_records)
        item["action"] = "skip_verified_complete" if scope.scope_id in completed else "backfill_or_resume"
        plans.append(item)
    pending = [item for item in plans if item["action"] == "backfill_or_resume"]
    estimate = sum(item["estimated_peak_additional_bytes"] for item in pending)
    used = snapshot.get("database_bytes")
    return {"operation": "five_borough_whole_zip_preflight", "scopes": plans,
            "coverage_before": status, "database_checked": conn is not None, "database_bytes": used,
            "database_rows_before": snapshot.get("total_stored_rows_including_legacy"),
            "published_totals_before": snapshot.get("published_totals"),
            "safe_limit_bytes": budget, "pending_source_rows": sum(item["expected_rows"] for item in pending),
            "estimated_pending_peak_additional_bytes": estimate,
            "can_backfill": (conn is not None and used is not None and used + estimate < budget
                             and all(item["within_record_limit"] for item in pending)),
            "note": "No bulk collection; up to 100 source rows per ZIP probed for size. "
                    "Completed ZIPs retain their recorded cutoff until --sync runs."}


def backfill_mvp(conn, client, max_records=50_000, budget=SAFE_BYTES, end=None):
    plan = plan_mvp(conn, client, max_records, budget, end)
    print(json.dumps(plan, indent=2, default=str, ensure_ascii=True), flush=True)
    if not plan["can_backfill"]:
        raise CapacityLimitError(f"MVP gate blocked: used={plan['database_bytes']}, "
                                 f"estimated_extra={plan['estimated_pending_peak_additional_bytes']}, "
                                 f"safe_limit={budget}; inspect per-ZIP counts/limits above. No ZIP was truncated.")
    pending = [item for item in plan["scopes"] if item["action"] == "backfill_or_resume"]
    for index, item in enumerate(pending):
        capacity(conn, sum(p["estimated_peak_additional_bytes"] for p in pending[index:]), budget)
        # Existing sync persists each page + cursor atomically, and resumes its frozen scope.
        snapshot = sync(conn, client, CompareScope(**item["scope"]), max_records, budget, initial_backfill=True)
        checked = {row["scope_id"]: row for row in mvp_status(snapshot)["scopes"]}
        if not checked[item["scope_id"]]["verified_complete"]:
            raise ValueError("Completed ZIP failed PostgreSQL reconciliation; remaining ZIPs not started")
    result = report(conn)
    result["mvp"] = mvp_status(result)
    if not result["mvp"]["all_five_verified"] or not result["verification"]["passed"]:
        raise ValueError("Five-borough MVP verification failed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_scope_arguments(parser)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--backfill", action="store_true", help="Initialize all historical data for an explicit geographic scope")
    mode.add_argument("--sync", action="store_true")
    mode.add_argument("--report", action="store_true")
    mode.add_argument("--plan-mvp", action="store_true", help="Count five complete ZIP scopes; never bulk download")
    mode.add_argument("--backfill-mvp", action="store_true", help="Sequentially backfill/resume missing five-borough ZIP scopes")
    mode.add_argument("--report-mvp", action="store_true", help="Verify all five ZIP scopes against PostgreSQL; fail if incomplete")
    parser.add_argument("--budget-mb", type=int, default=400)
    args = parser.parse_args()
    if (args.plan_mvp or args.backfill_mvp) and (args.bbl or args.borough or args.incident_zips
                                                or args.start != "2022-01-01T00:00:00"):
        parser.error("MVP uses the documented five whole ZIPs since 2022; use --backfill for another explicit scope")
    url = os.getenv("DATABASE_URL")
    if not url:
        if args.plan_mvp:
            print(json.dumps(plan_mvp(None, NYCClient(), args.max_records,
                                      args.budget_mb*1_000_000, args.end), indent=2, default=str))
            return
        raise SystemExit("DATABASE_URL is required. Set the existing Supabase SSL pooler URI before running backfill/report.")
    with connect(url) as conn:
        if args.plan_mvp:
            result = plan_mvp(conn, NYCClient(), args.max_records, args.budget_mb*1_000_000, args.end)
        elif args.backfill_mvp:
            result = backfill_mvp(conn, NYCClient(), args.max_records, args.budget_mb*1_000_000, args.end)
        elif args.report or args.report_mvp:
            result = report(conn)
            result["mvp"] = mvp_status(result)
        elif args.backfill:
            result = sync(conn, NYCClient(), scope_from_args(args), args.max_records,
                          args.budget_mb*1_000_000, initial_backfill=True)
        elif args.sync:
            explicit = (args.bbl or args.borough or args.incident_zips or args.end
                        or args.start != "2022-01-01T00:00:00")
            scopes = [scope_from_args(args)] if explicit else initialized_scopes(conn)
            for scope in scopes:
                sync(conn, NYCClient(), scope, args.max_records, args.budget_mb*1_000_000)
            result = report(conn)
        else:
            result = NYCClient().plan(scope_from_args(args), args.max_records)
            result["database_bytes"] = capacity(conn, result["estimated_peak_additional_bytes"], args.budget_mb*1_000_000)
            if not result["within_record_limit"]:
                raise ValueError("Scope exceeds record limit")
        print(json.dumps(result, indent=2, default=str, ensure_ascii=True))
        if args.report and not result["verification"]["passed"]:
            raise SystemExit("Backfill verification has not passed; see the measured coverage and counts above.")
        if args.report_mvp and not (result["verification"]["passed"] and result["mvp"]["all_five_verified"]):
            raise SystemExit("Five-borough ZIP coverage is incomplete or not reconciled; see mvp.scopes above.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # URLs/passwords may appear in driver exceptions; never print exception contents.
        message = str(exc) if isinstance(exc, CapacityLimitError) else "Check secrets, source, initialized backfill and capacity."
        raise SystemExit(f"ETL failed ({type(exc).__name__}); no successful coverage claimed. {message}") from None
