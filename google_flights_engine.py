import os
import sys
import re
import time
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Callable

from playwright.sync_api import sync_playwright
from routes import get_route, TOP_250_ROUTES
import database

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GoogleFlightsEngine")

PORT = int(os.getenv("CHROME_DEBUG_PORT", "9222"))
PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile"))
HEADLESS = os.getenv("HEADLESS", "true").lower() in ("true", "1", "yes")

CHROME_CANDIDATES = [
    os.getenv("CHROME_BIN"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]
CHROME_CANDIDATES = [c for c in CHROME_CANDIDATES if c]

TIME_RE = re.compile(r"^(\d{1,2}:\d{2}\s?[AP]M)\s?(\+\d)?$")
DUR_RE = re.compile(r"^(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?$")
STOPS_RE = re.compile(r"^(Nonstop|\d+\s+stops?)$", re.I)
CO2_RE = re.compile(r"([\d,]+)\s*kg CO2e")
EMIS_RE = re.compile(r"([+-]?\d+)%\s*emissions")
PRICE_RE = re.compile(r"^\$(\d[\d,]*)$")

# Thread-safe in-memory cache for route typical price ranges
ROUTE_RANGE_CACHE: Dict = {}
CACHE_LOCK = threading.Lock()


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def chrome_ready():
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/json/version", timeout=1.0)
        return True
    except Exception:
        return False


def start_chrome():
    """Starts Chrome with optimized memory settings and CDP enabled."""
    if chrome_ready():
        return None

    chrome_path = find_chrome()
    if not chrome_path:
        raise FileNotFoundError("Could not find chrome.exe.")

    args = [
        chrome_path,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1600,1000",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--mute-audio",
        "--disable-blink-features=AutomationControlled",
        "--js-flags=--max-old-space-size=512",
    ]
    if HEADLESS:
        args += [
            "--headless=new",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]
    else:
        args += ["--start-minimized"]

    proc = subprocess.Popen(args)

    for _ in range(30):
        if chrome_ready():
            return proc
        time.sleep(0.4)

    raise TimeoutError("Chrome launched but port 9222 not ready.")


def click_if_exists(locator, timeout=1500):
    try:
        locator.first.click(timeout=timeout)
        return True
    except Exception:
        return False


def to_24h(t):
    return datetime.strptime(t.replace(" ", "").upper(), "%I:%M%p").strftime("%H:%M")


def parse_flight_text(text: str, flight_date_str: str) -> Optional[Dict]:
    """Parses Google Flights flight card text using exact regex patterns from user's dwsd.py."""
    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() not in ("–", "-")]

    times = []
    last_idx = -1
    for i, l in enumerate(lines):
        m = TIME_RE.match(l)
        if m:
            times.append((m.group(1), m.group(2)))
            last_idx = i
            if len(times) == 2:
                break
    if len(times) < 2:
        return None

    (dep, _), (arr, plus) = times
    arr_offset = int(plus[1:]) if plus else 0

    airline = lines[last_idx + 1] if last_idx + 1 < len(lines) else ""
    rest = lines[last_idx + 2:]

    duration = stops = layover = None
    co2 = emis = price = None

    for j, l in enumerate(rest):
        if duration is None and l and DUR_RE.match(l) and ("hr" in l or "min" in l):
            m = DUR_RE.match(l)
            duration = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
            continue
        if stops is None and STOPS_RE.match(l):
            stops = 0 if l.lower() == "nonstop" else int(l.split()[0])
            if stops > 0 and j + 1 < len(rest) and not CO2_RE.search(rest[j + 1]):
                layover = rest[j + 1]
            continue
        if co2 is None:
            m = CO2_RE.search(l)
            if m:
                co2 = int(m.group(1).replace(",", ""))
                continue
        if emis is None:
            m = EMIS_RE.search(l)
            if m:
                emis = int(m.group(1))
                continue
            if "avg emissions" in l.lower():
                emis = 0
                continue
        m = PRICE_RE.match(l)
        if m:
            price = int(m.group(1).replace(",", ""))

    if price is None or duration is None or stops is None:
        return None

    try:
        d0 = date.fromisoformat(flight_date_str)
        dep24, arr24 = to_24h(dep), to_24h(arr)
        arr_date = d0 + timedelta(days=arr_offset)
    except Exception:
        dep24, arr24 = dep, arr
        arr_date = flight_date_str

    return {
        "dep_time": dep24,
        "arr_time": arr24,
        "arr_day_offset": arr_offset,
        "dep_datetime": f"{flight_date_str} {dep24}",
        "arr_datetime": f"{arr_date} {arr24}",
        "airline": airline,
        "duration_min": duration,
        "stops": stops,
        "layover": layover or "",
        "co2_kg": co2 if co2 is not None else "",
        "emissions_pct": emis if emis is not None else "",
        "price_usd": price,
    }


