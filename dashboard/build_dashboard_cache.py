from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"

REQUEST_CUBE_FILE = TABLE_DIR / "dashboard_request_cube.parquet"
TIME_CUBE_FILE = TABLE_DIR / "dashboard_time_cube.parquet"
BOARD_CUBE_FILE = TABLE_DIR / "dashboard_board_cube.parquet"
AGENCY_CUBE_FILE = TABLE_DIR / "dashboard_agency_cube.parquet"

CLOSURE_OVERALL_FILE = (
    TABLE_DIR
    / "closure_overall_summary_final.parquet"
)

CACHE_METADATA_FILE = (
    TABLE_DIR
    / "dashboard_cache_metadata.json"
)


# ============================================================
# HELPERS
# ============================================================

def atomic_write_parquet(
    df: pd.DataFrame,
    destination: Path,
) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = destination.with_name(
        destination.name + ".tmp.parquet"
    )

    df.to_parquet(
        tmp,
        index=False,
        compression="snappy",
    )

    tmp.replace(destination)


def combine_grouped(
    parts: list[pd.DataFrame],
    dimensions: list[str],
) -> pd.DataFrame:
    if not parts:
        return pd.DataFrame(
            columns=dimensions + ["requests"]
        )

    return (
        pd.concat(parts, ignore_index=True)
        .groupby(
            dimensions,
            dropna=False,
            as_index=False,
        )["requests"]
        .sum()
        .sort_values(dimensions)
        .reset_index(drop=True)
    )


# ============================================================
# BUILD
# ============================================================

