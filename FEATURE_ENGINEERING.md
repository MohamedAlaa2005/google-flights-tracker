# Flight Price Prediction: Feature Engineering Guide

This document describes all **base database columns** and provides exact specifications, formulas, SQL queries, and Python (Pandas) code to generate **derived features** for flight price intelligence and Machine Learning models.

---

## 1. Database Schema Overview (Ground Truth Features)

These features cannot be calculated or derived from existing columns and are stored directly in Supabase (`one_way_flights` and `round_trip_flights`):

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | `TEXT` | Unique flight record snapshot identifier |
| `origin_code` | `VARCHAR(10)` | 3-letter IATA origin airport code (e.g., `'JFK'`, `'CAI'`) |
| `destination_code` | `VARCHAR(10)` | 3-letter IATA destination airport code (e.g., `'LHR'`, `'DXB'`) |
| `origin_city` | `TEXT` | Origin city name |
| `destination_city` | `TEXT` | Destination city name |
| `origin_country` | `TEXT` | Origin country |
| `destination_country` | `TEXT` | Destination country |
| `departure_date` | `DATE` | Outbound flight departure date (`YYYY-MM-DD`) |
| `return_date` | `DATE` | Inbound return date (only for round trips; `NULL` for one-way) |
| `departure_time` | `VARCHAR(20)` | Scheduled local departure time (`HH:MM`, 24-hour) |
| `arrival_time` | `VARCHAR(20)` | Scheduled local arrival time (`HH:MM`, 24-hour) |
| `duration_minutes` | `INT` | Total trip duration in minutes |
| `distance_km` | `INT` | Great Circle flight distance between airports in kilometers |
| `co2_kg` | `INT` | Flight carbon emissions in kilograms of CO2e (from Google Flights) |
| `airline` | `TEXT` | Operating / ticketing airline name |
| `airline_code` | `VARCHAR(10)` | 2-letter IATA airline code |
| `stops` | `VARCHAR(50)` | Stop label (`'Nonstop'`, `'1 stop'`, `'2 stops'`) |
| `stop_count` | `INT` | Integer number of stops (`0`, `1`, `2`) |
| `stop_details` | `TEXT` | Layover details (e.g. `'50 min ZRH'`, `'Direct Nonstop Flight'`) |
| `is_self_transfer` | `BOOLEAN` | Whether the itinerary requires self-transfer between tickets |
| `current_lowest_price` | `NUMERIC(10,2)` | Target variable: lowest available fare in USD |
| `price_delta` | `NUMERIC(10,2)` | Price change compared to previous scrape on this route/date |
| `price_rating` | `VARCHAR(20)` | Google rating category (`'cheapest'`, `'low'`, `'typical'`, `'high'`) |
| `typical_price_min` | `NUMERIC(10,2)` | Lower bound of Google Flights typical price range |
| `typical_price_max` | `NUMERIC(10,2)` | Upper bound of Google Flights typical price range |
| `days_before` | `INT` | Booking horizon: days remaining between scrape and departure |
| `day_of_week` | `VARCHAR(20)` | Day name (`'Monday'`, `'Tuesday'`, etc.) |
| `scraped_at` | `TIMESTAMPTZ` | Timestamp when the fare was scraped |

---

## 2. Derived Features (How to Calculate)

All features below are **deterministic** and can be generated directly using SQL (during query time) or Python/Pandas (during ML pipeline preprocessing).

### A. Geographic & Route Generalization

#### 1. `haul_type` (Route Distance Category)
- **Concept:** Short-haul regional flights have higher fixed costs per km than intercontinental long-haul flights.
- **Formula:**
  - `< 1,500 km` $\to$ `'short-haul'`
  - `1,500 – 4,000 km` $\to$ `'medium-haul'`
  - `> 4,000 km` $\to$ `'long-haul'`
- **SQL:**
  ```sql
  CASE 
    WHEN distance_km < 1500 THEN 'short-haul'
    WHEN distance_km <= 4000 THEN 'medium-haul'
    ELSE 'long-haul'
  END AS haul_type
  ```
- **Python / Pandas:**
  ```python
  df['haul_type'] = pd.cut(
      df['distance_km'], 
      bins=[0, 1500, 4000, float('inf')], 
      labels=['short-haul', 'medium-haul', 'long-haul']
  )
  ```

#### 2. `is_international` (Cross-Border Flight)
- **Concept:** International flights incur different taxes, customs fees, and regulatory requirements.
- **SQL:**
  ```sql
  (origin_country != destination_country) AS is_international
  ```
- **Python / Pandas:**
  ```python
  df['is_international'] = df['origin_country'] != df['destination_country']
  ```

---

### B. Temporal & Calendar Seasonality

#### 3. `departure_month` & `departure_quarter`
- **Concept:** Captures seasonal demand surges (summer holiday peaks in July/August vs winter low season in February).
- **SQL:**
  ```sql
  EXTRACT(MONTH FROM departure_date)::INT AS departure_month,
  EXTRACT(QUARTER FROM departure_date)::INT AS departure_quarter
  ```
- **Python / Pandas:**
  ```python
  dep_dt = pd.to_datetime(df['departure_date'])
  df['departure_month'] = dep_dt.dt.month
  df['departure_quarter'] = dep_dt.dt.quarter
  ```

