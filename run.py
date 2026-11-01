import os
import sys
import argparse
from datetime import datetime, timedelta

import database
from hopper_scraper import HopperPlaywrightScraper
from routes import TOP_250_ROUTES

sys.stdout.reconfigure(encoding='utf-8')

def print_banner():
    print("""
========================================================================
    ✈️  HOPPER FLIGHT PRICE SCRAPER (PYTHON + PLAYWRIGHT)
    Direct Live Scraping • Real Hopper Data • Supabase Sync
========================================================================
    """)

def show_db_status():
    stats = database.get_db_stats()
    print("\n--- Supabase Database Status ---")
    for trip_type, info in stats.items():
        status = "Connected ✅" if info.get("connected") else "Error ❌"
        count = info.get("count", 0)
        print(f"  • {trip_type.upper()}: {status} | Stored Flights: {count}")
    print("--------------------------------\n")

def list_recent_flights(trip_type="one-way", limit=10):
    flights = database.get_flights(trip_type=trip_type, limit=limit)
    print(f"\n--- Stored {trip_type.upper()} Flights in Supabase (Top {len(flights)}) ---")
    if not flights:
        print("  (No flights found in database)")
    for f in flights:
        airline = f.get('airline', 'Unknown')
        price = f.get('current_lowest_price')
        cur = f.get('currency', 'USD')
        date = f.get('date')
        orig = f.get('origin_code')
        dest = f.get('destination_code')
        times = f"{f.get('departure_time')} -> {f.get('arrival_time')}"
        stops = f.get('stops', 'Nonstop')
        print(f"  📅 {date} | {orig} → {dest} | ✈️ {airline:<18} | 💰 ${price} {cur:<3} | 🕒 {times} ({stops})")
    print("----------------------------------------------------------------------\n")

def main():
    parser = argparse.ArgumentParser(description="Hopper Flight Price Scraper with Python & Playwright")
    parser.add_argument("--url", "-u", type=str, default=None, help="Direct full Hopper flight search URL")
    parser.add_argument("--origin", "-o", type=str, default=None, help="Origin airport/city code (e.g. ADD, MIL, JFK)")
    parser.add_argument("--dest", "-d", type=str, default=None, help="Destination airport/city code (e.g. DXB, CAI, LHR)")
    parser.add_argument("--date", type=str, default=None, help="Departure date YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1, help="Number of days to scrape (default: 1)")
    parser.add_argument("--trip-type", type=str, default="one-way", choices=["one-way", "round-trip"], help="Trip type")
    parser.add_argument("--adults", type=int, default=2, help="Number of travelers (default: 2)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay / wait time in seconds (default: 1.5)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode (default: visible headed)")
    parser.add_argument("--status", action="store_true", help="Show Supabase database status")
    parser.add_argument("--clear", action="store_true", help="Clear database table")
    parser.add_argument("--login", action="store_true", help="Launch Chrome to sign in to your Google Account")

    args = parser.parse_args()

    print_banner()
    database.init_tables()

    if args.login:
        print("\nOpening Google Chrome so you can sign in to your Google Account...")
        print("Once signed in, your session is saved permanently for all future scrapes.")
        HopperPlaywrightScraper.open_google_login("https://hopper.com")
        return

    if args.status:
        show_db_status()
        list_recent_flights(args.trip_type, limit=20)
        return

    if args.clear:
        confirm = input("Are you sure you want to delete all stored flights in Supabase? (y/N): ")
        if confirm.lower().strip() == 'y':
            database.clear_flights(args.trip_type)
            print("Database table cleared successfully!")
        return

    # Direct URL mode
    if args.url:
        scraper = HopperPlaywrightScraper(headless=args.headless, delay=args.delay)
        try:
            scraper.start(initial_url=args.url)
            scraper.scrape_url(args.url)
            print("\nScrape completed! Here are the updated records from Supabase:")
            list_recent_flights(args.trip_type, limit=15)
        finally:
            scraper.close()
        return

    # If parameters not supplied via CLI flags, enter interactive mode
    origin = args.origin
    dest = args.dest
    date_str = args.date
    days_count = args.days
    trip_type = args.trip_type
    adults = args.adults
    headless = args.headless

    if not origin or not dest:
        print("Select an option:")
        print("  1. Paste Direct Hopper URL (e.g. https://hopper.com/en-us/air/shop?...)")
        print("  2. Scrape ADD -> DXB (Addis Ababa to Dubai)")
        print("  3. Scrape MIL -> CAI (Milan to Cairo)")
        print("  4. Scrape DOH -> DXB (Doha to Dubai)")
        print("  5. Scrape YYZ -> LHR (Toronto to London)")
        print("  6. Custom Route & Date Range")
        print("  7. View Supabase Database Status")
        print("  8. Clear Old Mock Data from Supabase")
        print("  9. 👤 Sign In With Your Google Account")
        
        choice = input("\nEnter choice [1-9] (default: 2): ").strip() or "2"

        if choice == "1":
            pasted_url = input("Paste your Hopper flight search URL: ").strip()
            if pasted_url:
                scraper = HopperPlaywrightScraper(headless=headless, delay=args.delay)
                try:
                    scraper.start(initial_url=pasted_url)
                    scraper.scrape_url(pasted_url)
                    print("\nScrape completed! Here are the updated records from Supabase:")
                    list_recent_flights(trip_type, limit=15)
                finally:
                    scraper.close()
                return
        elif choice == "2":
            origin, dest = "ADD", "DXB"
        elif choice == "3":
            origin, dest = "MIL", "CAI"
        elif choice == "4":
            origin, dest = "DOH", "DXB"
        elif choice == "5":
            origin, dest = "YYZ", "LHR"
        elif choice == "6":
            origin = input("Enter origin code (e.g. ADD, MIL, JFK): ").strip().upper()
            dest = input("Enter destination code (e.g. DXB, CAI, LHR): ").strip().upper()
            date_input = input("Enter start date (YYYY-MM-DD) [default: tomorrow]: ").strip()
            if date_input:
                date_str = date_input
            days_input = input("Enter days count (1 for single date, 30 for month) [default: 1]: ").strip()
            if days_input:
                days_count = int(days_input)
        elif choice == "7":
            show_db_status()
            list_recent_flights(trip_type, limit=20)
            return
        elif choice == "8":
            database.clear_flights(trip_type)
            print("Old data cleared from Supabase!")
            return
        elif choice == "9":
            print("\nOpening Google Chrome so you can sign in to your Google Account...")
            print("Once signed in, your session is saved permanently for all future scrapes.")
            HopperPlaywrightScraper.open_google_login("https://hopper.com")
            return

    if not date_str:
        # Default to tomorrow (since same-day bookings may be closed)
        tomorrow = datetime.now() + timedelta(days=2)
        date_str = tomorrow.strftime("%Y-%m-%d")

    scraper = HopperPlaywrightScraper(headless=headless, delay=args.delay)
    try:
        scraper.start()
        if days_count > 1:
            scraper.scrape_date_range(origin, dest, date_str, days_count=days_count, trip_type=trip_type, adults=adults)
        else:
            scraper.scrape_flight(origin, dest, date_str, trip_type=trip_type, adults=adults)

        print("\nScrape completed! Here are the updated records from Supabase:")
        list_recent_flights(trip_type, limit=15)
    finally:
        scraper.close()

if __name__ == "__main__":
    main()

