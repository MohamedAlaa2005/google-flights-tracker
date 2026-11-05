import time
import os
from datetime import datetime, date
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("SUPABASE_POOLER_HOST", "aws-0-eu-west-1.pooler.supabase.com")
PASSWORD = os.getenv("SUPABASE_PASSWORD", "OlcvPogBrneuhYW7")
ONE_WAY_USER = os.getenv("SUPABASE_ONE_WAY_USER", "postgres.uopbpkiogtspjwiyiaxe")
ROUND_TRIP_USER = os.getenv("SUPABASE_ROUND_TRIP_USER", "postgres.afqmwnbuvsothamcbdpv")
PORT = 6543

def get_connection(trip_type: str = "round-trip"):
    user = ROUND_TRIP_USER if trip_type == "round-trip" else ONE_WAY_USER
    return psycopg2.connect(
        host=HOST,
        user=user,
        password=PASSWORD,
        dbname="postgres",
        port=PORT,
        sslmode="require"
    )

def init_tables():
    """Ensures required tables and departure_date columns exist on Supabase."""
    for trip_type in ["one-way", "round-trip"]:
        table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
        try:
            conn = get_connection(trip_type)
            cur = conn.cursor()
            
            # Ensure table exists
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS public.{table} (
                    id TEXT PRIMARY KEY,
                    trip_type VARCHAR(20) NOT NULL DEFAULT '{trip_type}',
                    leaving TEXT NOT NULL,
                    origin_code VARCHAR(10) NOT NULL,
                    origin_city TEXT,
                    origin_country TEXT,
                    departure_airport VARCHAR(10) NOT NULL,
                    departure_airport_name TEXT,
                    going TEXT NOT NULL,
                    destination_code VARCHAR(10) NOT NULL,
                    destination_city TEXT,
                    destination_country TEXT,
                    arrival_airport VARCHAR(10) NOT NULL,
                    arrival_airport_name TEXT,
                    date DATE NOT NULL,
                    departure_date DATE,
                    return_date DATE,
                    current_lowest_price NUMERIC(10, 2) NOT NULL,
                    currency VARCHAR(10) DEFAULT 'USD',
                    price_labels TEXT,
                    price_rating VARCHAR(20) DEFAULT 'fair',
                    prediction_advice TEXT,
                    economy_price NUMERIC(10, 2),
                    business_price NUMERIC(10, 2),
                    airline TEXT NOT NULL,
                    airline_code VARCHAR(10),
                    airline_logo TEXT,
                    stops VARCHAR(50),
                    stop_count INT DEFAULT 0,
                    stop_details TEXT,
                    departure_time VARCHAR(20),
                    arrival_time VARCHAR(20),
                    total_time VARCHAR(20),
                    duration_minutes INT,
                    days_until_flight INT,
                    airport VARCHAR(50),
                    source_url TEXT,
                    adults INT DEFAULT 2,
                    scraped_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # Add departure_date, days_before, day_of_week, daily_delta columns if missing
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS departure_date DATE;")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS days_before INT;")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS day_of_week VARCHAR(20);")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS daily_delta NUMERIC(10, 2) DEFAULT 0;")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS typical_price_min NUMERIC(10, 2);")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS typical_price_max NUMERIC(10, 2);")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS typical_price_range VARCHAR(50);")
            cur.execute(f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS is_self_transfer BOOLEAN DEFAULT FALSE;")

            # Backfill any flights with self transfer in airline or details
            cur.execute(f"""
                UPDATE public.{table}
                SET is_self_transfer = TRUE
                WHERE airline ILIKE '%self transfer%'
                   OR airline ILIKE '%self-transfer%'
                   OR stop_details ILIKE '%self transfer%'
                   OR stop_details ILIKE '%self-transfer%';
            """)

            # Backfill any null or Unknown day_of_week / days_before
            cur.execute(f"""
                UPDATE public.{table}
                SET 
                    day_of_week = TRIM(TO_CHAR(COALESCE(departure_date, date), 'Day')),
                    days_before = GREATEST(0, (COALESCE(departure_date, date) - CURRENT_DATE))
                WHERE day_of_week IS NULL OR day_of_week = 'Unknown';
            """)

            # Backfill typical price range from other dates on the same route if missing
            cur.execute(f"""
                WITH route_ranges AS (
                    SELECT origin_code, destination_code, 
                           MAX(typical_price_min) as t_min, 
                           MAX(typical_price_max) as t_max,
                           MAX(typical_price_range) as t_range
                    FROM public.{table}
                    WHERE typical_price_min IS NOT NULL
                    GROUP BY origin_code, destination_code
                )
                UPDATE public.{table} f
                SET 
                    typical_price_min = rr.t_min,
                    typical_price_max = rr.t_max,
                    typical_price_range = rr.t_range
                FROM route_ranges rr
                WHERE f.origin_code = rr.origin_code 
                  AND f.destination_code = rr.destination_code
                  AND f.typical_price_min IS NULL;
            """)

            # Ensure flight_price_history exists
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS public.flight_price_history (
                    id BIGSERIAL PRIMARY KEY,
                    route_id VARCHAR(50) NOT NULL,
                    trip_type VARCHAR(20) NOT NULL DEFAULT '{trip_type}',
                    origin VARCHAR(10) NOT NULL,
                    destination VARCHAR(10) NOT NULL,
                    flight_date DATE NOT NULL,
                    price NUMERIC(10, 2) NOT NULL,
                    price_rating VARCHAR(20),
                    airline TEXT,
                    daily_delta NUMERIC(10, 2) DEFAULT 0,
                    recorded_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            conn.commit()
            cur.close()
            conn.close()
            print(f"[Supabase DB] Table {table} initialized successfully.")
        except Exception as e:
            print(f"[Supabase DB] Error initializing {table}: {e}")

def save_flights_batch(flights: list, trip_type: str = "round-trip") -> int:
    """
    High-performance batch insertion for flight records.
    Pre-fetches typical price ranges and previous prices in bulk,
    then executes a single atomic batch query via execute_values.
    Drastically eliminates lock contention and connection pool exhaustion
    when multiple concurrent scraper workflows insert simultaneously.
    """
    if not flights:
        return 0

    table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
    conn = get_connection(trip_type)
    try:
        cur = conn.cursor()

        # 1. Collect unique route pairs in this batch
        route_pairs = list({(f["origin_code"], f["destination_code"]) for f in flights if f.get("origin_code") and f.get("destination_code")})

        typical_cache = {}
        prev_prices = {}

        if route_pairs:
            # Pre-fetch typical price ranges
            try:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (origin_code, destination_code)
                           origin_code, destination_code, typical_price_min, typical_price_max, typical_price_range
                    FROM public.{table}
                    WHERE (origin_code, destination_code) IN %s AND typical_price_min IS NOT NULL
                    """,
                    (tuple(route_pairs),)
                )
                for r in cur.fetchall():
                    typical_cache[(r[0], r[1])] = (
                        float(r[2]) if r[2] is not None else None,
                        float(r[3]) if r[3] is not None else None,
                        r[4]
                    )
            except Exception:
                pass

            # Pre-fetch latest recorded prices to compute daily_delta
            try:
                cur.execute(
                    f"""
                    SELECT DISTINCT ON (origin_code, destination_code, departure_date)
                           origin_code, destination_code, departure_date, current_lowest_price
                    FROM public.{table}
                    WHERE (origin_code, destination_code) IN %s
                    ORDER BY origin_code, destination_code, departure_date, scraped_at DESC
                    """,
                    (tuple(route_pairs),)
                )
                for r in cur.fetchall():
                    d_str = str(r[2])[:10] if r[2] else ""
                    prev_prices[(r[0], r[1], d_str)] = float(r[3]) if r[3] is not None else None
            except Exception:
                pass

        # 2. Build rows for batch execution
        base_time = int(time.time() * 1000)
        rows_to_insert = []

        for idx, fl in enumerate(flights):
            dep_str = str(fl.get("departure_date") or fl.get("date"))[:10]
            ret_str = str(fl.get("return_date"))[:10] if fl.get("return_date") else None
            orig = fl["origin_code"]
            dest = fl["destination_code"]
            curr_price = float(fl.get("current_lowest_price") or 0.0)

            # Daily delta
            prev_p = prev_prices.get((orig, dest, dep_str))
            daily_delta = round(curr_price - prev_p, 2) if prev_p is not None else 0.0

            # Dates
            try:
                dep_dt = datetime.strptime(dep_str, "%Y-%m-%d").date()
                days_before = fl.get("days_before") if fl.get("days_before") is not None else max(0, (dep_dt - date.today()).days)
                day_of_week = fl.get("day_of_week") or dep_dt.strftime("%A")
            except Exception:
                days_before = 0
                day_of_week = "Unknown"

            # Typical range
            t_min = fl.get("typical_price_min")
            t_max = fl.get("typical_price_max")
            t_range = fl.get("typical_price_range")
            if (t_min is None or t_max is None) and (orig, dest) in typical_cache:
                c_min, c_max, c_rng = typical_cache[(orig, dest)]
                t_min = t_min or c_min
                t_max = t_max or c_max
                t_range = t_range or c_rng

            # Self-transfer check
            is_st = bool(
                fl.get("is_self_transfer") or
                "self transfer" in str(fl.get("airline", "")).lower() or
                "self-transfer" in str(fl.get("airline", "")).lower() or
                "self transfer" in str(fl.get("stop_details", "")).lower() or
                "self-transfer" in str(fl.get("stop_details", "")).lower()
            )

            raw_id = fl.get("id") or f"GF_{orig}_{dest}_{dep_str}"
            unique_id = f"{raw_id}_{base_time}_{idx}"

            row_tuple = (
                unique_id,
                trip_type,
                fl.get("leaving") or f"{orig} to {dest}",
                orig,
                fl.get("origin_city"),
                fl.get("origin_country"),
                fl.get("departure_airport") or orig,
                fl.get("departure_airport_name"),
                fl.get("going") or f"{dest}",
                dest,
                fl.get("destination_city"),
                fl.get("destination_country"),
                fl.get("arrival_airport") or dest,
                fl.get("arrival_airport_name"),
                dep_str,
                dep_str,
                ret_str,
                curr_price,
                fl.get("currency", "USD"),
                fl.get("price_labels", "Prices are currently typical"),
                fl.get("price_rating", "typical"),
                fl.get("prediction_advice"),
                t_min,
                t_max,
                t_range,
                curr_price,
                fl.get("business_price"),
                fl.get("airline") or "Multiple Airlines",
                fl.get("airline_code"),
                fl.get("airline_logo"),
                fl.get("stops") or "Available",
                fl.get("stop_count", 0),
                fl.get("stop_details"),
                is_st,
                fl.get("departure_time"),
                fl.get("arrival_time"),
                fl.get("total_time") or "N/A",
                fl.get("duration_minutes"),
                fl.get("days_until_flight", days_before),
                days_before,
                day_of_week,
                daily_delta,
                fl.get("airport") or f"{orig} - {dest}",
                fl.get("source_url"),
                fl.get("adults", 1)
            )
            rows_to_insert.append(row_tuple)

        # 3. Fast atomic execute_values
        sql = f"""
            INSERT INTO public.{table} (
                id, trip_type, leaving, origin_code, origin_city, origin_country,
                departure_airport, departure_airport_name,
                going, destination_code, destination_city, destination_country,
                arrival_airport, arrival_airport_name,
                date, departure_date, return_date, current_lowest_price, currency,
                price_labels, price_rating, prediction_advice,
                typical_price_min, typical_price_max, typical_price_range,
                economy_price, business_price,
                airline, airline_code, airline_logo, stops, stop_count, stop_details, is_self_transfer,
                departure_time, arrival_time, total_time, duration_minutes, days_until_flight,
                days_before, day_of_week, daily_delta,
                airport, source_url, adults, scraped_at
            ) VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                current_lowest_price = EXCLUDED.current_lowest_price,
                scraped_at = NOW();
        """
        execute_values(
            cur,
            sql,
            rows_to_insert,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
            page_size=500
        )
        conn.commit()
        cur.close()
        return len(rows_to_insert)
    finally:
        conn.close()


def save_flight(flight: dict, trip_type: str = "round-trip"):
    """Saves a single flight record (wraps save_flights_batch)."""
    return save_flights_batch([flight], trip_type=trip_type) > 0


def get_flights(trip_type: str = "round-trip", limit: int = 250):
    table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
    conn = get_connection(trip_type)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"SELECT * FROM public.{table} ORDER BY scraped_at DESC, COALESCE(departure_date, date) ASC LIMIT %s;", (limit,))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def clear_flights(trip_type: str = "round-trip"):
    table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
    conn = get_connection(trip_type)
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM public.{table};")
        conn.commit()
        cur.close()
        return True
    finally:
        conn.close()

def get_db_stats():
    stats = {}
    for t in ["one-way", "round-trip"]:
        table = "round_trip_flights" if t == "round-trip" else "one_way_flights"
        try:
            conn = get_connection(t)
            cur = conn.cursor()
            cur.execute(f"SELECT count(*) FROM public.{table};")
            cnt = cur.fetchone()[0]
            cur.close()
            conn.close()
            stats[t] = {"connected": True, "count": cnt, "host": HOST}
        except Exception as e:
            stats[t] = {"connected": False, "count": 0, "error": str(e), "host": HOST}
    return stats

def test_supabase_connection():
    import time
    start = time.time()
    try:
        conn = get_connection("one-way")
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.close()
        conn.close()
        latency_ms = round((time.time() - start) * 1000, 1)
        return {
            "success": True,
            "host": HOST,
            "port": PORT,
            "latencyMs": latency_ms,
            "oneWayUser": ONE_WAY_USER,
            "roundTripUser": ROUND_TRIP_USER,
            "message": f"Connected to Supabase ({latency_ms}ms)"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "host": HOST
        }

