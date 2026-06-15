#!/usr/bin/env python3
"""Update Azure regional availability and retail price archive data."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"

AVAILABILITY_URL = (
    "https://azure.microsoft.com/en-us/explore/global-infrastructure/"
    "products-by-region/table"
)
PRICES_URL = "https://prices.azure.com/api/retail/prices"
USER_AGENT = (
    "azure-regional-services-archive/1.0 "
    "(https://github.com/s04/azure-regional-services)"
)

DEFAULT_TRACKED_REGIONS = [
    "eastus",
    "eastus2",
    "westus2",
    "westeurope",
    "northeurope",
    "swedencentral",
    "brazilsouth",
    "japaneast",
    "australiaeast",
    "southeastasia",
]

DEFAULT_TRACKED_SERVICES = [
    "Virtual Machines",
    "Storage",
    "Azure App Service",
    "SQL Database",
    "Azure Kubernetes Service",
    "Azure Cosmos DB",
    "Functions",
    "Load Balancer",
    "Azure Database for PostgreSQL",
    "Application Gateway",
]


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    return [part.strip() for part in raw.split(",") if part.strip()]


def env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise SystemExit(f"{name} must be >= {minimum}, got {value}")
    return value


def fetch(url: str, accept: str, timeout: int = 60) -> tuple[bytes, dict[str, str]]:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    return body, headers


def fetch_json(url: str, timeout: int = 60) -> tuple[dict[str, Any], dict[str, str]]:
    body, headers = fetch(url, "application/json", timeout)
    return json.loads(body.decode("utf-8")), headers


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8")


def parse_availability_html(html: str) -> list[dict[str, Any]]:
    marker = "const data ="
    marker_index = html.find(marker)
    if marker_index == -1:
        raise ValueError("Could not find Azure availability data marker")

    array_start = html.find("[", marker_index + len(marker))
    if array_start == -1:
        raise ValueError("Could not find Azure availability JSON array")

    decoder = json.JSONDecoder()
    rows, _ = decoder.raw_decode(html[array_start:])
    if not isinstance(rows, list):
        raise ValueError("Azure availability payload was not a JSON array")
    return rows


def normalize_region(raw_region: str) -> tuple[str, tuple[str, ...]]:
    region = raw_region.strip()
    flags: list[str] = []
    match = re.search(r"\s+(\*+)$", region)
    if match:
        marker = match.group(1)
        region = region[: match.start()].rstrip()
        if marker == "*":
            flags.append("restricted_access")
        elif marker == "**":
            flags.append("early_access")
        else:
            flags.append(f"{len(marker)}_star_marker")
    return region, tuple(flags)


def normalize_availability_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in raw_rows:
        raw_region = str(row.get("RegionName", "")).strip()
        region, flags = normalize_region(raw_region)
        offering = str(row.get("OfferingName", "")).strip()
        sku = str(row.get("ProductSkuName", "")).strip()
        state = str(row.get("CurrentState", "")).strip()
        geography = str(row.get("GeographyName", "")).strip()
        if not region or not offering:
            continue

        key = (region, offering, sku, state)
        value = {
            "geography": geography,
            "offering": offering,
            "region": region,
            "regionDisplayName": raw_region,
            "sku": sku,
            "state": state,
        }
        if flags:
            value["regionFlags"] = list(flags)
        normalized[key] = value

    return sorted(
        normalized.values(),
        key=lambda item: (
            item["offering"].lower(),
            item["sku"].lower(),
            item["geography"].lower(),
            item["region"].lower(),
            item["state"].lower(),
        ),
    )


def summarize_availability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    state_counts: Counter[str] = Counter()
    geography_counts: Counter[str] = Counter()
    region_data: dict[str, dict[str, Any]] = {}
    offering_data: dict[str, dict[str, Any]] = {}
    unique_skus: set[tuple[str, str]] = set()

    for row in rows:
        region = row["region"]
        offering = row["offering"]
        sku = row["sku"]
        state = row["state"] or "Unknown"
        geography = row["geography"] or "Unknown"
        flags = tuple(row.get("regionFlags", []))

        state_counts[state] += 1
        geography_counts[geography] += 1
        unique_skus.add((offering, sku))

        region_entry = region_data.setdefault(
            region,
            {
                "geography": geography,
                "offerings": set(),
                "region": region,
                "regionFlags": set(),
                "skus": set(),
                "states": Counter(),
            },
        )
        region_entry["offerings"].add(offering)
        region_entry["skus"].add((offering, sku))
        region_entry["states"][state] += 1
        region_entry["regionFlags"].update(flags)

        offering_entry = offering_data.setdefault(
            offering,
            {
                "geographies": set(),
                "offering": offering,
                "regions": set(),
                "skus": set(),
                "states": Counter(),
            },
        )
        offering_entry["geographies"].add(geography)
        offering_entry["regions"].add(region)
        offering_entry["skus"].add(sku)
        offering_entry["states"][state] += 1

    region_summaries = []
    for entry in region_data.values():
        region_summaries.append(
            {
                "geography": entry["geography"],
                "offeringCount": len(entry["offerings"]),
                "region": entry["region"],
                "regionFlags": sorted(entry["regionFlags"]),
                "skuCount": len(entry["skus"]),
                "stateCounts": dict(sorted(entry["states"].items())),
            }
        )

    offering_summaries = []
    for entry in offering_data.values():
        offering_summaries.append(
            {
                "geographyCount": len(entry["geographies"]),
                "offering": entry["offering"],
                "regionCount": len(entry["regions"]),
                "skuCount": len(entry["skus"]),
                "stateCounts": dict(sorted(entry["states"].items())),
            }
        )

    region_summaries.sort(key=lambda item: (-item["offeringCount"], item["region"]))
    offering_summaries.sort(key=lambda item: (-item["regionCount"], item["offering"]))

    preview_rows = [row for row in rows if row["state"].lower() == "preview"]
    retiring_rows = [
        row
        for row in rows
        if row["state"].lower() in {"closing down", "retiring", "retired"}
    ]

    return {
        "geographies": [
            {"geography": geography, "rowCount": count}
            for geography, count in sorted(geography_counts.items())
        ],
        "geographyCount": len(geography_counts),
        "offeringCount": len(offering_data),
        "offerings": offering_summaries,
        "previewSamples": preview_rows[:50],
        "regionCount": len(region_data),
        "regions": region_summaries,
        "retiringSamples": retiring_rows[:50],
        "rowCount": len(rows),
        "skuCount": len(unique_skus),
        "stateCounts": dict(sorted(state_counts.items())),
        "topOfferingsByRegionCount": offering_summaries[:25],
        "topRegionsByOfferingCount": region_summaries[:25],
    }


def availability_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["region"], row["offering"], row["sku"])


def availability_changes(
    old_doc: dict[str, Any] | None, new_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    if not old_doc:
        return {
            "added": len(new_rows),
            "addedSamples": new_rows[:50],
            "removed": 0,
            "removedSamples": [],
            "stateChanged": 0,
            "stateChangedSamples": [],
        }

    old_rows = old_doc.get("rows", [])
    old_map = {availability_key(row): row for row in old_rows}
    new_map = {availability_key(row): row for row in new_rows}

    added = [new_map[key] for key in sorted(new_map.keys() - old_map.keys())]
    removed = [old_map[key] for key in sorted(old_map.keys() - new_map.keys())]
    state_changed = []
    for key in sorted(new_map.keys() & old_map.keys()):
        old_state = old_map[key].get("state")
        new_state = new_map[key].get("state")
        if old_state != new_state:
            state_changed.append(
                {
                    "from": old_state,
                    "offering": key[1],
                    "region": key[0],
                    "sku": key[2],
                    "to": new_state,
                }
            )

    return {
        "added": len(added),
        "addedSamples": added[:50],
        "removed": len(removed),
        "removedSamples": removed[:50],
        "stateChanged": len(state_changed),
        "stateChangedSamples": state_changed[:50],
    }


def price_query_url(service: str, region: str) -> str:
    filter_value = (
        "priceType eq 'Consumption' "
        f"and serviceName eq '{service}' "
        f"and armRegionName eq '{region}'"
    )
    params = {
        "api-version": "2023-01-01-preview",
        "meterRegion": "primary",
        "$filter": filter_value,
    }
    return f"{PRICES_URL}?{urlencode(params)}"


def compact_price_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "armRegionName",
        "armSkuName",
        "currencyCode",
        "effectiveStartDate",
        "location",
        "meterId",
        "meterName",
        "productId",
        "productName",
        "retailPrice",
        "serviceFamily",
        "serviceId",
        "serviceName",
        "skuId",
        "skuName",
        "unitOfMeasure",
        "unitPrice",
    ]
    return {key: item.get(key) for key in keys if key in item}


def summarize_price_items(
    service: str, region: str, items: list[dict[str, Any]], truncated: bool
) -> dict[str, Any]:
    prices = [
        float(item["retailPrice"])
        for item in items
        if isinstance(item.get("retailPrice"), (int, float))
    ]
    products = {item.get("productName") for item in items if item.get("productName")}
    skus = {item.get("skuName") for item in items if item.get("skuName")}
    units = {item.get("unitOfMeasure") for item in items if item.get("unitOfMeasure")}
    cheapest = sorted(
        items,
        key=lambda item: (
            float(item.get("retailPrice", sys.maxsize)),
            str(item.get("productName", "")),
            str(item.get("skuName", "")),
        ),
    )[:5]

    if prices:
        min_price = min(prices)
        max_price = max(prices)
        avg_price = round(sum(prices) / len(prices), 8)
    else:
        min_price = None
        max_price = None
        avg_price = None

    return {
        "averageRetailPrice": avg_price,
        "cheapestSamples": cheapest,
        "currency": items[0].get("currencyCode") if items else "USD",
        "itemCount": len(items),
        "maxRetailPrice": max_price,
        "minRetailPrice": min_price,
        "productCount": len(products),
        "region": region,
        "service": service,
        "skuCount": len(skus),
        "truncated": truncated,
        "unitCount": len(units),
    }


def fetch_price_snapshot(
    services: list[str], regions: list[str], max_items_per_query: int, timeout: int
) -> dict[str, Any]:
    all_items: list[dict[str, Any]] = []
    query_summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for service in services:
        for region in regions:
            url = price_query_url(service, region)
            query_items: list[dict[str, Any]] = []
            truncated = False

            while url and len(query_items) < max_items_per_query:
                try:
                    payload, _headers = fetch_json(url, timeout)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        {
                            "error": str(exc),
                            "region": region,
                            "service": service,
                        }
                    )
                    break

                page_items = payload.get("Items", [])
                remaining = max_items_per_query - len(query_items)
                query_items.extend(compact_price_item(item) for item in page_items[:remaining])

                next_page = payload.get("NextPageLink")
                if next_page and len(page_items) > remaining:
                    truncated = True
                    break
                if next_page and len(query_items) >= max_items_per_query:
                    truncated = True
                    break
                url = next_page

                time.sleep(0.05)

            query_items.sort(
                key=lambda item: (
                    str(item.get("serviceName", "")),
                    str(item.get("armRegionName", "")),
                    str(item.get("productName", "")),
                    str(item.get("skuName", "")),
                    str(item.get("meterName", "")),
                    str(item.get("meterId", "")),
                )
            )
            all_items.extend(query_items)
            query_summaries.append(
                summarize_price_items(service, region, query_items, truncated)
            )

    all_items.sort(
        key=lambda item: (
            str(item.get("serviceName", "")),
            str(item.get("armRegionName", "")),
            str(item.get("productName", "")),
            str(item.get("skuName", "")),
            str(item.get("meterName", "")),
            str(item.get("meterId", "")),
        )
    )
    query_summaries.sort(key=lambda item: (item["service"], item["region"]))

    return {
        "failures": failures,
        "items": all_items,
        "querySummaries": query_summaries,
        "trackedRegions": regions,
        "trackedServices": services,
    }


def price_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("armRegionName", "")),
        str(item.get("serviceName", "")),
        str(item.get("skuId", "")),
        str(item.get("meterId", "")),
    )


def price_changes(
    old_doc: dict[str, Any] | None, new_items: list[dict[str, Any]]
) -> dict[str, Any]:
    if not old_doc:
        return {
            "added": len(new_items),
            "addedSamples": new_items[:50],
            "priceChanged": 0,
            "priceChangedSamples": [],
            "removed": 0,
            "removedSamples": [],
        }

    old_items = old_doc.get("items", [])
    old_map = {price_key(item): item for item in old_items}
    new_map = {price_key(item): item for item in new_items}

    added = [new_map[key] for key in sorted(new_map.keys() - old_map.keys())]
    removed = [old_map[key] for key in sorted(old_map.keys() - new_map.keys())]
    changed = []
    for key in sorted(new_map.keys() & old_map.keys()):
        old_price = old_map[key].get("retailPrice")
        new_price = new_map[key].get("retailPrice")
        if old_price != new_price:
            changed.append(
                {
                    "from": old_price,
                    "meterName": new_map[key].get("meterName"),
                    "productName": new_map[key].get("productName"),
                    "region": key[0],
                    "service": key[1],
                    "skuName": new_map[key].get("skuName"),
                    "to": new_price,
                    "unitOfMeasure": new_map[key].get("unitOfMeasure"),
                }
            )

    return {
        "added": len(added),
        "addedSamples": added[:50],
        "priceChanged": len(changed),
        "priceChangedSamples": changed[:50],
        "removed": len(removed),
        "removedSamples": removed[:50],
    }


def update_csv(path: Path, key_fields: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    fieldnames = list(row.keys())

    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            if reader.fieldnames:
                fieldnames = list(dict.fromkeys([*reader.fieldnames, *fieldnames]))

    new_key = tuple(str(row[field]) for field in key_fields)
    filtered = [
        existing
        for existing in rows
        if tuple(str(existing.get(field, "")) for field in key_fields) != new_key
    ]
    filtered.append({key: "" if value is None else value for key, value in row.items()})
    filtered.sort(key=lambda item: tuple(str(item.get(field, "")) for field in key_fields))

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for existing in filtered:
            writer.writerow({field: existing.get(field, "") for field in fieldnames})


def update_price_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "date",
        "runAt",
        "service",
        "region",
        "itemCount",
        "productCount",
        "skuCount",
        "minRetailPrice",
        "maxRetailPrice",
        "averageRetailPrice",
        "currency",
        "truncated",
    ]
    existing_rows: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_rows = list(reader)

    replacement_keys = {
        (str(row["date"]), str(row["service"]), str(row["region"])) for row in rows
    }
    kept = [
        row
        for row in existing_rows
        if (row.get("date", ""), row.get("service", ""), row.get("region", ""))
        not in replacement_keys
    ]
    kept.extend(rows)
    kept.sort(key=lambda item: (item["date"], item["service"], item["region"]))

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in kept:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def compact_availability_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in summary.items()
        if key
        not in {
            "offerings",
            "regions",
        }
    }


def build_medallion_documents(
    *,
    availability_body: bytes,
    availability_delta: dict[str, Any],
    availability_hash: str,
    availability_headers: dict[str, str],
    availability_rows: list[dict[str, Any]],
    availability_summary: dict[str, Any],
    max_items_per_query: int,
    price_delta: dict[str, Any],
    price_hash: str,
    price_items: list[dict[str, Any]],
    price_snapshot: dict[str, Any],
    raw_availability_rows: list[dict[str, Any]],
    run_at: str,
    run_date: str,
    tracked_regions: list[str],
    tracked_services: list[str],
) -> dict[str, Any]:
    sources = {
        "availability": {
            "contentLength": len(availability_body),
            "etag": availability_headers.get("etag"),
            "lastModified": availability_headers.get("last-modified"),
            "url": AVAILABILITY_URL,
        },
        "prices": {
            "apiVersion": "2023-01-01-preview",
            "url": PRICES_URL,
        },
    }

    bronze_availability = {
        "capturedAt": run_at,
        "rowCount": len(raw_availability_rows),
        "rows": raw_availability_rows,
        "schemaVersion": 1,
        "source": sources["availability"],
    }
    bronze_prices = {
        "capturedAt": run_at,
        "failures": price_snapshot["failures"],
        "items": price_items,
        "maxItemsPerQuery": max_items_per_query,
        "querySummaries": price_snapshot["querySummaries"],
        "schemaVersion": 1,
        "source": sources["prices"],
        "trackedRegions": tracked_regions,
        "trackedServices": tracked_services,
    }
    silver_availability = {
        "dataHash": availability_hash,
        "rows": availability_rows,
        "schemaVersion": 1,
        "source": {
            "name": "Azure product availability by region",
            "url": AVAILABILITY_URL,
        },
    }
    silver_prices = {
        "dataHash": price_hash,
        "items": price_items,
        "querySummaries": price_snapshot["querySummaries"],
        "schemaVersion": 1,
        "source": {
            "name": "Azure Retail Prices API",
            "url": PRICES_URL,
        },
        "trackedRegions": tracked_regions,
        "trackedServices": tracked_services,
    }
    gold_summary = {
        "availability": {
            "changes": availability_delta,
            "dataHash": availability_hash,
            "summary": availability_summary,
        },
        "generatedAt": run_at,
        "layers": {
            "bronze": "data/bronze/latest",
            "silver": "data/silver/latest",
            "gold": "data/gold/latest",
        },
        "price": {
            "changes": price_delta,
            "dataHash": price_hash,
            "failures": price_snapshot["failures"],
            "itemCount": len(price_items),
            "maxItemsPerQuery": max_items_per_query,
            "queryCount": len(price_snapshot["querySummaries"]),
            "querySummaries": price_snapshot["querySummaries"],
            "trackedRegions": tracked_regions,
            "trackedServices": tracked_services,
        },
        "schemaVersion": 1,
        "sources": sources,
    }
    daily_summary = {
        "availability": {
            "changes": availability_delta,
            "dataHash": availability_hash,
            "summary": compact_availability_summary(availability_summary),
        },
        "date": run_date,
        "generatedAt": run_at,
        "price": {
            "changes": price_delta,
            "dataHash": price_hash,
            "failures": price_snapshot["failures"],
            "itemCount": len(price_items),
            "maxItemsPerQuery": max_items_per_query,
            "queryCount": len(price_snapshot["querySummaries"]),
            "querySummaries": price_snapshot["querySummaries"],
        },
        "schemaVersion": 1,
    }
    run_receipt = {
        "availabilityHash": availability_hash,
        "availabilityRows": len(availability_rows),
        "date": run_date,
        "generatedAt": run_at,
        "medallion": {
            "bronze": "source-shaped captures",
            "silver": "normalized service and price datasets",
            "gold": "dashboard-ready summaries and time series",
        },
        "priceFailures": price_snapshot["failures"],
        "priceHash": price_hash,
        "priceItems": len(price_items),
        "priceQueries": len(price_snapshot["querySummaries"]),
        "sources": sources,
        "trackedRegions": tracked_regions,
        "trackedServices": tracked_services,
    }

    return {
        "bronze_availability": bronze_availability,
        "bronze_prices": bronze_prices,
        "daily_summary": daily_summary,
        "gold_summary": gold_summary,
        "run_receipt": run_receipt,
        "silver_availability": silver_availability,
        "silver_prices": silver_prices,
    }


def write_medallion_documents(
    started_at: datetime, run_stamp: str, docs: dict[str, Any]
) -> None:
    date_path = started_at.strftime("%Y/%m/%d")
    write_json(BRONZE_DIR / "latest" / "availability-source.json", docs["bronze_availability"])
    write_json(BRONZE_DIR / "latest" / "prices-source.json", docs["bronze_prices"])
    write_json(BRONZE_DIR / "runs" / date_path / f"{run_stamp}.json", docs["run_receipt"])
    write_json(SILVER_DIR / "latest" / "availability.json", docs["silver_availability"])
    write_json(SILVER_DIR / "latest" / "prices.json", docs["silver_prices"])
    write_json(GOLD_DIR / "latest" / "summary.json", docs["gold_summary"])
    write_json(GOLD_DIR / "archive" / date_path / "summary.json", docs["daily_summary"])


def update_gold_timeseries(
    *,
    availability_delta: dict[str, Any],
    availability_hash: str,
    availability_summary: dict[str, Any],
    price_delta: dict[str, Any],
    price_hash: str,
    price_snapshot: dict[str, Any],
    run_at: str,
    run_date: str,
) -> None:
    update_csv(
        GOLD_DIR / "timeseries" / "daily-summary.csv",
        ["date"],
        {
            "date": run_date,
            "runAt": run_at,
            "availabilityHash": availability_hash,
            "priceHash": price_hash,
            "availabilityRows": availability_summary["rowCount"],
            "regions": availability_summary["regionCount"],
            "geographies": availability_summary["geographyCount"],
            "offerings": availability_summary["offeringCount"],
            "skus": availability_summary["skuCount"],
            "priceItems": len(price_snapshot["items"]),
            "priceQueries": len(price_snapshot["querySummaries"]),
            "availabilityAdded": availability_delta["added"],
            "availabilityRemoved": availability_delta["removed"],
            "availabilityStateChanged": availability_delta["stateChanged"],
            "priceAdded": price_delta["added"],
            "priceRemoved": price_delta["removed"],
            "priceChanged": price_delta["priceChanged"],
            "priceFailures": len(price_snapshot["failures"]),
        },
    )

    price_csv_rows = []
    for query in price_snapshot["querySummaries"]:
        price_csv_rows.append(
            {
                "averageRetailPrice": query["averageRetailPrice"],
                "currency": query["currency"],
                "date": run_date,
                "itemCount": query["itemCount"],
                "maxRetailPrice": query["maxRetailPrice"],
                "minRetailPrice": query["minRetailPrice"],
                "productCount": query["productCount"],
                "region": query["region"],
                "runAt": run_at,
                "service": query["service"],
                "skuCount": query["skuCount"],
                "truncated": query["truncated"],
            }
        )
    update_price_csv(GOLD_DIR / "timeseries" / "price-summary.csv", price_csv_rows)


def main() -> int:
    started_at = utc_now()
    run_at = iso_z(started_at)
    run_date = started_at.date().isoformat()
    run_stamp = started_at.strftime("%H%M%SZ")

    timeout = env_int("AZURE_ARCHIVE_HTTP_TIMEOUT", 60)
    max_items_per_query = env_int("AZURE_PRICE_ITEMS_PER_QUERY", 200)
    tracked_regions = env_list("AZURE_TRACKED_REGIONS", DEFAULT_TRACKED_REGIONS)
    tracked_services = env_list("AZURE_TRACKED_SERVICES", DEFAULT_TRACKED_SERVICES)

    print(f"Fetching Azure product availability from {AVAILABILITY_URL}")
    availability_body, availability_headers = fetch(
        AVAILABILITY_URL, "text/html,application/xhtml+xml", timeout
    )
    raw_rows = parse_availability_html(availability_body.decode("utf-8"))
    availability_rows = normalize_availability_rows(raw_rows)
    availability_summary = summarize_availability(availability_rows)
    availability_hash = stable_hash(availability_rows)

    print(
        "Fetching Azure retail prices for "
        f"{len(tracked_services)} services across {len(tracked_regions)} regions"
    )
    price_snapshot = fetch_price_snapshot(
        tracked_services, tracked_regions, max_items_per_query, timeout
    )
    price_items = price_snapshot["items"]
    price_hash = stable_hash(
        {
            "items": price_items,
            "trackedRegions": tracked_regions,
            "trackedServices": tracked_services,
        }
    )

    silver_latest_dir = SILVER_DIR / "latest"
    old_availability_doc = read_json(silver_latest_dir / "availability.json")
    old_price_doc = read_json(silver_latest_dir / "prices.json")
    availability_delta = availability_changes(old_availability_doc, availability_rows)
    price_delta = price_changes(old_price_doc, price_items)

    docs = build_medallion_documents(
        availability_body=availability_body,
        availability_delta=availability_delta,
        availability_hash=availability_hash,
        availability_headers=availability_headers,
        availability_rows=availability_rows,
        availability_summary=availability_summary,
        max_items_per_query=max_items_per_query,
        price_delta=price_delta,
        price_hash=price_hash,
        price_items=price_items,
        price_snapshot=price_snapshot,
        raw_availability_rows=raw_rows,
        run_at=run_at,
        run_date=run_date,
        tracked_regions=tracked_regions,
        tracked_services=tracked_services,
    )
    write_medallion_documents(started_at, run_stamp, docs)
    update_gold_timeseries(
        availability_delta=availability_delta,
        availability_hash=availability_hash,
        availability_summary=availability_summary,
        price_delta=price_delta,
        price_hash=price_hash,
        price_snapshot=price_snapshot,
        run_at=run_at,
        run_date=run_date,
    )

    print(
        "Archive updated: "
        f"{availability_summary['regionCount']} regions, "
        f"{availability_summary['offeringCount']} offerings, "
        f"{len(price_items)} tracked price items"
    )
    if price_snapshot["failures"]:
        print(f"Price query failures: {len(price_snapshot['failures'])}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