def main() -> None:
    TABLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    processed_files = sorted(
        PROCESSED_DIR.glob(
            "nyc311_*_processed.parquet"
        )
    )

    if not processed_files:
        raise FileNotFoundError(
            f"No processed parquet files found in "
            f"{PROCESSED_DIR}"
        )

    if len(processed_files) != 48:
        print(
            "WARNING:",
            f"expected 48 monthly processed files, "
            f"found {len(processed_files)}.",
        )

    request_parts = []
    time_parts = []
    board_parts = []
    agency_parts = []
    closure_arrays = []

    total_rows = 0

    needed_columns = [
        "created_date",
        "year",
        "month",
        "borough",
        "complaint_type",
        "open_data_channel_type",
        "community_board",
        "agency",
        "agency_name",
        "closure_valid",
        "closure_hours",
    ]

    for idx, file in enumerate(
        processed_files,
        start=1,
    ):
        part = pd.read_parquet(
            file,
            columns=needed_columns,
        )

        total_rows += len(part)

        # -------------------------
        # Request cube
        # -------------------------
        request_part = (
            part
            .groupby(
                [
                    "year",
                    "month",
                    "borough",
                    "complaint_type",
                    "open_data_channel_type",
                ],
                dropna=False,
                as_index=False,
            )
            .size()
            .rename(
                columns={"size": "requests"}
            )
        )

        request_parts.append(
            request_part
        )

        # -------------------------
        # Time cube
        # -------------------------
        time_source = part[
            [
                "created_date",
                "year",
                "borough",
                "complaint_type",
            ]
        ].copy()

        time_source["weekday_num"] = (
            time_source[
                "created_date"
            ].dt.dayofweek
        )

        time_source["hour"] = (
            time_source[
                "created_date"
            ].dt.hour
        )

        time_part = (
            time_source
            .groupby(
                [
                    "year",
                    "borough",
                    "complaint_type",
                    "weekday_num",
                    "hour",
                ],
                dropna=False,
                as_index=False,
            )
            .size()
            .rename(
                columns={"size": "requests"}
            )
        )

        time_parts.append(
            time_part
        )

        # -------------------------
        # Community Board cube
        # -------------------------
        board_part = (
            part
            .groupby(
                [
                    "year",
                    "borough",
                    "complaint_type",
                    "community_board",
                ],
                dropna=False,
                as_index=False,
            )
            .size()
            .rename(
                columns={"size": "requests"}
            )
        )

        board_parts.append(
            board_part
        )

        # -------------------------
        # Agency cube
        # -------------------------
        agency_part = (
            part
            .dropna(
                subset=["agency"]
            )
            .groupby(
                [
                    "year",
                    "borough",
                    "complaint_type",
                    "agency",
                    "agency_name",
                ],
                dropna=False,
                as_index=False,
            )
            .size()
            .rename(
                columns={"size": "requests"}
            )
        )

        agency_parts.append(
            agency_part
        )

        # -------------------------
        # Closure overall
        # -------------------------
        valid_closure = (
            part.loc[
                part[
                    "closure_valid"
                ].fillna(False),
                "closure_hours",
            ]
            .dropna()
            .to_numpy(
                dtype="float64"
            )
        )

        if len(valid_closure):
            closure_arrays.append(
                valid_closure
            )

        print(
            f"[{idx:02d}/{len(processed_files)}] "
            f"{file.name}: {len(part):,} rows"
        )

    request_cube = combine_grouped(
        request_parts,
        [
            "year",
            "month",
            "borough",
            "complaint_type",
            "open_data_channel_type",
        ],
    )

    time_cube = combine_grouped(
        time_parts,
        [
            "year",
            "borough",
            "complaint_type",
            "weekday_num",
            "hour",
        ],
    )

    board_cube = combine_grouped(
        board_parts,
        [
            "year",
            "borough",
            "complaint_type",
            "community_board",
        ],
    )

    agency_cube = combine_grouped(
        agency_parts,
        [
            "year",
            "borough",
            "complaint_type",
            "agency",
            "agency_name",
        ],
    )

    atomic_write_parquet(
        request_cube,
        REQUEST_CUBE_FILE,
    )

    atomic_write_parquet(
        time_cube,
        TIME_CUBE_FILE,
    )

    atomic_write_parquet(
        board_cube,
        BOARD_CUBE_FILE,
    )

    atomic_write_parquet(
        agency_cube,
        AGENCY_CUBE_FILE,
    )

    if closure_arrays:
        closure_values = np.concatenate(
            closure_arrays
        )

        valid_count = len(
            closure_values
        )

        invalid_count = (
            total_rows
            - valid_count
        )

        closure_summary = pd.DataFrame(
            {
                "metric": [
                    "Total requests",
                    "Valid closure requests",
                    "Invalid/unavailable closure",
                    "Valid closure (%)",
                    "Mean closure hours",
                    "Median closure hours",
                    "P75 closure hours",
                    "P90 closure hours",
                    "P95 closure hours",
                    "P99 closure hours",
                    "Maximum closure hours",
                ],
                "value": [
                    total_rows,
                    valid_count,
                    invalid_count,
                    (
                        valid_count
                        / total_rows
                        * 100
                    ),
                    np.mean(
                        closure_values
                    ),
                    np.median(
                        closure_values
                    ),
                    np.quantile(
                        closure_values,
                        0.75,
                    ),
                    np.quantile(
                        closure_values,
                        0.90,
                    ),
                    np.quantile(
                        closure_values,
                        0.95,
                    ),
                    np.quantile(
                        closure_values,
                        0.99,
                    ),
                    np.max(
                        closure_values
                    ),
                ],
            }
        )

    else:
        closure_summary = pd.DataFrame(
            {
                "metric": [
                    "Total requests",
                    "Valid closure requests",
                    "Invalid/unavailable closure",
                    "Valid closure (%)",
                    "Mean closure hours",
                    "Median closure hours",
                    "P75 closure hours",
                    "P90 closure hours",
                    "P95 closure hours",
                    "P99 closure hours",
                    "Maximum closure hours",
                ],
                "value": [
                    total_rows,
                    0,
                    total_rows,
                    0,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                ],
            }
        )

    atomic_write_parquet(
        closure_summary,
        CLOSURE_OVERALL_FILE,
    )

    metadata = {
        "built_at": datetime.now(
            ZoneInfo(
                "America/New_York"
            )
        ).isoformat(),
        "processed_files": len(
            processed_files
        ),
        "processed_rows": int(
            total_rows
        ),
        "request_cube_rows": int(
            len(request_cube)
        ),
        "time_cube_rows": int(
            len(time_cube)
        ),
        "board_cube_rows": int(
            len(board_cube)
        ),
        "agency_cube_rows": int(
            len(agency_cube)
        ),
    }

    tmp_meta = (
        CACHE_METADATA_FILE
        .with_suffix(".tmp")
    )

    with open(
        tmp_meta,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            metadata,
            f,
            ensure_ascii=False,
            indent=2,
        )

    tmp_meta.replace(
        CACHE_METADATA_FILE
    )

    print("\nDASHBOARD CACHE BUILD COMPLETE")
    print(
        "Processed rows:",
        f"{total_rows:,}",
    )
    print(
        "Request cube:",
        f"{len(request_cube):,}",
    )
    print(
        "Time cube:",
        f"{len(time_cube):,}",
    )
    print(
        "Board cube:",
        f"{len(board_cube):,}",
    )
    print(
        "Agency cube:",
        f"{len(agency_cube):,}",
    )
    print(
        "Closure summary:",
        CLOSURE_OVERALL_FILE.name,
    )


if __name__ == "__main__":
    main()