def extract_price_insights(page) -> Dict[str, str]:
    """
    Extracts official Google Flights Price Insights & ML Prediction Advice directly from the Price Insights card:
    - price_labels: Google's exact heading (e.g. 'Prices are currently typical for your search', 'Prices are currently low for your search')
    - price_rating: Google's rating ('low', 'typical', 'high')
    - prediction_advice: Google's typical price range & status (e.g. 'The least expensive flights for similar trips usually cost between $130–190. ($149 is typical)')
    """
    try:
        target = page.locator('text=Prices are currently').first
        if target.count() > 0:
            # Click to expand card if collapsed
            try:
                chevron_or_parent = target.locator('xpath=ancestor::div[contains(@class, "IhZhl") or @role="heading"]/..')
                chevron_or_parent.first.click(timeout=1000)
                page.wait_for_timeout(600)
            except Exception:
                pass

            card = target.locator('xpath=ancestor::div[contains(@class, "L9Lk9b") or contains(@class, "IhZhl")]/../../..')
            text = card.inner_text() if card.count() > 0 else page.inner_text('body')

            # 1. Headline & Rating
            headline_m = re.search(r'Prices are currently\s+(low|typical|high)[^\n]*', text, re.I)
            if headline_m:
                headline = headline_m.group(0).strip()
                rating = headline_m.group(1).lower()
            else:
                headline = target.inner_text().strip()
                rating = 'low' if 'low' in headline.lower() else ('high' if 'high' in headline.lower() else 'typical')

            # 2. Similar trips range advice e.g. "The least expensive flights for similar trips to Rome usually cost between $130–190."
            range_m = re.search(r'usually cost between\s+\$?([\d,]+)(?:\s*(?:[-–—to]|and)\s*|\s+)\$?([\d,]+)', text, re.I)
            if not range_m:
                range_m = re.search(r'between\s+\$?([\d,]+)(?:\s*(?:[-–—to]|and)\s*|\s+)\$?([\d,]+)', text, re.I)

            indicator_m = re.search(r'\$?[\d,]+\s+is\s+(low|typical|high)', text, re.I)

            t_min = None
            t_max = None
            range_text = None
            if range_m:
                try:
                    t_min = float(range_m.group(1).replace(',', ''))
                    t_max = float(range_m.group(2).replace(',', ''))
                    range_text = f"The least expensive flights usually cost between ${t_min:.0f}–${t_max:.0f}."
                except Exception:
                    pass

            advice = ""
            if range_m and indicator_m:
                advice = f"{range_text} ({indicator_m.group(0).strip()})"
            elif range_m:
                advice = range_text
            elif indicator_m:
                advice = f"{indicator_m.group(0).strip()} for this route"
            else:
                # Look for booking timing advice
                timing_m = re.search(r'The cheapest time to book[^\n]*', text, re.I)
                unlikely_m = re.search(r'Prices are unlikely to drop[^\n]*', text, re.I)
                if timing_m:
                    advice = timing_m.group(0).strip()
                elif unlikely_m:
                    advice = unlikely_m.group(0).strip()
                else:
                    advice = headline

            return {
                "price_rating": rating,
                "price_labels": headline,
                "prediction_advice": advice,
                "typical_min": t_min,
                "typical_max": t_max,
                "range_text": range_text
            }
    except Exception as e:
        logger.warning(f"Failed extracting price insights: {e}")

    return {
        "price_rating": "typical",
        "price_labels": "Prices are currently typical for your search",
        "prediction_advice": "Prices are currently typical for this route",
        "typical_min": None,
        "typical_max": None,
        "range_text": None
    }


