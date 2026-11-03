#!/usr/bin/env python3
"""
Stand-Alone Top 250 Routes Google Flights Round-Trip Scraper & Supabase Synchronizer
Runs 30-day scans using 3 parallel Playwright workers per route.
For each departure day i, searches return dates in window [i+1 .. i+13] (e.g. 1:[2..14], 2:[3..15])
and extracts the cheapest round-trip flight combination directly into Supabase (round_trip_flights).
"""
import sys
import argparse
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 encoding on standard output for console stability
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import database
from routes import TOP_250_ROUTES
import google_flights_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("Run250RoutesRoundTrip")


def main():
    parser = argparse.ArgumentParser(description="Scrape Top 250 Routes Round-Trip on Google Flights and sync to Supabase.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of routes to process (e.g. 5 for testing).")
    parser.add_argument("--days", type=int, default=30, help="Departure days to look ahead (default: 30).")
    parser.add_argument("--return-window", type=int, default=14, help="Max return days offset (default: 14, searching [1..14] days stay).")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between page requests (default: 0.2s).")
    parser.add_argument("--dry-run", action="store_true", help="Do not persist to Supabase.")
    args = parser.parse_args()

    # Ensure Supabase schema & columns exist
    logger.info("Initializing Supabase tables (round_trip_flights) and ensuring columns exist...")
    database.init_tables()

    routes_to_run = TOP_250_ROUTES[:args.limit] if args.limit else TOP_250_ROUTES
    total_routes = len(routes_to_run)
    save_db = not args.dry_run

    logger.info(f"==> Launching ROUND-TRIP batch scan across {total_routes} routes ({args.days} dep days, 1-{args.return_window}d return window) <==")
    start_time = datetime.now()

    def on_progress(r_idx, tot_routes, route_key, data):
        day = data.get("day")
        total = data.get("total_days")
        flight = data.get("flight") or {}
        price = flight.get("current_lowest_price", "--")
        airline = flight.get("airline", "Unknown")
        rating = flight.get("price_rating", "unknown").upper()
        ret_date = flight.get("return_date", "--")
        dep_date = flight.get("departure_date", "--")
        self_transfer = "Yes" if flight.get("is_self_transfer") else "No"
        logger.info(f"[{r_idx}/{tot_routes}] {route_key} | Dep {dep_date} -> Ret {ret_date} (Day {day}/{total}): ${price} ({airline}) | SelfTransfer: {self_transfer} | {rating}")

    stats = google_flights_engine.scrape_all_250_routes_round_trip(
        save_to_supabase=save_db,
        routes_subset=routes_to_run,
        on_route_progress=on_progress
    )

    elapsed = datetime.now() - start_time
    logger.info("=" * 60)
    logger.info(f"ROUND-TRIP BATCH FINISHED in {elapsed}")
    logger.info(f"Routes processed: {stats['total_routes_processed']}/{total_routes}")
    logger.info(f"Cheapest round-trip flights saved to Supabase (round_trip_flights): {stats['total_saved_flights']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

