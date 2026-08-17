from __future__ import annotations

import csv
import json
from pathlib import Path

source_path = Path("/tmp/krx_listing.csv")
output_path = Path("/home/ubuntu/ymym_review/data/kr_stock_index.json")
output_path.parent.mkdir(parents=True, exist_ok=True)

records: dict[str, dict[str, str]] = {}
with source_path.open("r", encoding="utf-8-sig", newline="") as source:
    for row in csv.DictReader(source):
        code = str(row.get("Code") or "").strip().zfill(6)
        name = str(row.get("Name") or "").strip()
        market = str(row.get("Market") or "").strip()
        if code.isdigit() and len(code) == 6 and name:
            records[code] = {"symbol": code, "name": name, "market": market}

payload = {
    "source": "KRX listed-company cache 2026-08-17",
    "count": len(records),
    "items": sorted(records.values(), key=lambda item: (item["name"], item["symbol"])),
}
output_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"wrote {payload['count']} Korean stock search entries to {output_path}")
