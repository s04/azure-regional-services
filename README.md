# Azure Regional Services Archive

A small Azure regional services and retail pricing archive that updates itself with GitHub Actions.

The repo is intentionally shaped like a mini medallion pipeline:

| Layer | Path | Purpose |
| --- | --- | --- |
| Bronze | `data/bronze/` | Source-shaped captures and per-run receipts. |
| Silver | `data/silver/` | Normalized availability and price datasets. |
| Gold | `data/gold/` | Dashboard-ready summaries, daily archives, and CSV time series. |

## What It Tracks

- Azure product availability by region from the public Microsoft product-by-region page.
- A configurable sample of Azure Retail Prices API results across tracked services and regions.
- Daily summary history and per-run receipts so scheduled runs produce a visible audit trail.
- A static GitHub Pages dashboard from `site/`.

## Update Locally

```bash
python scripts/update_archive.py
```

Optional knobs:

```bash
AZURE_PRICE_ITEMS_PER_QUERY=200 \
AZURE_TRACKED_REGIONS=eastus,westeurope,swedencentral \
AZURE_TRACKED_SERVICES="Virtual Machines,Storage,Azure App Service" \
python scripts/update_archive.py
```

## Automation

`.github/workflows/update-archive.yml` runs twice a day and on demand. It updates the medallion data, commits changes with the GitHub Actions bot, and deploys the static dashboard to GitHub Pages.
