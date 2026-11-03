import os
from datetime import datetime, date
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("SUPABASE_POOLER_HOST", "aws-0-eu-west-1.pooler.supabase.com")
PASSWORD = os.getenv("SUPABASE_PASSWORD", "OlcvPogBrneuhYW7")
ONE_WAY_USER = os.getenv("SUPABASE_ONE_WAY_USER", "postgres.uopbpkiogtspjwiyiaxe")
ROUND_TRIP_USER = os.getenv("SUPABASE_ROUND_TRIP_USER", "postgres.afqmwnbuvsothamcbdpv")
PORT = 6543

def get_connection(trip_type: str = "one-way"):
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

def save_flight(flight: dict, trip_type: str = "one-way"):
    """Upserts the cheapest verified flight record into Supabase."""
    table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
    conn = get_connection(trip_type)
    try:
        cur = conn.cursor()

        # 1. Fetch previous price to calculate daily delta
        cur.execute(
            f"SELECT current_lowest_price FROM public.{table} WHERE id = %s",
            (flight["id"],)
        )
        row = cur.fetchone()
        prev_price = float(row[0]) if row and row[0] is not None else None
        daily_delta = round(float(flight["current_lowest_price"]) - prev_price, 2) if prev_price is not None else 0.0

        # Calculate days_before and day_of_week
        dep_str = str(flight.get("departure_date") or flight.get("date"))
        try:
            dep_date_obj = datetime.strptime(dep_str[:10], "%Y-%m-%d").date()
            days_before = flight.get("days_before") if flight.get("days_before") is not None else max(0, (dep_date_obj - date.today()).days)
            day_of_week = flight.get("day_of_week") or dep_date_obj.strftime("%A")
        except Exception:
            days_before = 0
            day_of_week = "Unknown"

        flight["days_before"] = days_before
        flight["day_of_week"] = day_of_week
        flight["daily_delta"] = daily_delta

        # Check if typical_price_min/max is missing and inherit from another date on same route
        t_min = flight.get("typical_price_min")
        t_max = flight.get("typical_price_max")
        t_range = flight.get("typical_price_range")

        if t_min is None or t_max is None:
            try:
                cur.execute(
                    f"SELECT typical_price_min, typical_price_max, typical_price_range FROM public.{table} WHERE origin_code = %s AND destination_code = %s AND typical_price_min IS NOT NULL LIMIT 1",
                    (flight["origin_code"], flight["destination_code"])
                )
                r_row = cur.fetchone()
                if r_row:
                    t_min = float(r_row[0]) if r_row[0] is not None else None
                    t_max = float(r_row[1]) if r_row[1] is not None else None
                    t_range = r_row[2]
            except Exception:
                pass

        flight["typical_price_min"] = t_min
        flight["typical_price_max"] = t_max
        flight["typical_price_range"] = t_range

        # Check is_self_transfer
        is_st = bool(
            flight.get("is_self_transfer") or
            "self transfer" in str(flight.get("airline", "")).lower() or
            "self-transfer" in str(flight.get("airline", "")).lower() or
            "self transfer" in str(flight.get("stop_details", "")).lower() or
            "self-transfer" in str(flight.get("stop_details", "")).lower()
        )
        flight["is_self_transfer"] = is_st

        # 2. Upsert flight
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
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                current_lowest_price = EXCLUDED.current_lowest_price,
                price_labels = EXCLUDED.price_labels,
                price_rating = EXCLUDED.price_rating,
                prediction_advice = EXCLUDED.prediction_advice,
                typical_price_min = COALESCE(EXCLUDED.typical_price_min, public.{table}.typical_price_min),
                typical_price_max = COALESCE(EXCLUDED.typical_price_max, public.{table}.typical_price_max),
                typical_price_range = COALESCE(EXCLUDED.typical_price_range, public.{table}.typical_price_range),
                economy_price = EXCLUDED.economy_price,
                business_price = EXCLUDED.business_price,
                airline = EXCLUDED.airline,
                airline_code = EXCLUDED.airline_code,
                airline_logo = EXCLUDED.airline_logo,
                stops = EXCLUDED.stops,
                is_self_transfer = EXCLUDED.is_self_transfer,
                departure_time = EXCLUDED.departure_time,
                arrival_time = EXCLUDED.arrival_time,
                total_time = EXCLUDED.total_time,
                duration_minutes = EXCLUDED.duration_minutes,
                days_before = EXCLUDED.days_before,
                day_of_week = EXCLUDED.day_of_week,
                daily_delta = EXCLUDED.daily_delta,
                scraped_at = NOW();
        """

        cur.execute(sql, (
            flight["id"],
            trip_type,
            flight["leaving"],
            flight["origin_code"],
            flight.get("origin_city"),
            flight.get("origin_country"),
            flight["departure_airport"],
            flight.get("departure_airport_name"),
            flight["going"],
            flight["destination_code"],
            flight.get("destination_city"),
            flight.get("destination_country"),
            flight["arrival_airport"],
            flight.get("arrival_airport_name"),
            flight["date"],
            flight.get("departure_date") or flight["date"],
            flight.get("return_date"),
            flight["current_lowest_price"],
            flight.get("currency", "USD"),
            flight.get("price_labels", "Prices are currently typical"),
            flight.get("price_rating", "typical"),
            flight.get("prediction_advice"),
            flight.get("typical_price_min"),
            flight.get("typical_price_max"),
            flight.get("typical_price_range"),
            flight.get("economy_price", flight["current_lowest_price"]),
            flight.get("business_price"),
            flight["airline"],
            flight.get("airline_code"),
            flight.get("airline_logo"),
            flight.get("stops", "Nonstop"),
            flight.get("stop_count", 0),
            flight.get("stop_details"),
            flight.get("is_self_transfer", False),
            flight.get("departure_time"),
            flight.get("arrival_time"),
            flight.get("total_time"),
            flight.get("duration_minutes"),
            flight.get("days_until_flight", days_before),
            days_before,
            day_of_week,
            daily_delta,
            flight.get("airport"),
            flight.get("source_url"),
            flight.get("adults", 2)
        ))

        # 3. Log to history
        route_id = f"{flight['origin_code']}-{flight['destination_code']}"
        cur.execute("""
            INSERT INTO public.flight_price_history (
                route_id, trip_type, origin, destination, flight_date, price, price_rating, airline, daily_delta, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
        """, (
            route_id,
            trip_type,
            flight["origin_code"],
            flight["destination_code"],
            flight["date"],
            flight["current_lowest_price"],
            flight.get("price_rating", "fair"),
            flight["airline"],
            daily_delta
        ))

        conn.commit()
        cur.close()
        return True
    finally:
        conn.close()

def get_flights(trip_type: str = "one-way", limit: int = 250):
    table = "round_trip_flights" if trip_type == "round-trip" else "one_way_flights"
    conn = get_connection(trip_type)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(f"SELECT * FROM public.{table} ORDER BY COALESCE(departure_date, date) ASC, current_lowest_price ASC LIMIT %s;", (limit,))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def clear_flights(trip_type: str = "one-way"):
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

