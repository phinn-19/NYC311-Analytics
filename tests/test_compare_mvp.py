"""Offline checks for whole-ZIP orchestration; fixtures are never published."""
import copy
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from unittest.mock import Mock, patch

from src import load_rental_clean as etl


CUTOFF = "2026-11-17T15:25:23"


def snapshot(scopes=()):
    rows, checks = [], []
    for scope in scopes:
        rows.append({"scope_id": scope.scope_id, "scope": asdict(scope), "status": "complete",
                     "backfill_completed_at": CUTOFF, "last_success_at": CUTOFF,
                     "expected_rows": 10, "actual_rows": 9, "address_count": 4,
                     "coverage_start": scope.start, "coverage_end": scope.end_exclusive,
                     "quality": {"validation_passed": True}})
        checks.append(dict(scope_id=scope.scope_id, source_count_verified=True,
                           eligible_plus_excluded_equals_source=True, postgres_count_verified=True,
                           address_count_verified=True))
    return {"coverage": rows, "verification": {"passed": bool(rows), "scopes": checks},
            "database_bytes": 50_000_000}


def client():
    result = Mock()
    result.plan.side_effect = lambda scope, limit: {
        "scope_id": scope.scope_id, "scope": asdict(scope), "expected_rows": 10,
        "estimated_peak_additional_bytes": 81920, "within_record_limit": limit >= 10}
    return result


class FiveBoroughBackfillTests(unittest.TestCase):
    def setUp(self):
        self.scopes = etl.mvp_scopes(CUTOFF)

    def test_scope_is_complete_zip_and_stable_across_resume_cutoffs(self):
        self.assertEqual(len({s.boroughs[0] for s in self.scopes}), 5)
        later = etl.mvp_scopes("2026-11-18T00:00:00")
        self.assertEqual([s.scope_id for s in self.scopes], [s.scope_id for s in later])
        for scope in self.scopes:
            self.assertEqual(scope.start, "2022-01-01T00:00:00")
            self.assertEqual(len(scope.complaint_types), 4)
            self.assertEqual(scope.bbls, ())
            self.assertIn("incident_zip IN", scope.where())
            self.assertIn("borough IN", scope.where())

    def test_status_requires_all_five_and_reconciled_matching_scope(self):
        good = snapshot(self.scopes)
        self.assertTrue(etl.mvp_status(good)["all_five_verified"])
        self.assertFalse(etl.mvp_status(snapshot(self.scopes[:1]))["all_five_verified"])
        for change in ("failed_check", "wrong_scope", "missing_quality"):
            bad = copy.deepcopy(good)
            if change == "failed_check":
                bad["verification"]["scopes"][2]["postgres_count_verified"] = False
            elif change == "wrong_scope":
                bad["coverage"][2]["scope"]["incident_zips"] = ["11201"]
            else:
                bad["coverage"][2]["quality"] = None
            self.assertFalse(etl.mvp_status(bad)["all_five_verified"], change)

    def test_no_database_still_counts_every_zip_but_cannot_authorize_download(self):
        api = client()
        plan = etl.plan_mvp(None, api, end=CUTOFF)
        self.assertEqual(api.plan.call_count, 5)
        self.assertEqual(plan["pending_source_rows"], 50)
        self.assertFalse(plan["database_checked"])
        self.assertFalse(plan["can_backfill"])

    def test_existing_queens_is_recounted_but_only_verified_coverage_skipped(self):
        with patch.object(etl, "report", return_value=snapshot(self.scopes[:1])):
            api = client()
            plan = etl.plan_mvp(object(), api, end=CUTOFF)
        self.assertEqual(api.plan.call_count, 5)
        self.assertEqual(plan["scopes"][0]["action"], "skip_verified_complete")
        self.assertEqual(plan["pending_source_rows"], 40)
        self.assertEqual(plan["estimated_pending_peak_additional_bytes"], 4 * 81920)

    def test_whole_batch_capacity_or_record_limit_blocks_before_first_sync(self):
        for used, limit in ((399_900_000, 50_000), (50_000_000, 5)):
            before = snapshot(self.scopes[:1])
            before["database_bytes"] = used
            with patch.object(etl, "report", return_value=before), patch.object(etl, "sync") as sync:
                with redirect_stdout(io.StringIO()), self.assertRaises(etl.CapacityLimitError):
                    etl.backfill_mvp(object(), client(), max_records=limit, end=CUTOFF)
                sync.assert_not_called()

    def test_failed_zip_stops_batch_and_rerun_skips_previous_success(self):
        completed = [self.scopes[0]]
        calls = []
        fail_once = [True]

        def run(conn, api, scope, max_records, budget, initial_backfill):
            self.assertTrue(initial_backfill)
            calls.append(scope.scope_id)
            if scope.scope_id == self.scopes[2].scope_id and fail_once[0]:
                fail_once[0] = False
                raise ConnectionError("synthetic interruption")
            completed.append(scope)
            return snapshot(completed)

        with patch.object(etl, "report", side_effect=lambda conn: snapshot(completed)), \
                patch.object(etl, "capacity", return_value=50_000_000) as capacity, \
                patch.object(etl, "sync", side_effect=run), redirect_stdout(io.StringIO()):
            with self.assertRaises(ConnectionError):
                etl.backfill_mvp(object(), client(), end=CUTOFF)
            self.assertEqual(calls, [self.scopes[1].scope_id, self.scopes[2].scope_id])
            result = etl.backfill_mvp(object(), client(), end=CUTOFF)
        self.assertTrue(result["mvp"]["all_five_verified"])
        self.assertEqual(calls.count(self.scopes[1].scope_id), 1)
        self.assertEqual(calls.count(self.scopes[2].scope_id), 2)
        self.assertEqual([call.args[1] for call in capacity.call_args_list],
                         [4 * 81920, 3 * 81920, 3 * 81920, 2 * 81920, 81920])

    def test_api_count_failure_does_not_start_any_sync(self):
        api = client()
        api.plan.side_effect = ConnectionError("synthetic API failure")
        with patch.object(etl, "report", return_value=snapshot(self.scopes[:1])), \
                patch.object(etl, "sync") as sync, self.assertRaises(ConnectionError):
            etl.backfill_mvp(object(), api, end=CUTOFF)
        sync.assert_not_called()

    def test_report_command_fails_partial_coverage_without_calling_api(self):
        with patch.dict(etl.os.environ, {"DATABASE_URL": "postgresql://fixture.invalid/db"}), \
                patch("sys.argv", ["load_rental_clean.py", "--report-mvp"]), \
                patch.object(etl, "connect"), \
                patch.object(etl, "report", return_value=snapshot(self.scopes[:1])), \
                patch.object(etl, "NYCClient") as api, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "incomplete"):
                etl.main()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
