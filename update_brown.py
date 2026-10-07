"""Daily update of index.html.

1. Gas and diesel: the Brown site renders its numbers with JavaScript, so we load it in headless
Chrome, read the gasoline and diesel totals it shows, and divide by 131 million
households (the same divisor Brown uses for its "per US household" figure).
2. Mortgage chart: daily Optimal Blue 30-year fixed index from FRED (keyless CSV).
"""

import os
import re
import subprocess
import urllib.request
import sys
from pathlib import Path

BROWN_URL = "https://iranwarcost.watson.brown.edu/"
BROWN_HOUSEHOLDS = 131e6  # Brown: total / 131e6 = "per US household"
REFUND_BOOST = 502        # IRS: ($324.757B - $274.979B) / 99.138M refunds
TARIFFS = 1820            # Tax Foundation: $1,000 (2025) + $820 (2026)

PAGE = Path(__file__).parent / "index.html"
CHROME = os.environ.get("CHROME", "google-chrome")
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=OBMMIC30YF&cosd=2025-01-01"


def read_brown_totals() -> tuple[float, float]:
    """Return (gasoline, diesel) consumer cost since war start, in billions of dollars."""
    dom = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=20000", "--dump-dom", BROWN_URL],
        capture_output=True, text=True, timeout=120,
    ).stdout
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom))
    # e.g. "Gasoline $2.982 → $4.369 ▲+46.5% $70.30B Diesel $3.670 → $6.315 ▲+72.1% $57.49B"
    match = re.search(r"Gasoline \$[\d.]+ → \$[\d.]+ \S+ \$([\d.]+)B Diesel \$[\d.]+ → \$[\d.]+ \S+ \$([\d.]+)B", text)
    if not match:
        sys.exit("Could not find gasoline and diesel totals on the Brown page.")
    return float(match.group(1)), float(match.group(2))


def read_mortgage_daily() -> list[list]:
    """Return [[date, rate], ...] for the Optimal Blue 30-year fixed index since Jan 2025."""
    # FRED stalls on some custom User-Agent strings; the urllib default works.
    lines = urllib.request.urlopen(FRED_CSV, timeout=60).read().decode().splitlines()[1:]
    rows = [line.split(",") for line in lines]
    return [[d, round(float(v), 3)] for d, v in rows if v not in ("", ".")]


def money(x: int) -> str:
    """-2293 -> '&minus;$2,293'."""
    return ("&minus;" if x < 0 else "") + f"${abs(x):,}"


def main() -> None:
    gas_b, diesel_b = read_brown_totals()
    fuel = round((gas_b + diesel_b) * 1e9 / BROWN_HOUSEHOLDS)
    net = REFUND_BOOST - fuel - TARIFFS
    print(f"Brown: gasoline ${gas_b}B, diesel ${diesel_b}B -> ${fuel} per household. Net {net}.")

    html = PAGE.read_text()
    html = re.sub(r'(id="row-gas">)[^<]*(</td>)', rf"\g<1>{money(-fuel)}\g<2>", html)
    html = re.sub(r'(id="row-net">)[^<]*(</td>)', rf"\g<1>{money(net)}\g<2>", html)

    try:
        mortgage = read_mortgage_daily()
        data = ",".join(f'["{d}",{v}]' for d, v in mortgage)
        html = re.sub(r"var MORTGAGE_DAILY = \[.*?\];", lambda m: f"var MORTGAGE_DAILY = [{data}];", html, count=1)
        print(f"Mortgage: {len(mortgage)} days, latest {mortgage[-1]}.")
    except Exception as error:  # keep the Brown update even if FRED is down
        print(f"Mortgage update skipped: {error}")

    PAGE.write_text(html)


if __name__ == "__main__":
    main()
