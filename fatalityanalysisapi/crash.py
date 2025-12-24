from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
from tabulate import tabulate
import matplotlib.pyplot as plt


# FARS 2022 Accidents (Feature Layer ID: 0)
# Layer page: https://services1.arcgis.com/4yjifSiIG17X0gW4/ArcGIS/rest/services/NTAD_FARS_2022/FeatureServer/0
# Query endpoint:
ARCGIS_QUERY_URL = (
    "https://services1.arcgis.com/4yjifSiIG17X0gW4/ArcGIS/rest/services/"
    "NTAD_FARS_2022/FeatureServer/0/query"
)

AZ_STATE_CODE = 4
YEAR = 2022

DEFAULT_OUTDIR = Path("out")
REQUEST_TIMEOUT = 45
MAX_RECORD_COUNT = 2000  # per service metadata


@dataclass(frozen=True)
class FetchSpec:
    state: int = AZ_STATE_CODE
    year: int = YEAR


def _request_json(session: requests.Session, url: str, params: Dict[str, Any], retries: int = 5) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            # ArcGIS errors come back as JSON like {"error": {...}}
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            return data
        except Exception as e:
            last_err = e
            # exponential backoff
            sleep_s = min(2 ** attempt, 20)
            time.sleep(sleep_s)
    raise RuntimeError(f"Request failed after {retries} retries. Last error: {last_err}") from last_err


def _where_clause(spec: FetchSpec) -> str:
    # Service has STATE and YEAR fields (see layer metadata).
    return f"STATE = {spec.state} AND YEAR = {spec.year}"


def get_count(session: requests.Session, where: str) -> int:
    params = {
        "f": "json",
        "where": where,
        "returnCountOnly": "true",
    }
    data = _request_json(session, ARCGIS_QUERY_URL, params)
    count = int(data.get("count", 0))
    return count


def fetch_all_features(session: requests.Session, where: str, out_fields: str = "*") -> List[Dict[str, Any]]:
    total = get_count(session, where)
    if total == 0:
        return []

    features: List[Dict[str, Any]] = []
    pages = math.ceil(total / MAX_RECORD_COUNT)

    for page_idx in range(pages):
        offset = page_idx * MAX_RECORD_COUNT
        params = {
            "f": "json",
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",  # lon/lat
            "resultRecordCount": str(MAX_RECORD_COUNT),
            "resultOffset": str(offset),
            "orderByFields": "OBJECTID",
        }
        data = _request_json(session, ARCGIS_QUERY_URL, params)
        page_features = data.get("features", [])
        features.extend(page_features)
        print(f"Fetched {len(page_features):5d} rows (offset {offset:5d})  | total so far: {len(features):5d}/{total}")

    return features


