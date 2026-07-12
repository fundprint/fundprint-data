"""Build the ZIP-centroid file the map places clinics with.

The bundled centroid file used to hold only the ZIPs of whatever clinics existed
when it was first made (537 of them). As coverage grew, every clinic in a ZIP the
file had never seen fell back to a ZIP3-prefix centroid or, failing that, off the
map entirely: 263 of 1,325 clinics were unplaced, and the ones that did place by
ZIP3 sat at the mean of a three-digit prefix rather than in their own ZIP.

This regenerates the file from the real source it always claimed to use: the
Census ZIP Code Tabulation Area national gazetteer, which covers every ZCTA in
the country. Public domain, no key, one file.

The output is committed so the dashboard build stays reproducible and makes no
network call.

Usage:
    python scripts/build_zcta_centroids.py
"""

from __future__ import annotations

import csv
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2023_Gazetteer/2023_Gaz_zcta_national.zip"
)
OUT = Path(__file__).resolve().parent.parent / "data" / "geo" / "zcta_centroids.json"
UA = "FundprintBot/0.1 (+mailto:atharva.doke737@gmail.com)"


def main() -> int:
    logger.info("fetching %s", GAZETTEER_URL)
    resp = httpx.get(
        GAZETTEER_URL, headers={"User-Agent": UA}, timeout=120.0, follow_redirects=True
    )
    resp.raise_for_status()

    z = zipfile.ZipFile(io.BytesIO(resp.content))
    member = next(n for n in z.namelist() if n.endswith(".txt"))
    centroids: dict[str, list[float]] = {}
    with z.open(member) as f:
        reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"), delimiter="\t")
        # The gazetteer pads its columns with spaces, header included, so the
        # names have to be stripped before they can be looked up.
        header = [c.strip() for c in next(reader)]
        ix = {name: i for i, name in enumerate(header)}
        i_geoid, i_lat, i_lng = ix["GEOID"], ix["INTPTLAT"], ix["INTPTLONG"]
        for row in reader:
            if len(row) <= i_lng:
                continue
            geoid, lat, lng = row[i_geoid].strip(), row[i_lat].strip(), row[i_lng].strip()
            if not (geoid and lat and lng):
                continue
            centroids[geoid.zfill(5)] = [round(float(lat), 5), round(float(lng), 5)]

    if len(centroids) < 30000:
        raise RuntimeError(
            f"only {len(centroids)} ZCTAs parsed; the national file has ~33,000. "
            "Refusing to overwrite a good file with a truncated one."
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(centroids, separators=(",", ":")), encoding="utf-8")
    logger.info("wrote %s with %d ZCTA centroids", OUT, len(centroids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
