"""
Reference implementation for EPIRICE bacterial blight.
This file reproduces the open-source 2023 epiRice/EPIRICE SEIR.R loop semantics
as closely as practical in Python, including its day indexing.

Input CSV columns:
YYYYMMDD, DOY, TEMP, RHUM, RAIN

Important:
- This is a source-parity implementation for software regression testing.
- Savary et al. (2012) Table 2 used aggregation a=1 for bacterial blight.
- The 2023 open-source predict_bacterial_blight.R snapshot uses a=4.
- Savary et al. (2012) describes wetness as daily MAX RH >90% OR rain >5 mm.
- The open-source helper accepts RHUM documented as mean daily RH and uses >= thresholds.
"""
import numpy as np
import pandas as pd

def interp_zero(x, xp, fp):
    return np.interp(x, xp, fp, left=0.0, right=0.0)

def simulate_epirice_blb(wth, config):
    n = config["duration_days"]
    if n < 1 or len(wth) != n:
        raise ValueError("Weather must contain exactly duration_days daily rows")
    values = wth[["TEMP", "RHUM", "RAIN"]].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Weather contains missing or non-finite values")
    age_pts = np.array(config["RcA_points"], dtype=float)
    temp_pts = np.array(config["RcT_points"], dtype=float)

    RcT = interp_zero(wth["TEMP"].to_numpy(), temp_pts[:,0], temp_pts[:,1])
    RcA = interp_zero(np.arange(0, n), age_pts[:,0], age_pts[:,1])

    fields = [
        "C","Rc","RcW","latency_event","infectious_event","intensity",
        "rsenesced","rgrowth","rtransfer","infection","diseased","senesced",
        "removed","I","L","H","TS"
    ]
    A = {k: np.zeros(n, dtype=float) for k in fields}
    prev_infday_r = None

    for idx in range(n):
        d = idx + 1

        if d == 1:
            A["H"][idx] = config["H0"]
            A["rsenesced"][idx] = config["RRS"] * A["H"][idx]
            latday_r = 1
            infday_r = 1
        else:
            if d > config["infectious_period_i_days"] and prev_infday_r is not None:
                removed_today = A["infectious_event"][prev_infday_r]
            else:
                removed_today = 0.0

            A["H"][idx] = (
                A["H"][idx-1] + A["rgrowth"][idx-1]
                - A["infection"][idx-1] - A["rsenesced"][idx-1]
            )
            A["rsenesced"][idx] = removed_today + config["RRS"] * A["H"][idx]
            A["senesced"][idx] = A["senesced"][idx-1] + A["rsenesced"][idx-1]

            A["latency_event"][idx] = A["infection"][idx-1]
            latday_r = max(1, d - config["latent_period_p_days"])
            A["L"][idx] = A["latency_event"][latday_r-1:idx+1].sum()

            A["infectious_event"][idx] = A["rtransfer"][idx-1]
            infday_r = max(1, d - config["infectious_period_i_days"])
            A["I"][idx] = A["infectious_event"][infday_r-1:idx+1].sum()

        A["RcW"][idx] = float(
            (wth["RHUM"].iloc[idx] >= config["rhlim_percent"])
            or (wth["RAIN"].iloc[idx] >= config["rainlim_mm_day"])
        )
        A["Rc"][idx] = config["RcOpt"] * RcA[idx] * RcT[idx] * A["RcW"][idx]

        A["diseased"][idx] = A["infectious_event"].sum() + A["L"][idx]
        A["removed"][idx] = A["infectious_event"].sum() - A["I"][idx]

        denominator = A["H"][idx] + A["diseased"][idx]
        A["C"][idx] = 1 - A["diseased"][idx] / denominator if denominator else 0.0

        if d == config["onset_index"]:
            A["infection"][idx] = config["I0"]
        elif d > config["onset_index"]:
            A["infection"][idx] = (
                A["I"][idx] * A["Rc"][idx] * (A["C"][idx] ** config["aggregation_a"])
            )

        if d >= config["latent_period_p_days"]:
            A["rtransfer"][idx] = A["latency_event"][latday_r-1]

        A["TS"][idx] = A["diseased"][idx] + A["H"][idx]
        A["rgrowth"][idx] = (
            config["RRG"] * A["H"][idx] * (1 - A["TS"][idx] / config["Sx"])
        )

        denominator2 = A["TS"][idx] - A["removed"][idx]
        A["intensity"][idx] = (
            (A["diseased"][idx] - A["removed"][idx]) / denominator2
            if denominator2 else 0.0
        )

        prev_infday_r = infday_r

    # Same trapezoidal integral, compatible with the pipeline's NumPy 1.26.
    increments = (A["intensity"][:-1] + A["intensity"][1:]) / 2
    audpc = float(np.sum(increments))

    return pd.DataFrame({
        "simday": np.arange(1, n+1),
        "date": wth["YYYYMMDD"],
        "TEMP": wth["TEMP"],
        "RHUM": wth["RHUM"],
        "RAIN": wth["RAIN"],
        "RcW": A["RcW"], "RcT": RcT, "RcA": RcA, "Rc": A["Rc"],
        "healthy_sites": A["H"], "latent": A["L"], "infectious": A["I"],
        "removed": A["removed"], "rateinf_new_infections": A["infection"],
        "rtransfer_L_to_I": A["rtransfer"], "diseased_total": A["diseased"],
        "healthy_fraction_C": A["C"], "intensity_active": A["intensity"],
        "AUDPC": audpc,
        "AUDPC_cumulative": np.r_[0.0, np.cumsum(increments)],
        "total_sites": A["TS"],
        "growth_new_sites": A["rgrowth"],
        "senescence_sites_rate": A["rsenesced"],
        "senesced_total": A["senesced"],
        "latent_cohort_entries": A["latency_event"],
        "infectious_cohort_entries": A["infectious_event"],
    })
