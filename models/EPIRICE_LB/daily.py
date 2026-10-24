"""Daily all-station entry point using the existing downloaded weather folder."""
import os
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from models.EPIRICE_BLB.crop_calendar import CALENDAR_URL, STATIONS_URL
from models.EPIRICE_LB.predict import build_parser, run


def daily_arguments(environ=None):
    env = os.environ if environ is None else environ
    root = Path(env.get("PIPELINE_ROOT", Path(__file__).resolve().parents[2]))
    arguments = ["--all-stations", "--weather-dir", str(env.get("ERA5_INPUT_DIR", env.get("ERA5_OUTPUT_DIR", root / "ERA5"))),
                 "--output-dir", str(env.get("DATA_FOLDER", root / "rice_blast_prediction/data")),
                 "--state-dir", str(env.get("EPIRICE_LB_STATE_DIR", root / "rice_blast_prediction/epirice_lb_states")),
                 "--calendar", env.get("EPIRICE_CALENDAR_SOURCE", CALENDAR_URL),
                 "--station-list", env.get("EPIRICE_STATIONS_SOURCE", STATIONS_URL)]
    start = env.get("EPIRICE_LB_BACKFILL_START_DATE") or env.get("EPIRICE_BACKFILL_START_DATE")
    end = env.get("EPIRICE_LB_BACKFILL_END_DATE") or env.get("EPIRICE_BACKFILL_END_DATE")
    if bool(start) != bool(end):
        raise ValueError("LB backfill dates must be supplied together")
    if start:
        arguments += ["--start-date", start, "--end-date", end]
    return build_parser().parse_args(arguments)


if __name__ == "__main__":
    run(daily_arguments())
