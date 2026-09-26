"""Regenerate src/cheap_flights/data/airports.csv from OurAirports (public domain data).

Keeps large and medium airports with scheduled service and an IATA code, plus each airport's
longest runway (feet), used to rank a country's airports by size.
Run: ./scripts/dev.sh uv run python scripts/build_airports.py
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
OUT = Path(__file__).resolve().parent.parent / "src" / "cheap_flights" / "data" / "airports.csv"
SIZES = {"large_airport": "large", "medium_airport": "medium"}


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read().decode("utf-8")


def longest_runways() -> dict[str, int]:
    longest: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(fetch(RUNWAYS_URL))):
        try:
            length = int(row["length_ft"])
        except ValueError:
            continue
        ident = row["airport_ident"]
        longest[ident] = max(length, longest.get(ident, 0))
    return longest


def main() -> None:
    text = fetch(URL)
    runways = longest_runways()
    rows: dict[str, tuple[str, str, str, str, str, int]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        iata = (row["iata_code"] or "").strip().upper()
        size = SIZES.get(row["type"])
        if len(iata) != 3 or not iata.isalpha() or size is None:
            continue
        if row["scheduled_service"] != "yes":
            continue
        runway = runways.get(row["ident"], 0)
        candidate = (iata, row["name"], row["municipality"], row["iso_country"], size, runway)
        if iata not in rows or (size == "large" and rows[iata][4] != "large"):
            rows[iata] = candidate
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["iata", "name", "city", "country", "size", "runway_ft"])
        writer.writerows(sorted(rows.values()))
    print(f"wrote {len(rows)} airports to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