def evaluate_price_against_range(best_price: float, insights: Dict) -> Dict:
    """
    Compares the TRUE verified cheapest flight price against Google's authoritative typical range.
    Google's card initially evaluates the first/top flight (which is often expensive, e.g. $236 is high),
    so we re-evaluate using the true lowest price ($197) against Google's predicted bounds ($115 - $205):
    - best_price < typical_min: 'low'
    - typical_min <= best_price <= typical_max: 'typical'
    - best_price > typical_max: 'high'
    """
    t_min = insights.get("typical_min")
    t_max = insights.get("typical_max")
    range_text = insights.get("range_text")
    t_range_str = f"${t_min:.0f}–${t_max:.0f}" if (t_min is not None and t_max is not None) else ""

    if t_min is not None and t_max is not None:
        if best_price < t_min:
            rating = "low"
            label = "Prices are currently low for your search"
            status = f"${best_price:.0f} is low"
        elif best_price > t_max:
            rating = "high"
            label = "Prices are currently high for your search"
            status = f"${best_price:.0f} is high"
        else:
            rating = "typical"
            label = "Prices are currently typical for your search"
            status = f"${best_price:.0f} is typical"

        prefix = range_text or f"The least expensive flights usually cost between {t_range_str}."
        advice = f"{prefix} ({status})"
        return {
            "price_rating": rating,
            "price_labels": label,
            "prediction_advice": advice,
            "typical_min": t_min,
            "typical_max": t_max,
            "typical_range": t_range_str
        }

    return {
        "price_rating": insights.get("price_rating", "typical"),
        "price_labels": insights.get("price_labels", "Prices are currently typical for your search"),
        "prediction_advice": insights.get("prediction_advice", "Prices are currently typical for this route"),
        "typical_min": t_min,
        "typical_max": t_max,
        "typical_range": t_range_str
    }


def read_cheapest_flight(page, flight_date_str: str) -> Optional[Dict]:
    """
    Parses top flight candidates on page and strictly selects the TRUE MINIMUM price.
    Prevents picking high-priced sponsored / unsorted first items.
    """
    rows = page.locator("li").filter(has_text=re.compile(r"\$\d"))
    count = min(rows.count(), 10)
    candidates = []
    for i in range(count):
        try:
            text = rows.nth(i).inner_text(timeout=800)
        except Exception:
            continue
        if "AM" not in text and "PM" not in text:
            continue
        fl = parse_flight_text(text, flight_date_str)
        if fl:
            candidates.append(fl)
    
    if candidates:
        # Strictly return the absolute cheapest flight found among candidates
        return min(candidates, key=lambda f: f["price_usd"])
    return None


def scrape_cheapest_flight(
    origin: str,
    destination: str,
    flight_date: str,
    adults: int = 1,
    currency: str = "USD"
) -> Optional[Dict]:
    """Scrapes the TRUE cheapest flight for a single day."""
    res = scrape_30_days_range(
        origin=origin,
        destination=destination,
        start_date=flight_date,
        days_count=1,
        currency=currency,
        save_to_supabase=False
    )
    return res[0] if res else None


