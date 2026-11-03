import os
import sys
import json
import asyncio
import threading
from typing import Optional, List
from datetime import datetime, date, timedelta
from pydantic import BaseModel
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import database
import google_flights_engine
from scheduler_service import scheduler_instance
from routes import TOP_250_ROUTES, get_route

sys.stdout.reconfigure(encoding='utf-8')

IS_VERCEL = bool(os.getenv("VERCEL"))

app = FastAPI(title="Google Flights Intelligence Tracker", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Supabase tables safely
try:
    database.init_tables()
except Exception as e:
    print(f"[Supabase DB] Table initialization deferred: {e}")

# Start background 12:00 PM daily scheduler only in persistent server mode (not on serverless)
if not IS_VERCEL:
    scheduler_instance.start()

# In-memory scraper status log
scrape_logs = []

def add_log(msg: str, status_type: str = "info"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    scrape_logs.insert(0, {"time": timestamp, "message": msg, "type": status_type})
    if len(scrape_logs) > 60:
        scrape_logs.pop()

# Wire scheduler logs to web_app live log console
scheduler_instance.log_callback = add_log

# Global state for interactive scraping progress
current_scrape_state = {
    "is_running": False,
    "mode": "idle",
    "route": "",
    "current_day": 0,
    "total_days": 30,
    "saved_count": 0,
    "latest_flight": None
}

# --- Request Models ---
class SingleScrapeRequest(BaseModel):
    origin: str
    destination: str
    flight_date: str
    currency: str = "USD"
    adults: int = 1

class RangeScrapeRequest(BaseModel):
    origin: str
    destination: str
    start_date: Optional[str] = None
    currency: str = "USD"
    adults: int = 1

class Batch250Request(BaseModel):
    currency: str = "USD"
    adults: int = 1

# --- API Endpoints ---
@app.get("/api/status")
def get_status():
    return database.get_db_stats()

@app.get("/api/supabase/test")
def test_supabase():
    return database.test_supabase_connection()

@app.get("/api/flights")
def get_flights(trip_type: str = "round-trip", limit: int = 100):
    return database.get_flights(trip_type=trip_type, limit=limit)

@app.get("/api/routes")
def get_routes():
    return TOP_250_ROUTES

@app.get("/api/logs")
def get_logs():
    return scrape_logs

@app.get("/api/scrape/progress")
def get_scrape_progress():
    sched_status = scheduler_instance.get_status()
    return {
        "interactive": current_scrape_state,
        "scheduler": sched_status
    }

@app.post("/api/clear")
def clear_db(trip_type: str = "round-trip"):
    success = database.clear_flights(trip_type)
    add_log(f"Cleared {trip_type} database table in Supabase.", "warn")
    return {"success": success}

# --- Scheduler Endpoints ---
@app.get("/api/scheduler/status")
def get_scheduler_status():
    return scheduler_instance.get_status()

@app.post("/api/scheduler/trigger")
def trigger_scheduler_now():
    add_log("Manual trigger for Daily 12:00 PM routine started in background.", "info")
    res = scheduler_instance.trigger_now()
    return res

@app.post("/api/scheduler/toggle")
def toggle_scheduler(enable: bool = True):
    if enable:
        scheduler_instance.start()
        add_log("Daily 12:00 PM Scheduler activated.", "success")
    else:
        scheduler_instance.stop()
        add_log("Daily 12:00 PM Scheduler stopped.", "warn")
    return scheduler_instance.get_status()

# --- Google Flights Scraper Endpoints ---
@app.post("/api/scrape/google-flight")
def scrape_single_flight(req: SingleScrapeRequest):
    orig = req.origin.upper().strip()
    dest = req.destination.upper().strip()
    add_log(f"Scraping cheapest flight for {orig} -> {dest} on {req.flight_date} via Google Flights...", "info")
    
    record = google_flights_engine.scrape_cheapest_flight(
        origin=orig,
        destination=dest,
        flight_date=req.flight_date,
        adults=req.adults,
        currency=req.currency
    )

    if record:
        database.save_flight(record, trip_type="one-way")
        add_log(f"Saved cheapest: {record['airline']} (${record['current_lowest_price']}) to Supabase!", "success")
        return {"success": True, "flight": record}
    else:
        add_log(f"No flight options returned for {orig} -> {dest} on {req.flight_date}.", "warn")
        return {"success": False, "error": "No flights found"}

def _run_30_days_task(origin: str, destination: str, start_date: str, currency: str, adults: int):
    global current_scrape_state
    if IS_VERCEL:
        add_log("Vercel Serverless Mode: Live Supabase database and queries are fully active.", "info")
        add_log("Notice: Playwright Chrome automation requires local execution (python run_250_routes.py) or Docker.", "warn")
        current_scrape_state["is_running"] = False
        current_scrape_state["mode"] = "idle"
        return

    current_scrape_state["is_running"] = True
    current_scrape_state["mode"] = "30-days"
    current_scrape_state["route"] = f"{origin}-{destination}"
    current_scrape_state["current_day"] = 0
    current_scrape_state["total_days"] = 30
    current_scrape_state["saved_count"] = 0

    add_log(f"Started 30-Day Cheapest Scan: {origin} -> {destination} from {start_date}...", "info")

    def on_progress(cur, tot, fl):
        current_scrape_state["current_day"] = cur
        if fl and "current_lowest_price" in fl:
            current_scrape_state["saved_count"] += 1
            current_scrape_state["latest_flight"] = fl
            add_log(f"Day {cur}/30 [{fl['date']}]: ${fl['current_lowest_price']} ({fl['airline']} - {fl['stops']})", "info")
        else:
            d_str = fl.get("date", f"Day {cur}") if fl else f"Day {cur}"
            add_log(f"Day {cur}/30 [{d_str}]: No flights available on this date", "warn")

    try:
        results = google_flights_engine.scrape_round_trip_range(
            origin=origin,
            destination=destination,
            start_date=start_date,
            days_count=30,
            return_window_days=13,
            save_to_supabase=True,
            delay_sec=0.25,
            on_progress=on_progress
        )
        current_scrape_state["current_day"] = 30
        add_log(f"Completed 30-day scan for {origin}->{destination}! Saved {len(results)} cheapest dates to Supabase.", "success")
    except Exception as e:
        add_log(f"Error in 30-day scan: {str(e)}", "error")
    finally:
        current_scrape_state["is_running"] = False
        current_scrape_state["mode"] = "idle"

@app.post("/api/scrape/google-30days")
def scrape_30_days(req: RangeScrapeRequest, bg: BackgroundTasks):
    orig = req.origin.upper().strip()
    dest = req.destination.upper().strip()
    start_d = req.start_date or date.today().strftime("%Y-%m-%d")

    if current_scrape_state["is_running"] or scheduler_instance.is_scraping:
        return {"success": False, "error": "Another scrape job is already currently running. Please wait for it to finish."}

    bg.add_task(_run_30_days_task, orig, dest, start_d, req.currency, req.adults)
    return {"success": True, "message": f"30-day scan for {orig} -> {dest} started in background."}

def _run_250_routes_task(currency: str, adults: int):
    global current_scrape_state
    if IS_VERCEL:
        add_log("Vercel Serverless Mode: Live Supabase database and queries are fully active.", "info")
        add_log("Notice: Full 250 routes batch scan requires local execution (python run_250_routes.py) or Docker.", "warn")
        current_scrape_state["is_running"] = False
        current_scrape_state["mode"] = "idle"
        return

    current_scrape_state["is_running"] = True
    current_scrape_state["mode"] = "250-routes"
    add_log("Starting Global 250 Routes Batch Scan across 30 days...", "info")

    def on_route_prog(r_idx, total_r, r_key, p_data):
        current_scrape_state["route"] = r_key
        current_scrape_state["current_day"] = p_data.get("day", 0)
        current_scrape_state["total_days"] = p_data.get("total_days", 30)
        fl = p_data.get("flight")
        if fl and "current_lowest_price" in fl:
            current_scrape_state["saved_count"] += 1
            current_scrape_state["latest_flight"] = fl
            add_log(f"[{r_idx}/{total_r}] {r_key} Day {p_data.get('day')}: ${fl['current_lowest_price']} ({fl['airline']})", "info")

    try:
        stats = google_flights_engine.scrape_all_250_routes_round_trip(
            save_to_supabase=True,
            on_route_progress=on_route_prog
        )
        add_log(f"All 250 Routes Completed! Total cheapest flights saved to Supabase: {stats['total_saved_flights']}", "success")
    except Exception as e:
        add_log(f"Batch 250 routes error: {str(e)}", "error")
    finally:
        current_scrape_state["is_running"] = False
        current_scrape_state["mode"] = "idle"

@app.post("/api/scrape/google-all-250")
def scrape_all_250(req: Batch250Request, bg: BackgroundTasks):
    if current_scrape_state["is_running"] or scheduler_instance.is_scraping:
        return {"success": False, "error": "A scraping task is already running."}

    bg.add_task(_run_250_routes_task, req.currency, req.adults)
    return {"success": True, "message": "Global 250 routes 30-day scan launched in background."}

# --- HTML5/Tailwind Modern Dashboard UI ---
@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTML_CONTENT

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Google Flights Intelligence & Price Tracker</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Google Fonts: Plus Jakarta Sans -->
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .custom-scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: #f1f1f1; border-radius: 4px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900 min-h-screen flex flex-col">

  <!-- Top Header Navigation -->
  <header class="bg-white border-b border-slate-200 sticky top-0 z-40 shadow-sm">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="size-10 rounded-xl bg-gradient-to-tr from-blue-600 via-indigo-600 to-sky-500 flex items-center justify-center text-white shadow-md shadow-blue-500/20">
          <svg class="size-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
          </svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-lg font-bold tracking-tight text-slate-900">Google Flights Intelligence</h1>
            <span class="bg-blue-100 text-blue-700 text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider">v3.0 Python</span>
          </div>
          <p class="text-xs text-slate-500">Live 30-Day Cheapest Price Tracker & Supabase Synchronizer</p>
        </div>
      </div>

      <!-- Live Badges -->
      <div class="flex items-center gap-3">
        <!-- Scheduler Badge -->
        <div id="scheduler-badge" class="flex items-center gap-2 bg-indigo-50 border border-indigo-200 px-3 py-1.5 rounded-full text-xs font-semibold text-indigo-700 shadow-sm">
          <span class="size-2 rounded-full bg-indigo-500 animate-pulse"></span>
          <span>Daily 12:00 PM: Active</span>
        </div>

        <!-- Supabase Ping Badge -->
        <div id="db-status-pill" onclick="testSupabaseLive()" title="Click to test Supabase latency" class="flex items-center gap-2 bg-emerald-50 border border-emerald-300 px-3 py-1.5 rounded-full text-xs font-semibold text-emerald-800 cursor-pointer shadow-sm hover:bg-emerald-100 transition-colors">
          <span class="size-2 rounded-full bg-emerald-500"></span>
          <span>Supabase Connected</span>
        </div>
      </div>
    </div>
  </header>

  <!-- Main Content Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">

    <!-- KPI Summary Grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Global Routes</p>
          <p class="text-2xl font-bold text-slate-900 mt-1">250 Routes</p>
          <p class="text-xs text-slate-400 mt-0.5">Top International & Regional</p>
        </div>
        <div class="size-11 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center font-bold text-lg">🌍</div>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Tracked in Supabase</p>
          <p id="stat-stored-count" class="text-2xl font-bold text-slate-900 mt-1">--</p>
          <p class="text-xs text-emerald-600 font-medium mt-0.5">Live Upsert by Date</p>
        </div>
        <div class="size-11 rounded-xl bg-emerald-50 text-emerald-600 flex items-center justify-center font-bold text-lg">💾</div>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">30-Day Window</p>
          <p id="stat-window-range" class="text-2xl font-bold text-slate-900 mt-1">30 Days</p>
          <p class="text-xs text-slate-400 mt-0.5">Cheapest in 1-13d Return Window</p>
        </div>
        <div class="size-11 rounded-xl bg-indigo-50 text-indigo-600 flex items-center justify-center font-bold text-lg">📅</div>
      </div>

      <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Automated Trigger</p>
          <p class="text-2xl font-bold text-slate-900 mt-1">12:00 PM</p>
          <p id="stat-next-run" class="text-xs text-indigo-600 font-medium mt-0.5">Next: Today 12:00 PM</p>
        </div>
        <div class="size-11 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center font-bold text-lg">⏰</div>
      </div>
    </div>

    <!-- Scraper Control Panel & Live Progress -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

      <!-- Left Column: Route Picker & Controls -->
      <div class="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-5">
        <div class="flex items-center justify-between border-b border-slate-100 pb-4">
          <div>
            <h2 class="text-base font-bold text-slate-900 flex items-center gap-2">
              <span>✈️ Scrape Control Center</span>
            </h2>
            <p class="text-xs text-slate-500">Pick any of the 250 global routes or run the full automated batch.</p>
          </div>
          <span class="text-xs font-semibold px-2.5 py-1 rounded-md bg-blue-50 text-blue-700 border border-blue-200">Google Flights Engine</span>
        </div>

        <!-- 250 Routes Quick Picker -->
        <div>
          <label class="block text-xs font-bold text-slate-700 uppercase tracking-wider mb-1.5">Select Route from Top 250 Catalog</label>
          <select id="route-selector" onchange="onRouteSelected()" class="w-full bg-slate-50 border border-slate-300 rounded-xl px-3.5 py-2.5 text-sm font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500">
            <option value="">Loading routes...</option>
          </select>
        </div>

        <!-- Custom Origin / Destination Inputs & Start Date -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label class="block text-xs font-bold text-slate-700 mb-1">Origin (IATA)</label>
            <input type="text" id="input-origin" value="CAI" maxlength="4" class="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-sm font-bold text-slate-900 uppercase focus:ring-2 focus:ring-blue-500">
          </div>
          <div>
            <label class="block text-xs font-bold text-slate-700 mb-1">Destination (IATA)</label>
            <input type="text" id="input-destination" value="DXB" maxlength="4" class="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-sm font-bold text-slate-900 uppercase focus:ring-2 focus:ring-blue-500">
          </div>
          <div>
            <label class="block text-xs font-bold text-slate-700 mb-1">Start Date (Day 0)</label>
            <input type="date" id="input-start-date" class="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-sm font-semibold text-slate-900 focus:ring-2 focus:ring-blue-500">
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex flex-wrap items-center gap-3 pt-2">
          <!-- Scrape 30 Days -->
          <button id="btn-scrape-30" onclick="trigger30DaysScrape()" class="flex-1 min-w-[200px] bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-5 rounded-xl shadow-md shadow-blue-500/20 flex items-center justify-center gap-2 transition-all">
            <svg class="size-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
            <span>Scrape 30 Days (Round-Trip)</span>
          </button>

          <!-- Run Top 250 Global Batch -->
          <button id="btn-scrape-250" onclick="trigger250Batch()" class="bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 font-bold py-3 px-4 rounded-xl flex items-center gap-2 transition-all">
            <span>🌐 Run Top 250 Routes</span>
          </button>

          <!-- Trigger 12:00 PM Now -->
          <button id="btn-trigger-sched" onclick="triggerDailyNow()" class="bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 font-bold py-3 px-4 rounded-xl flex items-center gap-2 transition-all" title="Run the automated 12:00 PM routine right now">
            <span>⏰ Trigger 12:00 PM Job</span>
          </button>
        </div>

        <!-- Live Progress Bar -->
        <div id="progress-container" class="hidden space-y-2 pt-2 border-t border-slate-100">
          <div class="flex items-center justify-between text-xs font-bold text-slate-700">
            <span id="progress-status-text">Scraping day 1 of 30...</span>
            <div class="flex items-center gap-2">
              <span id="progress-percent-text" class="text-blue-600">3%</span>
              <button onclick="showProgress(false)" class="text-slate-400 hover:text-rose-600 font-bold text-xs px-1.5 py-0.5 rounded hover:bg-slate-100 transition-colors" title="Close progress bar">✕</button>
            </div>
          </div>
          <div class="w-full bg-slate-100 rounded-full h-3 overflow-hidden border border-slate-200">
            <div id="progress-bar-fill" class="bg-gradient-to-r from-blue-500 to-indigo-600 h-full rounded-full transition-all duration-300" style="width: 0%"></div>
          </div>
          <p id="progress-sub-text" class="text-[11px] text-slate-500">Checking Google Flights internal RPC endpoint...</p>
        </div>
      </div>

      <!-- Right Column: Live Logs & Terminal -->
      <div class="bg-slate-900 rounded-2xl border border-slate-800 shadow-sm p-5 flex flex-col justify-between text-slate-200">
        <div>
          <div class="flex items-center justify-between pb-3 border-b border-slate-800 mb-3">
            <div class="flex items-center gap-2">
              <span class="size-2.5 rounded-full bg-emerald-400 animate-ping"></span>
              <span class="text-xs font-mono font-bold uppercase tracking-wider text-slate-400">Live Scraping Console</span>
            </div>
            <button onclick="clearLogsUI()" class="text-[11px] text-slate-400 hover:text-white transition-colors">Clear</button>
          </div>
          <div id="console-logs" class="space-y-1.5 h-64 overflow-y-auto custom-scrollbar font-mono text-xs pr-1">
            <div class="text-slate-400">[System] Ready to scrape Google Flights. Choose a route and start!</div>
          </div>
        </div>

        <div class="pt-3 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
          <span>Engine: fast_flights RPC</span>
          <span class="text-emerald-400">● 0 ReCAPTCHA</span>
        </div>
      </div>

    </div>

    <!-- Supabase Flights Data Table -->
    <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      <!-- Table Header & Controls -->
      <div class="p-5 border-b border-slate-200 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div>
          <h3 class="text-base font-bold text-slate-900 flex items-center gap-2">
            <span>📊 Supabase Database - Cheapest Flights Verified</span>
          </h3>
          <p class="text-xs text-slate-500">Each row represents strictly the lowest verified fare for that day & route.</p>
        </div>

        <div class="flex items-center gap-3 w-full sm:w-auto">
          <!-- Search input -->
          <div class="relative flex-1 sm:w-64">
            <input type="text" id="table-search" oninput="filterFlightsTable()" placeholder="Search route, airline, date..." class="w-full bg-slate-50 border border-slate-300 rounded-xl pl-9 pr-3 py-2 text-xs font-medium text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500">
            <svg class="size-4 text-slate-400 absolute left-3 top-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
          </div>

          <!-- Refresh Button -->
          <button onclick="loadStoredFlights()" class="p-2 border border-slate-300 rounded-xl hover:bg-slate-50 text-slate-600 transition-colors" title="Reload flights from Supabase">
            <svg class="size-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          </button>

          <!-- Export CSV -->
          <button onclick="exportTableToCSV()" class="px-3 py-2 border border-slate-300 rounded-xl hover:bg-slate-50 text-xs font-bold text-slate-700 flex items-center gap-1.5 transition-colors">
            <span>Export CSV</span>
          </button>

          <!-- Clear Table -->
          <button onclick="clearDatabase()" class="px-3 py-2 border border-rose-200 text-rose-600 hover:bg-rose-50 rounded-xl text-xs font-bold transition-colors">
            <span>Clear</span>
          </button>
        </div>
      </div>

      <!-- Table Scrollable Area -->
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs text-slate-700">
          <thead class="bg-slate-50 text-[11px] font-bold text-slate-500 uppercase tracking-wider border-b border-slate-200">
            <tr>
              <th class="px-4 py-3">Departure Date</th>
              <th class="px-4 py-3">Return Date</th>
              <th class="px-3 py-3">Day of Week</th>
              <th class="px-3 py-3">Days Before</th>
              <th class="px-4 py-3">Route</th>
              <th class="px-4 py-3">Cheapest Airline</th>
              <th class="px-4 py-3">Flight & Schedule</th>
              <th class="px-4 py-3">Stops & Duration</th>
              <th class="px-3 py-3 text-center whitespace-nowrap">Self Transfer</th>
              <th class="px-4 py-3">Lowest Fare</th>
              <th class="px-3 py-3 whitespace-nowrap">Typical Range</th>
              <th class="px-3 py-3">Daily Delta</th>
              <th class="px-4 py-3 min-w-[220px]">Price Label & Google Insights</th>
              <th class="px-4 py-3">Action</th>
            </tr>
          </thead>
          <tbody id="flights-table-body" class="divide-y divide-slate-100 font-medium">
            <tr>
              <td colspan="8" class="text-center py-10 text-slate-400">Loading stored flights from Supabase...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>

  <!-- JavaScript Frontend Logic -->
  <script>
    let topRoutes = [];
    let isScraping = false;
    let pollInterval = null;

    // Set default date to today
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('input-start-date').value = today;

    // 1. Fetch Top 250 Routes and populate selector
    async function loadRoutes() {
      try {
        const res = await fetch('/api/routes');
        topRoutes = await res.json();
        const sel = document.getElementById('route-selector');
        sel.innerHTML = `<option value="">-- Choose a route (${topRoutes.length} available) --</option>`;
        topRoutes.forEach(r => {
          const opt = document.createElement('option');
          opt.value = `${r.origin}-${r.destination}`;
          opt.innerText = `${r.origin} ➔ ${r.destination} | ${r.originCity} to ${r.destinationCity} (${r.region || 'Global'})`;
          sel.appendChild(opt);
        });
      } catch (e) {
        console.error('Failed to load routes:', e);
      }
    }

    function onRouteSelected() {
      const val = document.getElementById('route-selector').value;
      if (!val) return;
      const [orig, dest] = val.split('-');
      document.getElementById('input-origin').value = orig;
      document.getElementById('input-destination').value = dest;
    }

    // 2. Fetch Stored Flights from Supabase
    async function loadStoredFlights() {
      const tbody = document.getElementById('flights-table-body');
      try {
        const res = await fetch('/api/flights?trip_type=round-trip&limit=250');
        let flights = await res.json();
        
        // Sort chronologically by date
        flights.sort((a, b) => {
          const dateA = a.departure_date || a.date || '';
          const dateB = b.departure_date || b.date || '';
          return dateA.localeCompare(dateB);
        });

        document.getElementById('stat-stored-count').innerText = flights.length;

        if (!flights.length) {
          tbody.innerHTML = `<tr><td colspan="8" class="text-center py-10 text-slate-400">No flights stored in Supabase yet. Click "Scrape 30 Days" above to start!</td></tr>`;
          return;
        }

        tbody.innerHTML = flights.map(f => {
          const formattedDate = f.departure_date || f.date || 'N/A';
          const price = Number(f.current_lowest_price).toFixed(0);
          const deepLink = f.source_url || `https://www.google.com/travel/flights?q=Flights%20to%20${f.destination_code}%20from%20${f.origin_code}%20on%20${formattedDate}`;

          // Calculate day_of_week and days_before
          let dayOfWeek = f.day_of_week;
          let daysBefore = f.days_before;
          if (!dayOfWeek || dayOfWeek === 'Unknown' || daysBefore === undefined || daysBefore === null) {
            try {
              const dObj = new Date(formattedDate);
              const todayObj = new Date();
              todayObj.setHours(0,0,0,0);
              dObj.setHours(0,0,0,0);
              daysBefore = Math.max(0, Math.round((dObj - todayObj) / (1000 * 60 * 60 * 24)));
              dayOfWeek = dObj.toLocaleDateString('en-US', { weekday: 'long' });
            } catch(e) {
              dayOfWeek = '--';
              daysBefore = '--';
            }
          }

          const deltaNum = Number(f.daily_delta || 0);
          let deltaBadge = `<span class="text-slate-400 font-mono text-xs">$0</span>`;
          if (deltaNum < 0) {
            deltaBadge = `<span class="inline-flex items-center text-emerald-600 font-bold font-mono text-xs">▼ $${Math.abs(deltaNum)}</span>`;
          } else if (deltaNum > 0) {
            deltaBadge = `<span class="inline-flex items-center text-rose-600 font-bold font-mono text-xs">▲ +$${deltaNum}</span>`;
          }

          // Google Flights Rating Badge (Cheapest, Low, Typical, High)
          const rLower = (f.price_rating || '').toLowerCase();
          let ratingBadge = '';
          if (rLower === 'cheapest') {
            ratingBadge = `<span class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[10px] font-black uppercase tracking-wider bg-emerald-600 text-white shadow-sm ring-1 ring-emerald-700/30" title="${f.price_labels || 'Cheapest fare in date range (Compared with other dates shown)'}"><span>✦</span> CHEAPEST</span>`;
          } else if (rLower === 'low' || rLower === 'great') {
            ratingBadge = `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-emerald-100 text-emerald-800 border border-emerald-300" title="${f.price_labels || 'Prices are currently low'}">LOW</span>`;
          } else if (rLower === 'high') {
            ratingBadge = `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider bg-rose-100 text-rose-700 border border-rose-200" title="${f.price_labels || 'Prices are currently high'}">HIGH</span>`;
          } else {
            ratingBadge = `<span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider bg-slate-100 text-slate-600 border border-slate-200" title="${f.price_labels || 'Prices are currently typical'}">TYPICAL</span>`;
          }

          const isCheapestRow = rLower === 'cheapest';

          return `
            <tr class="hover:bg-slate-50 transition-colors ${isCheapestRow ? 'bg-emerald-50/50' : ''}">
              <td class="px-4 py-3 font-semibold text-slate-900 whitespace-nowrap">
                <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg ${isCheapestRow ? 'bg-emerald-100 text-emerald-900 font-bold' : 'bg-slate-100 text-slate-800'}">
                  📅 ${formattedDate}
                </span>
              </td>
              <td class="px-4 py-3 font-semibold text-slate-900 whitespace-nowrap">
                <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-indigo-50 text-indigo-900 font-bold border border-indigo-200">
                  🔁 ${f.return_date || 'N/A'}
                </span>
              </td>
              <td class="px-3 py-3 whitespace-nowrap">
                <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-blue-50 text-blue-700 border border-blue-200">
                  ${dayOfWeek}
                </span>
              </td>
              <td class="px-3 py-3 whitespace-nowrap font-mono font-bold text-xs text-slate-600">
                +${daysBefore}d
              </td>
              <td class="px-4 py-3 whitespace-nowrap font-bold text-slate-900">
                <span class="text-blue-600">${f.origin_code}</span> ➔ <span class="text-indigo-600">${f.destination_code}</span>
                <span class="block text-[10px] text-slate-400 font-normal">${f.origin_city || ''} to ${f.destination_city || ''}</span>
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <div class="flex items-center gap-2">
                  <span class="font-bold text-slate-800">${f.airline}</span>
                </div>
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <span class="text-slate-800 font-semibold">${f.departure_time || '--:--'} - ${f.arrival_time || '--:--'}</span>
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <span class="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold ${f.stops === 'Nonstop' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-amber-50 text-amber-700 border border-amber-200'}">
                  ${f.stops || 'Direct'}
                </span>
                <span class="block text-[10px] text-slate-400 mt-0.5">${f.total_time || ''}</span>
              </td>
              <td class="px-3 py-3 text-center whitespace-nowrap">
                ${f.is_self_transfer ? `
                  <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-amber-100 text-amber-800 border border-amber-300" title="Self-transfer: Separate tickets booked together">
                    ⚠️ Yes
                  </span>
                ` : `
                  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500 border border-slate-200">
                    No
                  </span>
                `}
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <span class="text-sm font-extrabold ${isCheapestRow ? 'text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded-lg' : 'text-emerald-600'}">$${price}</span>
                <span class="text-[10px] text-slate-400 block">${f.currency || 'USD'}</span>
              </td>
              <td class="px-3 py-3 whitespace-nowrap">
                <span class="px-2 py-0.5 rounded text-[11px] font-mono font-bold bg-slate-100 text-slate-700 border border-slate-200" title="Google Flights typical range for this route">
                  ${f.typical_price_range || '--'}
                </span>
              </td>
              <td class="px-3 py-3 whitespace-nowrap">
                ${deltaBadge}
              </td>
              <td class="px-4 py-3 min-w-[220px]">
                <div class="flex items-center gap-1.5 flex-wrap">
                  ${ratingBadge}
                  <span class="text-[11px] font-bold text-slate-800">${f.price_labels || ''}</span>
                </div>
                ${f.prediction_advice ? `<span class="block text-[10px] text-slate-500 font-medium mt-1 leading-snug">💡 ${f.prediction_advice}</span>` : ''}
              </td>
              <td class="px-4 py-3 whitespace-nowrap">
                <a href="${deepLink}" target="_blank" class="inline-flex items-center gap-1 text-xs font-bold text-blue-600 hover:text-blue-800 hover:underline">
                  <span>Google Flights ↗</span>
                </a>
              </td>
            </tr>
          `;
        }).join('');

      } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" class="text-center py-6 text-rose-500">Error loading flights: ${e.message}</td></tr>`;
      }
    }

    // 3. Trigger 30-Day Scrape
    async function trigger30DaysScrape() {
      const origin = document.getElementById('input-origin').value.trim();
      const destination = document.getElementById('input-destination').value.trim();
      const startDate = document.getElementById('input-start-date').value;

      if (!origin || !destination) return alert('Please enter both origin and destination IATA codes.');

      const btn = document.getElementById('btn-scrape-30');
      btn.disabled = true;
      btn.innerText = 'Scraping 30 Days in Background...';

      showProgress(true);
      addLogMessage(`Starting 30-Day Google Flights scrape: ${origin} ➔ ${destination} from ${startDate}`, 'info');

      try {
        const res = await fetch('/api/scrape/google-30days', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ origin, destination, start_date: startDate })
        });
        const data = await res.json();
        if (data.success) {
          startPollingProgress();
        } else {
          addLogMessage(`Error: ${data.error}`, 'error');
          btn.disabled = false;
          btn.innerText = 'Scrape 30 Days (Cheapest Only)';
        }
      } catch (e) {
        addLogMessage(`Network error: ${e.message}`, 'error');
        btn.disabled = false;
        btn.innerText = 'Scrape 30 Days (Cheapest Only)';
      }
    }

    // 4. Trigger 250 Routes Batch
    async function trigger250Batch() {
      if (!confirm('Run 30-day scans across all 250 global routes in the background?')) return;
      const btn = document.getElementById('btn-scrape-250');
      btn.disabled = true;
      btn.innerText = 'Running Global 250...';
      showProgress(true);
      addLogMessage('Initiated Global 250 Routes Scrape...', 'info');

      try {
        const res = await fetch('/api/scrape/google-all-250', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.success) {
          startPollingProgress();
        } else {
          addLogMessage(`Error: ${data.error}`, 'error');
          btn.disabled = false;
        }
      } catch (e) {
        addLogMessage(`Error: ${e.message}`, 'error');
        btn.disabled = false;
      }
    }

    // 5. Trigger Daily 12:00 PM Job manually
    async function triggerDailyNow() {
      const btn = document.getElementById('btn-trigger-sched');
      btn.disabled = true;
      btn.innerText = 'Triggering 12:00 PM...';
      try {
        const res = await fetch('/api/scheduler/trigger', { method: 'POST' });
        const data = await res.json();
        addLogMessage(data.message, data.success ? 'success' : 'warn');
        if (data.success) {
          showProgress(true);
          startPollingProgress();
        }
      } catch (e) {
        addLogMessage(`Error: ${e.message}`, 'error');
      } finally {
        setTimeout(() => {
          btn.disabled = false;
          btn.innerText = '⏰ Trigger 12:00 PM Job';
        }, 3000);
      }
    }

    // Progress Polling
    function startPollingProgress() {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const res = await fetch('/api/scrape/progress');
          const data = await res.json();
          const p = data.interactive;
          const sched = data.scheduler;

          // Update Scheduler Badge
          if (sched && sched.is_active) {
            document.getElementById('stat-next-run').innerText = `Next: ${sched.next_run.split(' ')[1] || '12:00:00'}`;
          }

          const isRunning = p.is_running || (sched && sched.is_scraping);
          if (isRunning) {
            showProgress(true);
            let pct = 1;
            let statusText = '';
            let subText = '';

            if (p.is_running) {
              pct = Math.round((p.current_day / p.total_days) * 100) || 1;
              statusText = `Scraping ${p.route}: Day ${p.current_day} of ${p.total_days}`;
              if (p.latest_flight) {
                subText = `Found: $${p.latest_flight.current_lowest_price} on ${p.latest_flight.airline} (${p.latest_flight.stops})`;
              }
            } else if (sched && sched.is_scraping) {
              const rIdx = sched.progress.route_index || 1;
              const totR = sched.progress.total_routes || 250;
              const curD = sched.progress.current_day || 1;
              pct = Math.round((rIdx / totR) * 100) || 1;
              statusText = `12:00 PM Trigger: [${rIdx}/${totR}] ${sched.progress.current_route || 'Routes'}: Day ${curD}/30`;
              if (sched.progress.latest_cheapest) {
                subText = `Found: $${sched.progress.latest_cheapest.price} on ${sched.progress.latest_cheapest.airline}`;
              }
            }

            document.getElementById('progress-bar-fill').style.width = `${pct}%`;
            document.getElementById('progress-percent-text').innerText = `${pct}%`;
            document.getElementById('progress-status-text').innerText = statusText;
            if (subText) {
              document.getElementById('progress-sub-text').innerText = subText;
            }
          } else {
            // Finished
            if (pollInterval) {
              clearInterval(pollInterval);
              pollInterval = null;
            }
            // Animate to 100% completed
            document.getElementById('progress-bar-fill').style.width = '100%';
            document.getElementById('progress-percent-text').innerText = '100%';
            document.getElementById('progress-status-text').innerText = '✓ Completed! All dates scanned and saved.';
            document.getElementById('progress-bar-fill').className = 'bg-emerald-500 h-full rounded-full transition-all duration-300';
            
            document.getElementById('btn-scrape-30').disabled = false;
            document.getElementById('btn-scrape-30').innerText = 'Scrape 30 Days (Cheapest Only)';
            document.getElementById('btn-scrape-250').disabled = false;
            document.getElementById('btn-scrape-250').innerText = '🌐 Run Top 250 Routes';
            await loadStoredFlights();
            await fetchLogs();

            // Cleanly hide progress bar after 2.5 seconds
            setTimeout(() => {
              showProgress(false);
              document.getElementById('progress-bar-fill').className = 'bg-gradient-to-r from-blue-500 to-indigo-600 h-full rounded-full transition-all duration-300';
            }, 2500);
          }
        } catch (e) {
          console.error(e);
        }
      }, 1000);
    }

    function showProgress(visible) {
      document.getElementById('progress-container').className = visible ? 'space-y-2 pt-2 border-t border-slate-100' : 'hidden';
    }

    // Live Logs Display
    function addLogMessage(msg, type = 'info') {
      const logs = document.getElementById('console-logs');
      const time = new Date().toLocaleTimeString();
      const colors = {
        info: 'text-slate-300',
        success: 'text-emerald-400 font-semibold',
        warn: 'text-amber-400',
        error: 'text-rose-400 font-bold'
      };
      const row = document.createElement('div');
      row.className = colors[type] || 'text-slate-300';
      row.innerText = `[${time}] ${msg}`;
      logs.prepend(row);
    }

    async function fetchLogs() {
      try {
        const res = await fetch('/api/logs');
        const logs = await res.json();
        const container = document.getElementById('console-logs');
        container.innerHTML = logs.map(l => {
          const colors = { info: 'text-slate-300', success: 'text-emerald-400 font-semibold', warn: 'text-amber-400', error: 'text-rose-400 font-bold' };
          return `<div class="${colors[l.type] || 'text-slate-300'}">[${l.time}] ${l.message}</div>`;
        }).join('');
      } catch (e) {}
    }

    function clearLogsUI() {
      document.getElementById('console-logs').innerHTML = '<div class="text-slate-500">[Console cleared]</div>';
    }

    // Latency Ping
    async function testSupabaseLive() {
      const pill = document.getElementById('db-status-pill');
      pill.innerHTML = `<span class="size-2 rounded-full bg-amber-500 animate-spin"></span><span>Testing...</span>`;
      try {
        const res = await fetch('/api/supabase/test');
        const data = await res.json();
        if (data.success) {
          pill.innerHTML = `<span class="size-2 rounded-full bg-emerald-500"></span><span>Supabase ${data.latencyMs}ms</span>`;
          pill.className = "flex items-center gap-2 bg-emerald-50 border border-emerald-300 px-3 py-1.5 rounded-full text-xs font-semibold text-emerald-800 cursor-pointer shadow-sm";
          addLogMessage(`Supabase Ping: ${data.latencyMs}ms (${data.host})`, 'success');
        } else {
          pill.innerHTML = `<span class="size-2 rounded-full bg-rose-500"></span><span>Supabase Error</span>`;
        }
      } catch (e) {
        pill.innerHTML = `<span class="size-2 rounded-full bg-rose-500"></span><span>Supabase Offline</span>`;
      }
    }

    async function clearDatabase() {
      if (!confirm('Are you sure you want to clear stored flights in Supabase?')) return;
      try {
        const res = await fetch('/api/clear?trip_type=round-trip', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          addLogMessage('Cleared Supabase flights table.', 'warn');
          await loadStoredFlights();
        }
      } catch (e) {}
    }

    function filterFlightsTable() {
      const q = document.getElementById('table-search').value.toLowerCase();
      const rows = document.querySelectorAll('#flights-table-body tr');
      rows.forEach(tr => {
        tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
      });
    }

    function exportTableToCSV() {
      const rows = document.querySelectorAll('#flights-table-body tr');
      if (!rows.length) return alert('No flights to export');
      let csv = 'date,day_of_week,days_before,route,airline,schedule,stops,is_self_transfer,price_usd,typical_range,daily_delta,price_label_insights\\n';
      rows.forEach(tr => {
        const cols = tr.querySelectorAll('td');
        if (cols.length >= 12) {
          const date = cols[0].innerText.replace('📅', '').trim();
          const day = cols[1].innerText.trim();
          const daysBefore = cols[2].innerText.replace('+', '').replace('d', '').trim();
          const route = cols[3].innerText.replace(/\\n/g, ' ').trim();
          const airline = cols[4].innerText.trim();
          const sched = cols[5].innerText.trim();
          const stops = cols[6].innerText.replace(/\\n/g, ' ').trim();
          const isSelfTransfer = cols[7].innerText.includes('Yes') ? 'TRUE' : 'FALSE';
          const price = cols[8].innerText.replace('$', '').replace(/\\n/g, ' ').trim();
          const range = cols[9].innerText.trim();
          const delta = cols[10].innerText.replace(/[^0-9.-]/g, '').trim() || '0';
          const insights = cols[11].innerText.replace(/\\n/g, ' | ').replace(/\"/g, '""').trim();
          csv += `"${date}","${day}","${daysBefore}","${route}","${airline}","${sched}","${stops}","${isSelfTransfer}","${price}","${range}","${delta}","${insights}"\\n`;
        }
      });
      const blob = new Blob([csv], { type: 'text/csv' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `flight_price_prediction_dataset.csv`;
      a.click();
    }

    // Startup Initialization
    showProgress(false);
    loadRoutes();
    loadStoredFlights();
    fetchLogs();
    setInterval(fetchLogs, 4000);

    // Check if a scrape is actively running, otherwise keep progress hidden
    fetch('/api/scrape/progress')
      .then(r => r.json())
      .then(d => {
        if (d && d.interactive && d.interactive.is_running) {
          startPollingProgress();
        } else {
          showProgress(false);
        }
      })
      .catch(() => showProgress(false));
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    import webbrowser
    port = int(os.getenv("PORT", 3000))
    url = f"http://localhost:{port}"
    print(f"\n===========================================================")
    print(f"✈️  Google Flights Intelligence & Price Tracker (Python)")
    print(f"🔗 Supabase Status: Connected")
    print(f"⏰ Daily 12:00 PM Scheduler: Active")
    print(f"🚀 Opening Web Dashboard at: {url}")
    print(f"===========================================================\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)
