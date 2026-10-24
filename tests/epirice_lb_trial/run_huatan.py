"""Frozen historical-weather, single-station cloud smoke test; no downloads."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from models.EPIRICE_LB.predict import build_parser, run

FIXTURE = ROOT / "tests/fixtures/epirice_lb_huatan"


def smoke(output):
    output = output.resolve()
    if output == ROOT or any(output.is_relative_to(ROOT / p) for p in ("models", "tests", "rice_blast_prediction", ".git", ".github")):
        raise ValueError("Use a dedicated temporary result folder")
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads((FIXTURE / "provenance.json").read_text(encoding="utf-8"))
    for name, expected in provenance["fixture_sha256"].items():
        if hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Fixture hash mismatch: {name}")
    raw = gzip.decompress((FIXTURE / "hourly.csv.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != provenance["hourly_sha256"]:
        raise ValueError("Hourly source hash mismatch")
    weather = output / "weather"
    weather.mkdir(exist_ok=True)
    (weather / "C0G910_花壇_24.032076_120.549461.csv").write_bytes(raw)
    args = build_parser().parse_args([
        "--weather-dir", str(weather), "--calendar", str(FIXTURE / "regional_calendar.csv"),
        "--station-list", str(FIXTURE / "station_list.csv"), "--station-id", "C0G910",
        "--weather-provenance", str(FIXTURE / "provenance.json"),
        "--as-of", "2026-10-24", "--start-date", "2026-02-04", "--end-date", "2026-06-16",
        "--output-dir", str(output / "data"), "--state-dir", str(output / "states")])
    report = run(args)
    states = pd.read_csv(output / "states/2026_1/C0G910.csv", float_precision="round_trip")
    if len(states) != 120 or states.date.tolist() != pd.date_range("2026-02-05", "2026-06-04").strftime("%Y-%m-%d").tolist():
        raise ValueError("Unexpected full crop trajectory")
    if states.is_forecast.any() or states.status.ne("ok").any():
        raise ValueError("Invalid historical state status")
    fields = ["RcOpt", "RcA", "RcT", "RcW", "Rc", "healthy_sites", "latent", "infectious", "removed",
              "healthy_fraction_C", "rateinf_new_infections", "growth_new_sites", "senescence_sites_rate",
              "total_sites", "intensity_active", "AUDPC", "AUDPC_cumulative"]
    if not np.isfinite(states[fields].to_numpy(float)).all():
        raise ValueError("Missing full-state output")
    expected = {"ok": 120, "non_crop_period": 2, "outside_model_duration": 11}
    if report["output_status_counts"] != expected:
        raise ValueError("Unexpected daily boundary statuses")
    if len(list((output / "data").glob("*.csv"))) != 133:
        raise ValueError("Missing daily output files")
    for path in sorted((output / "data").glob("*.csv")):
        daily = pd.read_csv(path, dtype={"站號": str}, float_precision="round_trip")
        if len(daily) != 1 or daily["站號"].iloc[0] != "C0G910":
            raise ValueError("Not a single-station output")
        row = daily.iloc[0]
        if row.EPIRICE_LB_status == "ok":
            state = states.set_index("date").loc[row["日期"]]
            for field in fields:
                np.testing.assert_allclose(row["EPIRICE_LB_" + field], state[field], atol=0, rtol=0)
            assert row.EPIRICE_LB == state.rateinf_new_infections
        elif row.EPIRICE_LB_status == "non_crop_period":
            assert row.EPIRICE_LB == -99
        else:
            assert pd.isna(row.EPIRICE_LB)
    secondary = states[states.simday.gt(15)]
    peak = secondary.loc[secondary.rateinf_new_infections.idxmax()]
    np.testing.assert_allclose(peak.rateinf_new_infections, .8291641529751366, atol=1e-9, rtol=0)
    summary = {"status": "passed", "station_id": "C0G910", "state_rows": len(states),
               "daily_files": len(list((output / "data").glob("*.csv"))), "statuses": expected,
               "secondary_peak_date": peak.date, "secondary_peak_RI": float(peak.rateinf_new_infections),
               "weather_sha256": provenance["hourly_sha256"], "weather_source": provenance,
               "field_forecast_skill_validated": False, "R_parity_report": "../comparison.json"}
    (output / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("status", "state_rows", "daily_files", "secondary_peak_date")}, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.output)