#### 4. `is_weekend` (Departure Weekend Indicator)
- **Concept:** Friday, Saturday, and Sunday departures typically command higher leisure premiums.
- **SQL:**
  ```sql
  day_of_week IN ('Friday', 'Saturday', 'Sunday') AS is_weekend
  ```
- **Python / Pandas:**
  ```python
  df['is_weekend'] = df['day_of_week'].isin(['Friday', 'Saturday', 'Sunday'])
  ```

#### 5. `departure_period` (Time-of-Day Window)
- **Concept:** Red-eye (night) and early dawn flights are discounted compared to business-friendly morning and afternoon hours.
- **Definition:**
  - `00:00 – 05:59` $\to$ `'night'` (red-eye)
  - `06:00 – 11:59` $\to$ `'morning'`
  - `12:00 – 17:59` $\to$ `'afternoon'`
  - `18:00 – 23:59` $\to$ `'evening'`
- **SQL:**
  ```sql
  CASE 
    WHEN departure_time < '06:00' THEN 'night'
    WHEN departure_time < '12:00' THEN 'morning'
    WHEN departure_time < '18:00' THEN 'afternoon'
    ELSE 'evening'
  END AS departure_period
  ```
- **Python / Pandas:**
  ```python
  dep_hour = pd.to_datetime(df['departure_time'], format='%H:%M', errors='coerce').dt.hour
  df['departure_period'] = pd.cut(
      dep_hour, 
      bins=[-1, 5, 11, 17, 24], 
      labels=['night', 'morning', 'afternoon', 'evening']
  )
  ```

---

### C. Round-Trip Stay Duration

#### 6. `trip_duration_days` (Duration of Stay)
- **Concept:** Business trips (2–4 days) vs vacation stays (7, 14, 21 days) have vastly different pricing curves.
- **SQL:**
  ```sql
  (return_date - departure_date) AS trip_duration_days
  ```
- **Python / Pandas:**
  ```python
  df['trip_duration_days'] = (pd.to_datetime(df['return_date']) - pd.to_datetime(df['departure_date'])).dt.days
  ```

---

### D. Pricing & Efficiency Metrics

#### 7. `price_per_km` (Route Deal Metric)
- **Concept:** Normalizes airfare by distance, enabling direct cost comparisons across short and long routes.
- **SQL:**
  ```sql
  ROUND(current_lowest_price / NULLIF(distance_km, 0), 4) AS price_per_km
  ```
- **Python / Pandas:**
  ```python
  df['price_per_km'] = (df['current_lowest_price'] / df['distance_km'].replace(0, np.nan)).round(4)
  ```

#### 8. `effective_speed_kmh` (Speed Efficiency)
- **Concept:** Flags excessive layovers where effective ground speed drops significantly.
- **SQL:**
  ```sql
  ROUND(distance_km / NULLIF(duration_minutes / 60.0, 0), 1) AS speed_kmh
  ```
- **Python / Pandas:**
  ```python
  df['speed_kmh'] = (df['distance_km'] / (df['duration_minutes'] / 60.0).replace(0, np.nan)).round(1)
  ```

#### 9. `typical_price_midpoint` & `discount_pct`
- **Concept:** Quantifies how discounted the current fare is against Google Flights' historical benchmark.
- **SQL:**
  ```sql
  ROUND((typical_price_min + typical_price_max) / 2.0, 2) AS typical_price_midpoint,
  ROUND(
    (((typical_price_min + typical_price_max) / 2.0) - current_lowest_price) 
    / NULLIF((typical_price_min + typical_price_max) / 2.0, 0) * 100.0, 
    2
  ) AS discount_pct
  ```
- **Python / Pandas:**
  ```python
  midpoint = (df['typical_price_min'] + df['typical_price_max']) / 2.0
  df['typical_price_midpoint'] = midpoint
  df['discount_pct'] = (((midpoint - df['current_lowest_price']) / midpoint.replace(0, np.nan)) * 100).round(2)
  ```

---

## 3. Ready-to-Use SQL Query for ML Export

```sql
SELECT 
    id,
    origin_code,
    destination_code,
    departure_date,
    return_date,
    days_before,
    day_of_week,
    EXTRACT(MONTH FROM departure_date)::INT AS departure_month,
    day_of_week IN ('Friday', 'Saturday', 'Sunday') AS is_weekend,
    (origin_country != destination_country) AS is_international,
    (return_date - departure_date) AS trip_duration_days,
    distance_km,
    CASE 
        WHEN distance_km < 1500 THEN 'short-haul'
        WHEN distance_km <= 4000 THEN 'medium-haul'
        ELSE 'long-haul'
    END AS haul_type,
    duration_minutes,
    stops,
    stop_count,
    is_self_transfer,
    airline,
    co2_kg,
    current_lowest_price AS price_usd,
    price_delta,
    price_rating,
    ROUND((typical_price_min + typical_price_max) / 2.0, 2) AS typical_midpoint,
    ROUND(current_lowest_price / NULLIF(distance_km, 0), 4) AS price_per_km,
    scraped_at
FROM public.round_trip_flights
WHERE distance_km IS NOT NULL
ORDER BY scraped_at DESC;
```

