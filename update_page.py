"""Daily update of index.html from the computed tallies.

1. War energy and tariffs: read data/war_energy.json (war_energy.py) and data/tariffs.json
   (tariff_cost.py) and fill the headline table, the fuel table and the numbers in the text.
2. Brown comparison: the Brown site renders its numbers with JavaScript, so we load it in
   headless Chrome, read the gasoline and diesel totals it shows, and divide by 131 million
   households (the same divisor Brown uses for its "per US household" figure).
3. Mortgage chart and text: daily Optimal Blue 30-year fixed index from FRED (keyless CSV).

Brown and FRED are optional: if either is down, the rest of the page still updates.
"""

import json
import os
import re
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
PAGE = REPO_DIR / "index.html"
WAR_ENERGY_JSON = REPO_DIR / "data" / "war_energy.json"
TARIFFS_JSON = REPO_DIR / "data" / "tariffs.json"

BROWN_URL = "https://iranwarcost.watson.brown.edu/"
BROWN_HOUSEHOLDS = 131e6  # Brown: total / 131e6 = "per US household"
CHROME = os.environ.get("CHROME", "google-chrome")
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=OBMMIC30YF&cosd=2025-01-01"

# Mortgage example in "What's not included": a $330,000 30-year loan at today's rate vs. February's.
LOAN_AMOUNT = 330_000
PREWAR_MORTGAGE_RATE = 6.05


def read_brown_per_household() -> int:
    """Brown's gasoline + diesel cost since the war began, per household (Brown's own 131M divisor)."""
    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=20000", "--dump-dom", BROWN_URL],
        capture_output=True, text=True, timeout=120,
    ).stdout
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom))
    # e.g. "Gasoline $2.982 → $4.369 ▲+46.5% $70.30B Diesel $3.670 → $6.315 ▲+72.1% $57.49B"
    match = re.search(r"Gasoline \$[\d.]+ → \$[\d.]+ \S+ \$([\d.]+)B Diesel \$[\d.]+ → \$[\d.]+ \S+ \$([\d.]+)B", text)
    if not match:
        raise RuntimeError("Could not find gasoline and diesel totals on the Brown page.")
    return round((float(match.group(1)) + float(match.group(2))) * 1e9 / BROWN_HOUSEHOLDS)


def read_mortgage_daily() -> list[list]:
    """Return [[date, rate], ...] for the Optimal Blue 30-year fixed index since Jan 2025."""
    # FRED stalls on some custom User-Agent strings; the urllib default works.
    lines = urllib.request.urlopen(FRED_CSV, timeout=60).read().decode().splitlines()[1:]
    rows = [line.split(",") for line in lines]
    return [[d, round(float(v), 3)] for d, v in rows if v not in ("", ".")]


def annual_payment(principal: float, annual_rate_percent: float, years: int = 30) -> float:
    """Yearly principal-and-interest payment on a fixed-rate mortgage."""
    r = annual_rate_percent / 100 / 12
    n = years * 12
    return 12 * principal * r / (1 - (1 + r) ** -n)


def money(x: int) -> str:
    """1551 -> '$1,551'."""
    return f"${x:,}"


def long_date(iso: str) -> str:
    """'2026-10-23' -> 'October 23, 2026'."""
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def short_date(iso: str) -> str:
    """'2026-10-23' -> 'Oct 23, 2026'."""
    d = date.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def fill(html: str, element_id: str, value: str) -> str:
    """Replace the text inside the element with this id (the element must contain only text)."""
    new_html, count = re.subn(rf'(id="{element_id}">)[^<]*(</)', lambda m: m.group(1) + value + m.group(2), html)
    if count != 1:
        raise RuntimeError(f'Expected one element with id="{element_id}", found {count}.')
    return new_html


def main() -> None:
    war = json.loads(WAR_ENERGY_JSON.read_text())
    tariffs = json.loads(TARIFFS_JSON.read_text())
    fuels = war["fuels"]
    war_total = sum(f["per_household"] for f in fuels.values())
    total = war_total + tariffs["per_household"]

    html = PAGE.read_text()
    values = {
        "updated": long_date(war["updated"]),
        "title-total": money(total),
        "intro-total": money(total),
        "intro-through": long_date(war["through"]),
        "row-war": "&minus;" + money(war_total),
        "row-tariffs": "&minus;" + money(tariffs["per_household"]),
        "row-total": "&minus;" + money(total),
        "war-through": short_date(war["through"]),
        "tariff-through": short_date(tariffs["through"]),
        "gas-latest": f"${fuels['gasoline']['latest_price']:.2f} on {date.fromisoformat(fuels['gasoline']['latest_price_date']):%B} "
                      f"{date.fromisoformat(fuels['gasoline']['latest_price_date']).day}",
        "fuel-billion": f"${sum(f['total_billion'] for f in fuels.values()):.1f} billion",
        "fuel-hh": money(war_total),
        "tariff-round": money(int(round(tariffs["per_household"], -2))),
        "tariff-split": f"{money(tariffs['measured_per_household'])} measured through February 2026 and "
                        f"{money(tariffs['projected_per_household'])} from the NY Fed&rsquo;s projected path since then",
        "pce-month": f"{date.fromisoformat(tariffs['latest_pce_month'] + '-01'):%B %Y}",
    }
    for fuel in ("gasoline", "diesel", "jet"):
        values[f"{fuel}-billion"] = f"${fuels[fuel]['total_billion']:.1f} billion"
        values[f"{fuel}-hh"] = money(fuels[fuel]["per_household"])
    for element_id, value in values.items():
        html = fill(html, element_id, value)
    print(f"War ${war_total} + tariffs ${tariffs['per_household']} = ${total} through {war['through']}.")

    try:
        brown = read_brown_per_household()
        html = fill(html, "brown", money(brown))
        print(f"Brown: ${brown} per household.")
    except Exception as error:  # keep the rest of the update even if Brown's page changes
        print(f"Brown update skipped: {error}")

    try:
        mortgage = read_mortgage_daily()
        data = ",".join(f'["{d}",{v}]' for d, v in mortgage)
        html = re.sub(r"var MORTGAGE_DAILY = \[.*?\];", lambda m: f"var MORTGAGE_DAILY = [{data}];", html, count=1)
        latest_rate = mortgage[-1][1]
        extra = annual_payment(LOAN_AMOUNT, latest_rate) - annual_payment(LOAN_AMOUNT, PREWAR_MORTGAGE_RATE)
        html = fill(html, "mort-now", f"{latest_rate:.2f}%")
        html = fill(html, "mort-cost", money(int(round(extra, -1))))
        print(f"Mortgage: {len(mortgage)} days, latest {mortgage[-1]}, ${extra:,.0f} a year more on $330K.")
    except Exception as error:  # keep the rest of the update even if FRED is down
        print(f"Mortgage update skipped: {error}")

    PAGE.write_text(html)


if __name__ == "__main__":
    main()
