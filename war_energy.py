"""Iran war energy cost per household, updated weekly from EIA data.

Method: for every day since the war began, take
    (actual price - EIA's pre-war forecast price for that month) x actual gallons used,
add up the days, and divide by US households.

- Actual prices: EIA weekly retail gasoline and diesel (Monday survey). Each
  survey price is applied to the seven days starting that Monday.
- Pre-war forecast: EIA Short-Term Energy Outlook, Feb 5 2026 (before the war),
  monthly retail gasoline, retail diesel and jet fuel refiner price. Stored in
  data/eia_steo_feb2026_forecast.csv so the run doesn't need the STEO workbook.
- Volumes: EIA Weekly Petroleum Status Report product supplied (week ending
  Friday) for finished gasoline, distillate and kerosene-type jet fuel. Days
  after the latest volume week reuse the average of the last four weeks.
- Jet fuel: EIA has no weekly retail jet price, so the actual price is the
  daily Gulf Coast jet spot price plus the Jan-Feb 2026 average gap between
  EIA's monthly refiner price and that spot price (JET_REFINER_MARGIN).

The tally runs through the last day covered by the latest weekly retail
survey. Jet fuel carries its latest daily spot price forward to that day.
Writes data/war_energy.json.

Run weekly (EIA posts retail prices Monday evening and volumes Wednesday).
"""

import csv
import io
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

REPO_DIR = Path(__file__).resolve().parent
FORECAST_CSV = REPO_DIR / "data" / "eia_steo_feb2026_forecast.csv"
OUTPUT_JSON = REPO_DIR / "data" / "war_energy.json"

WAR_START = date(2026, 3, 1)
HOUSEHOLDS = 131e6  # same divisor as Brown's tracker
GALLONS_PER_BARREL = 42

# EIA monthly jet refiner price minus Gulf Coast jet spot, Jan-Feb 2026 average
# (Jan: 2.101 - 2.015 = 0.086, Feb: 2.328 - 2.265 = 0.063).
JET_REFINER_MARGIN = 0.075

# Share of each fuel's extra cost that reaches households.
# Gasoline: households buy 801 gallons a year (BLS Consumer Expenditure Survey
#   2024, $2,645 / EIA 2024 average retail price $3.304) out of 1,037 gallons
#   per household of total US use (EIA 2024 product supplied, 8.84M b/d / 131M) = 0.77.
# Diesel: consumer spending's share of GDP (BEA, PCE / GDP, Q2 2026 = 0.68)
#   times an assumed 0.7 share passed into prices so far.
# Jet: commercial aviation's 85% of jet fuel (EIA, Today in Energy, Jul 8 2024;
#   military/government and general aviation excluded) times the diesel rule.
CONSUMER_SHARE_OF_GDP = 0.68
PASS_THROUGH_TO_DATE = 0.7
HOUSEHOLD_SHARE = {
    "gasoline": 0.77,
    "diesel": CONSUMER_SHARE_OF_GDP * PASS_THROUGH_TO_DATE,
    "jet": 0.85 * CONSUMER_SHARE_OF_GDP * PASS_THROUGH_TO_DATE,
}

# Suffix "w" is the weekly history file, "d" the daily one.
EIA_HISTORY_URL = "https://www.eia.gov/dnav/pet/hist_xls/{series}{frequency}.xls"

# fuel -> (weekly price series, weekly volume series, forecast column)
FUELS = {
    "gasoline": ("EMM_EPMR_PTE_NUS_DPG", "WGFUPUS2", "gasoline_retail"),
    "diesel": ("EMD_EPD2D_PTE_NUS_DPG", "WDIUPUS2", "diesel_retail"),
    "jet": ("EER_EPJK_PF4_RGC_DPG", "WKJUPUS2", "jet_refiner"),  # daily spot price
}


def fetch_eia_series(series_id: str, frequency: str = "w") -> pd.Series:
    """Download one EIA history spreadsheet ("w" weekly, "d" daily) as a date-indexed Series."""
    url = EIA_HISTORY_URL.format(series=series_id, frequency=frequency)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw_bytes = urlopen(request, timeout=60).read()
    frame = pd.read_excel(io.BytesIO(raw_bytes), sheet_name="Data 1", skiprows=2)
    frame.columns = ["date", "value"]
    frame = frame.dropna()
    return pd.Series(frame["value"].astype(float).values, index=pd.to_datetime(frame["date"]).dt.date)