def _scrape_worker_chunk(
    worker_id: int,
    origin: str,
    destination: str,
    route_info: Dict,
    current_dt: date,
    day_indices: List[int],
    total_days: int,
    currency: str,
    save_to_supabase: bool,
    delay_sec: float,
    prog_lock: threading.Lock,
    prog_counter: List[int],
    on_progress: Optional[Callable[[int, int, dict], None]]
) -> List[Dict]:
    worker_results = []
    chunk_desc = f"{day_indices[0]} to {day_indices[-1]}" if day_indices else "none"
    logger.info(f"[Worker {worker_id}] Starting chunk: {len(day_indices)} days (Offset {chunk_desc})...")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{PORT}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        for day_idx in day_indices:
            target_date_str = (current_dt + timedelta(days=day_idx)).strftime("%Y-%m-%d")
            url = (
                "https://www.google.com/travel/flights"
                f"?q=Flights%20to%20{destination}%20from%20{origin}%20on%20{target_date_str}%20oneway"
                f"&curr={currency}&hl=en&gl=eg"
            )

            # Fresh page per date to keep memory clean
            page = context.new_page()
            page.set_viewport_size({"width": 1400, "height": 900})

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)

                for label in ("Accept all", "Reject all"):
                    if click_if_exists(page.get_by_role("button", name=label), timeout=1000):
                        break

                page.wait_for_timeout(1000)

                # 1. Extract official Google Flights Price Insights FIRST (while Price Insights card is live on initial page)
                insights = extract_price_insights(page)

                # Inherit typical range across dates on the same route if missing on this specific date
                route_key = (origin, destination)
                with CACHE_LOCK:
                    if insights.get("typical_min") is not None:
                        ROUTE_RANGE_CACHE[route_key] = insights
                    elif route_key in ROUTE_RANGE_CACHE:
                        cached = ROUTE_RANGE_CACHE[route_key]
                        insights["typical_min"] = cached.get("typical_min")
                        insights["typical_max"] = cached.get("typical_max")
                        insights["range_text"] = cached.get("range_text")

                # 2. Click "Cheapest" tab and wait for Google Flights to re-sort
                try:
                    cheapest_tab = page.get_by_role("tab", name=re.compile("Cheapest", re.I))
                    if cheapest_tab.count() > 0:
                        cheapest_tab.first.click(timeout=4000)
                        page.wait_for_timeout(2000)
                except Exception:
                    pass

                page.mouse.wheel(0, 500)
                page.wait_for_timeout(600)

                best = read_cheapest_flight(page, target_date_str)
                if not best:
                    page.mouse.wheel(0, 500)
                    page.wait_for_timeout(800)
                    best = read_cheapest_flight(page, target_date_str)

                if best:
                    best_price = float(best["price_usd"])
                    # Compare true cheapest price against Google's authoritative typical range
                    eval_insights = evaluate_price_against_range(best_price, insights)

                    stops_count = best["stops"]
                    stops_label = "Nonstop" if stops_count == 0 else (f"{stops_count} stop" if stops_count == 1 else f"{stops_count} stops")
                    dur_h = best["duration_min"] // 60
                    dur_m = best["duration_min"] % 60
                    total_time_str = f"{dur_h}h {dur_m}m" if dur_h > 0 else f"{dur_m}m"

                    d0_obj = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                    days_before = max(0, (d0_obj - date.today()).days)
                    day_of_week = d0_obj.strftime("%A")

                    record = {
                        "id": f"GF_{origin}_{destination}_{target_date_str}",
                        "trip_type": "one-way",
                        "leaving": f"{route_info.get('originCity', origin)} ({origin})",
                        "origin_code": origin,
                        "origin_city": route_info.get("originCity", origin),
                        "origin_country": route_info.get("originCountry", "International"),
                        "departure_airport": origin,
                        "departure_airport_name": f"{origin} International Airport",
                        "going": f"{route_info.get('destinationCity', destination)} ({destination})",
                        "destination_code": destination,
                        "destination_city": route_info.get("destinationCity", destination),
                        "destination_country": route_info.get("destinationCountry", "International"),
                        "arrival_airport": destination,
                        "arrival_airport_name": f"{destination} Airport",
                        "date": target_date_str,
                        "departure_date": target_date_str,
                        "return_date": None,
                        "current_lowest_price": best_price,
                        "currency": currency,
                        "price_labels": eval_insights["price_labels"],
                        "price_rating": eval_insights["price_rating"],
                        "prediction_advice": eval_insights["prediction_advice"],
                        "typical_price_min": eval_insights.get("typical_min"),
                        "typical_price_max": eval_insights.get("typical_max"),
                        "typical_price_range": eval_insights.get("typical_range", ""),
                        "economy_price": float(best["price_usd"]),
                        "business_price": None,
                        "airline": best["airline"],
                        "airline_code": best["airline"][:2].upper(),
                        "airline_logo": f"https://www.gstatic.com/flights/airline_logos/70px/{best['airline'][:2].upper()}.png",
                        "stops": stops_label,
                        "stop_count": stops_count,
                        "stop_details": best.get("layover") or ("Direct Nonstop Flight" if stops_count == 0 else f"{stops_count} stop(s)"),
                        "departure_time": best["dep_time"],
                        "arrival_time": best["arr_time"],
                        "total_time": total_time_str,
                        "duration_minutes": best["duration_min"],
                        "days_until_flight": days_before,
                        "days_before": days_before,
                        "day_of_week": day_of_week,
                        "daily_delta": 0.0,
                        "airport": f"{origin} - {destination}",
                        "source_url": url,
                        "adults": 1
                    }

                    if save_to_supabase:
                        try:
                            database.save_flight(record, trip_type="one-way")
                            record["saved_to_db"] = True
                        except Exception as db_err:
                            logger.error(f"Failed to save {record['id']} to Supabase: {db_err}")
                            record["saved_to_db"] = False

                    worker_results.append(record)
                    logger.info(f"[Worker {worker_id}] [OK] [{target_date_str}] Cheapest: ${record['current_lowest_price']} on {record['airline']} ({record['stops']}) | {record['price_rating'].upper()}")

                    with prog_lock:
                        prog_counter[0] += 1
                        if on_progress:
                            on_progress(prog_counter[0], total_days, record)
                else:
                    logger.warning(f"[Worker {worker_id}] No flights found for {target_date_str}")
                    with prog_lock:
                        prog_counter[0] += 1
                        if on_progress:
                            on_progress(prog_counter[0], total_days, {"date": target_date_str, "error": "No flights found"})

            except Exception as day_err:
                logger.error(f"[Worker {worker_id}] Error scraping date {target_date_str}: {day_err}")
                with prog_lock:
                    prog_counter[0] += 1
                    if on_progress:
                        on_progress(prog_counter[0], total_days, {"date": target_date_str, "error": str(day_err)})

            finally:
                try:
                    page.close()
                except Exception:
                    pass

            if delay_sec > 0:
                time.sleep(delay_sec)

    return worker_results


