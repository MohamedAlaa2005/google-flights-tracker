import os
import time
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional

import schedule
import google_flights_engine
from routes import TOP_250_ROUTES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SchedulerService")

class DailyFlightScheduler:
    def __init__(self, daily_time: str = "12:00"):
        self.daily_time = daily_time
        self.is_active = False
        self.is_scraping = False
        self.last_run: Optional[str] = None
        self.last_status: str = "Idle"
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.log_callback: Optional[Callable[[str, str], None]] = None
        self.progress_state = {
            "current_route": None,
            "route_index": 0,
            "total_routes": 0,
            "current_day": 0,
            "total_days": 30,
            "latest_cheapest": None,
            "total_saved": 0
        }

    def _log(self, msg: str, status_type: str = "info"):
        logger.info(msg)
        if self.log_callback:
            try:
                self.log_callback(msg, status_type)
            except Exception:
                pass

    def _job_wrapper(self):
        """Executed automatically every day at 12:00 PM or triggered manually."""
        if self.is_scraping:
            logger.warning("Scrape job is already running; skipping trigger.")
            return

        self.is_scraping = True
        self.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.last_status = "Running"
        self._log(f"==> Daily Flight Scraper Job Started at {self.last_run} <==", "info")

        try:
            total_saved = 0
            total_routes = len(TOP_250_ROUTES)
            self.progress_state["total_routes"] = total_routes

            for idx, route in enumerate(TOP_250_ROUTES):
                if self._stop_event.is_set():
                    self._log("Scheduler execution stopped.", "warn")
                    break

                orig = route["origin"]
                dest = route["destination"]
                route_key = f"{orig}-{dest}"
                self.progress_state["current_route"] = route_key
                self.progress_state["route_index"] = idx + 1
                self._log(f"[{idx+1}/{total_routes}] Scanning {route_key} across 30 days (3 parallel workers)...", "info")

                def _day_progress(cur_day, tot_days, flight_data):
                    self.progress_state["current_day"] = cur_day
                    if flight_data and "current_lowest_price" in flight_data:
                        self.progress_state["latest_cheapest"] = {
                            "route": route_key,
                            "date": flight_data["date"],
                            "price": flight_data["current_lowest_price"],
                            "airline": flight_data["airline"]
                        }
                        self._log(f"[{idx+1}/{total_routes}] {route_key} Day {cur_day}/30: ${flight_data['current_lowest_price']} ({flight_data.get('airline', 'Flight')})", "info")

                results = google_flights_engine.scrape_30_days_range(
                    origin=orig,
                    destination=dest,
                    save_to_supabase=True,
                    delay_sec=0.2,
                    on_progress=_day_progress
                )
                total_saved += len(results)
                self.progress_state["total_saved"] = total_saved

            self.last_status = f"Completed successfully. Saved/updated {total_saved} cheapest flights in Supabase."
            self._log(f"==> Daily Flight Scraper Completed. Total saved: {total_saved} <==", "success")

        except Exception as e:
            self.last_status = f"Error: {str(e)}"
            self._log(f"Scheduler job encountered an error: {e}", "error")
        finally:
            self.is_scraping = False

    def trigger_now(self) -> Dict[str, Any]:
        """Manually trigger the 12:00 PM routine in a background thread immediately."""
        if os.getenv("VERCEL"):
            return {
                "success": True,
                "message": "Vercel Serverless Mode: Live database active. Background Chrome scraping runs via local runner (python run_250_routes.py) or Docker."
            }

        if self.is_scraping:
            return {"success": False, "message": "Scraping is already actively running."}

        t = threading.Thread(target=self._job_wrapper, daemon=True, name="ManualDailyScrape")
        t.start()
        return {"success": True, "message": "Triggered Google Flights 30-day scan immediately in background."}

    def _loop(self):
        logger.info(f"DailyFlightScheduler worker started. Configured to run every day at {self.daily_time}.")
        schedule.every().day.at(self.daily_time).do(self._job_wrapper)

        while not self._stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)

    def start(self):
        if self.is_active:
            return
        self._stop_event.clear()
        self.is_active = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="DailyFlightScheduler")
        self.thread.start()
        logger.info("DailyFlightScheduler thread launched.")

    def stop(self):
        self._stop_event.set()
        self.is_active = False
        schedule.clear()
        logger.info("DailyFlightScheduler stopped.")

    def get_status(self) -> Dict[str, Any]:
        if os.getenv("VERCEL"):
            return {
                "is_active": True,
                "is_scraping": False,
                "scheduled_time": "Daily 12:00 PM (Cloud Synced)",
                "last_run": "Supabase Connected",
                "next_run": "Daily 12:00 PM",
                "status": "Vercel Serverless Dashboard (Database Live)",
                "progress": self.progress_state
            }

        # Calculate next scheduled run
        now = datetime.now()
        target_time = datetime.strptime(self.daily_time, "%H:%M").time()
        next_run = datetime.combine(now.date(), target_time)
        if next_run <= now:
            next_run += timedelta(days=1)

        return {
            "is_active": self.is_active,
            "is_scraping": self.is_scraping,
            "scheduled_time": f"{self.daily_time} (Daily 12:00 PM)",
            "last_run": self.last_run or "Not run yet",
            "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "status": self.last_status,
            "progress": self.progress_state
        }

# Global singleton scheduler instance
scheduler_instance = DailyFlightScheduler(daily_time="12:00")