def load_prewar_forecast() -> dict[str, dict[str, float]]:
    """Return {forecast column: {"2026-03": price, ...}} from the stored Feb 2026 STEO forecast."""
    forecast: dict[str, dict[str, float]] = {}
    with open(FORECAST_CSV, newline="") as handle:
        for row in csv.DictReader(handle):
            for column, value in row.items():
                if column != "month":
                    forecast.setdefault(column, {})[row["month"]] = float(value)
    return forecast


def daily_volume_gallons(volumes_kbd: pd.Series, day: date) -> float:
    """Gallons used on `day`, from the weekly product-supplied figure (thousand barrels/day).

    A weekly figure for the week ending Friday F covers F-6 through F. Days past
    the latest week use the mean of the last four weeks.
    """
    for week_end, kbd in volumes_kbd.items():
        if week_end - timedelta(days=6) <= day <= week_end:
            return kbd * 1000 * GALLONS_PER_BARREL
    return volumes_kbd.iloc[-4:].mean() * 1000 * GALLONS_PER_BARREL


def main() -> None:
    forecast = load_prewar_forecast()
    results: dict[str, dict] = {}

    # Retail surveys are Mondays, so the latest one covers through the following Sunday.
    # Every fuel is tallied through the end of the week both retail series cover.
    retail_prices = {fuel: fetch_eia_series(FUELS[fuel][0]) for fuel in ("gasoline", "diesel")}
    last_day = min(series.index[-1] for series in retail_prices.values()) + timedelta(days=6)

    for fuel, (price_id, volume_id, forecast_column) in FUELS.items():
        if fuel == "jet":
            prices = fetch_eia_series(price_id, frequency="d") + JET_REFINER_MARGIN
        else:
            prices = retail_prices[fuel]
        volumes = fetch_eia_series(volume_id)
        volumes = volumes[volumes.index >= WAR_START - timedelta(days=7)]

        total_dollars = 0.0
        by_month: dict[str, float] = {}
        day = WAR_START
        while day <= last_day:
            # Price in effect on this day: the most recent weekly observation on or before it.
            actual_price = prices[prices.index <= day].iloc[-1]
            month_key = f"{day.year}-{day.month:02d}"
            counterfactual_price = forecast[forecast_column][month_key]
            dollars = (actual_price - counterfactual_price) * daily_volume_gallons(volumes, day)
            total_dollars += dollars
            by_month[month_key] = by_month.get(month_key, 0.0) + dollars
            day += timedelta(days=1)

        results[fuel] = {
            "latest_price_date": prices.index[prices.index <= last_day][-1].isoformat(),
            "total_billion": round(total_dollars / 1e9, 1),
            "per_household": round(total_dollars / HOUSEHOLDS),
            "household_share_per_household": round(total_dollars * HOUSEHOLD_SHARE[fuel] / HOUSEHOLDS),
            "latest_price": round(float(prices[prices.index <= last_day].iloc[-1]), 3),
            "prewar_forecast_price": forecast[forecast_column][f"{last_day.year}-{last_day.month:02d}"],
            "by_month_per_household": {m: round(v / HOUSEHOLDS) for m, v in by_month.items()},
        }

    output = {
        "method": "Actual EIA weekly prices minus EIA Feb 2026 STEO pre-war forecast, times EIA weekly volumes, divided by 131M households",
        "war_start": WAR_START.isoformat(),
        "through": last_day.isoformat(),
        "fuels": results,
        "gross_per_household": sum(r["per_household"] for r in results.values()),
        "household_share_per_household": sum(r["household_share_per_household"] for r in results.values()),
        "updated": date.today().isoformat(),
    }
    OUTPUT_JSON.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k != "fuels"}, indent=2))
    for fuel, r in results.items():
        print(f"{fuel:9s} ${r['total_billion']:6.1f}B  ${r['per_household']:5d} gross  ${r['household_share_per_household']:5d} household  latest price {r['latest_price_date']}")


if __name__ == "__main__":
    main()
