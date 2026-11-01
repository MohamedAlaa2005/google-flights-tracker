# Google Flights Price Intelligence & Automated Tracker

An automated flight price tracker, scraping engine, and intelligence dashboard powered by **Python**, **Playwright**, and **Supabase**.

## Key Features

- **3 Parallel Playwright Workers:** 30-day scans are divided into three 10-day chunks running concurrently for 3x speedup.
- **Genuine Cheapest Selection:** Automatically switches to Google Flights' "Cheapest" tab and extracts the true lowest verified fare.
- **Google ML Range Evaluation:** Extracts Google's predicted typical price range (e.g. `$115–$205`) and re-evaluates lowest fares against the range (`low`, `typical`, `high`).
- **Cross-Date Range Inheritance:** Caches and backfills typical price ranges across all dates for the same route.
- **Automated 12:00 PM Daily Scheduler:** Automatically scans all 250 top global routes every day at 12:00 PM and synchronizes verified lowest fares to Supabase.
- **Full Web Dashboard:** FastAPI-based UI with interactive search, filters, daily delta indicators, direct Google Flights deep links, and CSV export.
- **Docker-Ready:** Includes production `Dockerfile` and `docker-compose.yml` pre-configured with Google Chrome and Playwright dependencies.

## Architecture

```
├── web_app.py                # FastAPI Dashboard & REST API
├── google_flights_engine.py  # 3-Worker Playwright scraper & Google Flights Price Insights extractor
├── database.py               # Supabase PostgreSQL synchronization & range inheritance
├── scheduler_service.py      # Automated 12:00 PM background schedule runner
├── run_250_routes.py         # Standalone Top 250 routes batch CLI runner
├── routes.py / routes.json   # Top 250 global flight route catalog
├── Dockerfile                # Production container definition (Playwright + Chrome)
└── docker-compose.yml        # Docker compose configuration
```

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your Supabase database credentials:
```bash
cp .env.example .env
```

### 3. Run Web Dashboard
```bash
python web_app.py
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

### 4. Run Top 250 Routes Batch CLI
```bash
# Full 250 routes
python run_250_routes.py

# Test first 5 routes
python run_250_routes.py --limit 5
```

### 5. Run with Docker
```bash
docker compose up -d
```