def features_to_df(features: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    for f in features:
        attrs = dict(f.get("attributes") or {})
        geom = f.get("geometry") or {}
        # Geometry typically returns {"x": lon, "y": lat} when outSR=4326
        if "x" in geom and "y" in geom:
            attrs["lon"] = geom["x"]
            attrs["lat"] = geom["y"]
        rows.append(attrs)

    df = pd.DataFrame(rows)

    # Create a usable date column if YEAR/MONTH/DAY exist
    for col in ("YEAR", "MONTH", "DAY"):
        if col not in df.columns:
            break
    else:
        df["crash_date"] = pd.to_datetime(
            dict(year=df["YEAR"], month=df["MONTH"], day=df["DAY"]),
            errors="coerce",
        )

    return df


def _value_counts_table(df: pd.DataFrame, col: str, n: int = 10) -> pd.DataFrame:
    if col not in df.columns:
        return pd.DataFrame({"note": [f"Column '{col}' not present in dataset."]})
    vc = df[col].fillna("Unknown").value_counts().head(n)
    out = vc.rename_axis(col).reset_index(name="fatal_crashes")
    return out


def save_charts(df: pd.DataFrame, outdir: Path) -> List[Tuple[str, str]]:
    """
    Returns list of (title, filename) for embedding in HTML.
    """
    charts: List[Tuple[str, str]] = []
    outdir.mkdir(parents=True, exist_ok=True)

    def bar_chart(counts: pd.Series, title: str, fname: str, xlabel: str = "", ylabel: str = "Fatal crashes"):
        plt.figure()
        counts.plot(kind="bar")
        plt.title(title)
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.tight_layout()
        path = outdir / fname
        plt.savefig(path, dpi=160)
        plt.close()
        charts.append((title, fname))

    if "MONTHNAME" in df.columns:
        month_counts = df["MONTHNAME"].fillna("Unknown").value_counts()
        # order months if possible
        month_order = ["January","February","March","April","May","June","July","August","September","October","November","December"]
        month_counts = month_counts.reindex([m for m in month_order if m in month_counts.index] + [i for i in month_counts.index if i not in month_order])
        bar_chart(month_counts, "Arizona fatal crashes by month (2022)", "by_month.png", xlabel="Month")

    if "DAY_WEEKNAME" in df.columns:
        dow_counts = df["DAY_WEEKNAME"].fillna("Unknown").value_counts()
        dow_order = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
        dow_counts = dow_counts.reindex([d for d in dow_order if d in dow_counts.index] + [i for i in dow_counts.index if i not in dow_order])
        bar_chart(dow_counts, "Arizona fatal crashes by day of week (2022)", "by_dayofweek.png", xlabel="Day of week")

    if "HOUR" in df.columns:
        hour_counts = df["HOUR"].fillna(-1).astype(int).replace({-1: 99}).value_counts().sort_index()
        # replace 99 label for unknown
        hour_counts.index = ["Unknown" if i == 99 else str(i) for i in hour_counts.index]
        bar_chart(hour_counts, "Arizona fatal crashes by hour (2022)", "by_hour.png", xlabel="Hour (0-23)")

    return charts


def write_html_report(
    df: pd.DataFrame,
    outdir: Path,
    charts: List[Tuple[str, str]],
    tables: List[Tuple[str, pd.DataFrame]],
    title: str,
) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    report_path = outdir / "report.html"

    html_tables = "\n".join(
        f"<h2>{tbl_title}</h2>\n{tbl_df.to_html(index=False, escape=True)}"
        for tbl_title, tbl_df in tables
    )
    html_charts = "\n".join(
        f"<h2>{chart_title}</h2>\n<img src='{fname}' style='max-width: 100%; height: auto;' />"
        for chart_title, fname in charts
    )

    summary = {
        "Rows (fatal crashes)": len(df),
        "Columns": len(df.columns),
    }

    report_html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 10px 0 24px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; }}
    th {{ background: #f4f4f4; text-align: left; }}
    h1 {{ margin-top: 0; }}
    .meta {{ margin: 12px 0 24px 0; }}
    code {{ background: #f6f6f6; padding: 2px 4px; border-radius: 4px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="meta">
    <div><b>Summary</b></div>
    <ul>
      {''.join(f"<li>{k}: {v}</li>" for k, v in summary.items())}
    </ul>
    <div>Generated from the USDOT/BTS-hosted FARS 2022 Accidents feature service.</div>
  </div>

  {html_charts}

  {html_tables}

  <h2>Sample rows</h2>
  {df.head(25).to_html(index=False, escape=True)}

</body>
</html>
"""
    report_path.write_text(report_html, encoding="utf-8")
    return report_path


def print_console_summary(df: pd.DataFrame):
    print("\n==============================")
    print("FARS 2022 (AZ) — Console Summary")
    print("==============================\n")
    print(f"Fatal crashes (rows): {len(df):,}\n")

    candidates = [
        ("Top counties", "COUNTYNAME", 15),
        ("Top cities", "CITYNAME", 15),
        ("By month", "MONTHNAME", 12),
        ("By day of week", "DAY_WEEKNAME", 7),
        ("By light condition", "LGT_CONDNAME", 10),
        ("By weather", "WEATHERNAME", 10),
        ("By rural/urban", "RUR_URBNAME", 5),
        ("By route type", "ROUTENAME", 10),
    ]

    for title, col, n in candidates:
        t = _value_counts_table(df, col, n=n)
        print(f"\n--- {title} ---")
        print(tabulate(t, headers="keys", tablefmt="github", showindex=False))


def main():
    parser = argparse.ArgumentParser(description="Download and summarize FARS 2022 fatal crashes for Arizona.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Output directory (default: out)")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    spec = FetchSpec()
    where = _where_clause(spec)

    with requests.Session() as session:
        session.headers.update({"User-Agent": "fars-az-2022-script/1.0"})
        features = fetch_all_features(session, where)

    if not features:
        print("No records returned. Exiting.")
        return

    df = features_to_df(features)

    # Save raw data
    csv_path = outdir / "fars_az_2022_accidents.csv"
    df.to_csv(csv_path, index=False)
    print(f"\nSaved CSV: {csv_path.resolve()}")

    # Console output
    print_console_summary(df)

    # Charts + HTML report
    charts = save_charts(df, outdir)

    tables = [
        ("Top counties (fatal crashes)", _value_counts_table(df, "COUNTYNAME", n=20)),
        ("Top cities (fatal crashes)", _value_counts_table(df, "CITYNAME", n=20)),
        ("Light condition distribution", _value_counts_table(df, "LGT_CONDNAME", n=20)),
        ("Weather distribution", _value_counts_table(df, "WEATHERNAME", n=20)),
        ("Rural/Urban distribution", _value_counts_table(df, "RUR_URBNAME", n=20)),
    ]

    report_path = write_html_report(
        df=df,
        outdir=outdir,
        charts=charts,
        tables=tables,
        title="FARS 2022 — Arizona Fatal Crashes (Accidents)",
    )
    print(f"\nSaved HTML report: {report_path.resolve()}")


if __name__ == "__main__":
    main()