def scrape_30_days_range(
    origin: str,
    destination: str,
    start_date: Optional[str] = None,
    days_count: int = 30,
    currency: str = "USD",
    save_to_supabase: bool = True,
    delay_sec: float = 0.2,
    num_workers: int = 3,
    on_progress: Optional[Callable[[int, int, dict], None]] = None
) -> List[Dict]:
    """
    Parallel Scraper: divides 30 days into 3 workers with 10 days each:
    - Worker 1: Days 0..9 (10 days)
    - Worker 2: Days 10..19 (10 days)
    - Worker 3: Days 20..29 (10 days)
    """
    origin = origin.upper().strip()
    destination = destination.upper().strip()
    route_info = get_route(origin, destination)

    if start_date:
        current_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        current_dt = date.today()

    start_chrome()

    # Determine chunks: for 30 days, exactly 3 chunks of 10 days
    actual_workers = min(num_workers, days_count)
    chunk_size = (days_count + actual_workers - 1) // actual_workers
    chunks = []
    for w in range(actual_workers):
        start_idx = w * chunk_size
        end_idx = min(start_idx + chunk_size, days_count)
        if start_idx < days_count:
            chunks.append(list(range(start_idx, end_idx)))

    logger.info(f"Launching {len(chunks)} parallel workers for {origin}->{destination} ({days_count} days total, ~{chunk_size} days each)...")

    prog_lock = threading.Lock()
    prog_counter = [0]
    all_results = []

    t_start = time.time()
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [
            executor.submit(
                _scrape_worker_chunk,
                w_id + 1,
                origin,
                destination,
                route_info,
                current_dt,
                chunk,
                days_count,
                currency,
                save_to_supabase,
                delay_sec,
                prog_lock,
                prog_counter,
                on_progress
            )
            for w_id, chunk in enumerate(chunks)
        ]
        for f in futures:
            try:
                res = f.result()
                all_results.extend(res)
            except Exception as e:
                logger.error(f"Worker thread error: {e}")

    # Chronologically sort results
    all_results.sort(key=lambda r: r.get("departure_date", ""))
    total_elapsed = round(time.time() - t_start, 1)
    logger.info(f"[OK] Completed {len(all_results)} dates in parallel in {total_elapsed}s ({len(chunks)} workers, ~{chunk_size} days each)!")

    return all_results


def scrape_all_250_routes(
    save_to_supabase: bool = True,
    routes_subset: Optional[List[Dict]] = None,
    on_route_progress: Optional[Callable[[int, int, str, dict], None]] = None,
    stop_checker: Optional[Callable[[], bool]] = None
) -> Dict:
    """Runs 30-day scans across all routes using Headless Chrome."""
    target_routes = routes_subset or TOP_250_ROUTES
    total_routes = len(target_routes)
    total_saved = 0
    route_stats = {}

    for r_idx, route in enumerate(target_routes):
        if stop_checker and stop_checker():
            logger.info("Batch cancelled.")
            break

        orig = route["origin"]
        dest = route["destination"]
        route_key = f"{orig}-{dest}"

        def _day_cb(day_num, total_days, day_data):
            if on_route_progress:
                on_route_progress(r_idx + 1, total_routes, route_key, {
                    "day": day_num,
                    "total_days": total_days,
                    "flight": day_data
                })

        day_results = scrape_30_days_range(
            origin=orig,
            destination=dest,
            days_count=30,
            save_to_supabase=save_to_supabase,
            delay_sec=0.3,
            on_progress=_day_cb
        )

        total_saved += len(day_results)
        route_stats[route_key] = len(day_results)

    return {
        "total_routes_processed": len(route_stats),
        "total_saved_flights": total_saved,
        "details": route_stats
    }


if __name__ == "__main__":
    print("Testing TRUE Cheapest Flight Extraction (CAI -> CDG on 2026-11-05)...")
    t0 = time.time()
    rec = scrape_cheapest_flight("CAI", "CDG", "2026-11-05")
    elapsed = time.time() - t0
    if rec:
        print(f"\n[OK] DONE in {elapsed:.1f}s!")
        print(f"TRUE Cheapest: ${rec['current_lowest_price']} | Airline: {rec['airline']} | Stops: {rec['stops']} | Schedule: {rec['departure_time']}->{rec['arrival_time']}")
    else:
        print("No flight found.")
