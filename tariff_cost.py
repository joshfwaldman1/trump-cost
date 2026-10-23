"""Tariff cost per household, from the NY Fed's estimated effect of tariffs on consumer goods prices.

Method (based on Amiti, Heise and Weinstein, NY Fed Staff Report 1201; the calculations are ours):
for each month from February 2025 through the tally date,
    p / (1 + p) x BEA consumer spending on goods excluding energy, that month
where p is the NY Fed's "total effect on the price level" (Liberty Street Economics,
Oct 6 2026, Chart 2a data), as a decimal. p / (1 + p) turns a percent price increase
into the share of observed spending that is tariff cost, because observed spending
already includes the increase. Values after February 2026 are the NY Fed's projections.

- Spending: BEA PCE durable + nondurable goods minus gasoline and other energy goods
  (FRED PCEDG, PCEND, DGOERC1Q027SBEA), annual rate / 12. Months BEA hasn't released
  yet repeat the latest month.
- Coverage: the NY Fed's 67 CPI categories are 20.2% of the CPI basket, out of 27.1%
  for consumer goods (Table A2). We keep 20.2 / 27.1 of the cost and count the rest as zero.
- The current month is prorated by days. Divided by 132.7M households (Census ACS 2024).

The tally runs through the same day as the war energy tally (data/war_energy.json)
so the two lines cover the same window. Writes data/tariffs.json.
"""

import calendar
import csv
import io
import json
from datetime import date
from pathlib import Path
from urllib.request import urlopen

REPO_DIR = Path(__file__).resolve().parent
NYFED_CSV = REPO_DIR / "data" / "nyfed_chart2a_price_level.csv"
WAR_ENERGY_JSON = REPO_DIR / "data" / "war_energy.json"
OUTPUT_JSON = REPO_DIR / "data" / "tariffs.json"

HOUSEHOLDS = 132.7e6  # Census ACS 2024 1-year, table DP02
COVERAGE = 20.2 / 27.1  # NY Fed SR1201 Table A2: covered CPI share / consumer goods share
FIRST_MONTH = "2025-02"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


def fetch_fred(series_id: str) -> dict[str, float]:
    """Return {"YYYY-MM": value} for a FRED series (quarterly series keyed by the quarter's first month)."""
    # FRED stalls on some custom User-Agent strings; the urllib default works.
    text = urlopen(FRED_CSV.format(series=series_id), timeout=60).read().decode()
    rows = list(csv.reader(io.StringIO(text)))[1:]
    return {d[:7]: float(v) for d, v in rows if v not in ("", ".")}


def latest_on_or_before(series: dict[str, float], month: str) -> tuple[float, str]:
    """Value for `month`, or the latest earlier month if it isn't released yet. Returns (value, month used)."""
    used = max(m for m in series if m <= month)
    return series[used], used


def quarter_start(month: str) -> str:
    """'2026-08' -> '2026-07', the key FRED uses for that month's quarter."""
    year, mon = int(month[:4]), int(month[5:])
    return f"{year}-{(mon - 1) // 3 * 3 + 1:02d}"


def main() -> None:
    through = date.fromisoformat(json.loads(WAR_ENERGY_JSON.read_text())["through"])
    with open(NYFED_CSV, newline="") as handle:
        nyfed = {row["month"]: row for row in csv.DictReader(handle)}
    durables, nondurables, energy = fetch_fred("PCEDG"), fetch_fred("PCEND"), fetch_fred("DGOERC1Q027SBEA")

    total = measured = 0.0
    by_month: dict[str, float] = {}
    month = FIRST_MONTH
    last_month = f"{through.year}-{through.month:02d}"
    while month <= last_month:
        p = float(nyfed[month]["total_effect_percent"]) / 100
        dur, _ = latest_on_or_before(durables, month)
        non, pce_month = latest_on_or_before(nondurables, month)
        en, _ = latest_on_or_before(energy, quarter_start(month))
        share_of_month = 1.0
        if month == last_month:
            share_of_month = through.day / calendar.monthrange(through.year, through.month)[1]
        dollars = p / (1 + p) * (dur + non - en) / 12 * 1e9 * share_of_month * COVERAGE
        total += dollars
        if nyfed[month]["projected"] == "0":
            measured += dollars
        by_month[month] = round(dollars / HOUSEHOLDS)
        year, mon = int(month[:4]), int(month[5:])
        month = f"{year + mon // 12}-{mon % 12 + 1:02d}"

    output = {
        "method": "NY Fed SR1201 price-level effect p, p/(1+p) x BEA goods ex energy spending, x 20.2/27.1 coverage, / 132.7M households",
        "start_month": FIRST_MONTH,
        "through": through.isoformat(),
        "total_billion": round(total / 1e9, 1),
        "per_household": round(total / HOUSEHOLDS),
        "measured_per_household": round(measured / HOUSEHOLDS),
        "projected_per_household": round((total - measured) / HOUSEHOLDS),
        "latest_pce_month": max(nondurables),
        "latest_price_effect_percent": round(float(nyfed[last_month]["total_effect_percent"]), 2),
        "by_month_per_household": by_month,
        "updated": date.today().isoformat(),
    }
    OUTPUT_JSON.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k != "by_month_per_household"}, indent=2))


if __name__ == "__main__":
    main()
