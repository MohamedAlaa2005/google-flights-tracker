import csv
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

DATE = "2026-11-20"
ORIGIN = "CAI"
DESTINATION = "FCO"

URL = (
    "https://www.google.com/travel/flights"
    f"?q=Flights%20to%20{DESTINATION}%20from%20{ORIGIN}%20on%20{DATE}%20oneway"
    "&curr=USD&hl=en&gl=eg"
)

PORT = 9222
PROFILE_DIR = r"E:\price_prediction\chrome_profile"
CLOSE_CHROME_AT_END = True   # يقفل Chrome في الآخر (لو الكود هو اللي فتحه)
HEADLESS = False           # True = في الخلفية بدون نافذة | False = نافذة صغّرة (minimized)

OUT_DIR = Path("data2")
OUT_DIR.mkdir(exist_ok=True)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

FIELDS = [
    "scraped_at", "days_before", "date", "origin", "destination",
    "dep_time", "arr_time", "arr_day_offset", "dep_datetime", "arr_datetime",
    "airline", "duration_min", "stops", "layover",
    "co2_kg", "emissions_pct", "price_usd",
]

TIME_RE = re.compile(r"^(\d{1,2}:\d{2}\s?[AP]M)\s?(\+\d)?$")
DUR_RE = re.compile(r"^(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?$")
ROUTE_RE = re.compile(r"^[A-Z]{3}\s?[–-]\s?[A-Z]{3}$")
STOPS_RE = re.compile(r"^(Nonstop|\d+\s+stops?)$", re.I)
CO2_RE = re.compile(r"([\d,]+)\s*kg CO2e")
EMIS_RE = re.compile(r"([+-]?\d+)%\s*emissions")
PRICE_RE = re.compile(r"^\$(\d[\d,]*)$")


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def chrome_ready():
    try:
        urllib.request.urlopen(f"http://localhost:{PORT}/json/version", timeout=1)
        return True
    except Exception:
        return False


def start_chrome():
    """يرجّع process أو None لو كان مفتوح أصلاً."""
    if chrome_ready():
        return None

    chrome_path = find_chrome()
    if not chrome_path:
        raise FileNotFoundError("مش لاقي chrome.exe، حط المسار في CHROME_CANDIDATES")

    args = [
        chrome_path,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1600,1000",
    ]
    if HEADLESS:
        args += [
            "--headless=new",
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ]
    else:
        args += ["--start-minimized"]

    proc = subprocess.Popen(args)

    for _ in range(30):  # استنى لحد 15 ثانية
        if chrome_ready():
            return proc
        time.sleep(0.5)

    raise TimeoutError("Chrome فتح بس البورت 9222 مش جاهز. اقفل أي نافذة Chrome بنفس البروفايل وجرّب تاني.")


def click_if_exists(locator, timeout=2000):
    try:
        locator.first.click(timeout=timeout)
        return True
    except Exception:
        return False


def to_24h(t):
    return datetime.strptime(t.replace(" ", "").upper(), "%I:%M%p").strftime("%H:%M")


def parse_flight(text):
    """يحوّل نص الرحلة لـ dict. يرجّع None لو النص مش بالشكل المتوقع (زي النسخة الملزقة)."""
    lines = [l.strip() for l in text.split("\n") if l.strip() and l.strip() not in ("–", "-")]

    # أول وقتين = الإقلاع والوصول
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
            # لو فيه توقف، السطر اللي بعده غالباً مدة/مطار الـ layover
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

    d0 = date.fromisoformat(DATE)
    dep24, arr24 = to_24h(dep), to_24h(arr)
    arr_date = d0 + timedelta(days=arr_offset)

    return {
        "dep_time": dep24,
        "arr_time": arr24,
        "arr_day_offset": arr_offset,
        "dep_datetime": f"{d0} {dep24}",
        "arr_datetime": f"{arr_date} {arr24}",
        "airline": airline,
        "duration_min": duration,
        "stops": stops,
        "layover": layover or "",
        "co2_kg": co2 if co2 is not None else "",
        "emissions_pct": emis if emis is not None else "",
        "price_usd": price,
    }


def read_rows(page, results):
    rows = page.locator("li").filter(has_text=re.compile(r"\$\d"))
    for i in range(rows.count()):
        try:
            text = rows.nth(i).inner_text(timeout=1000)
        except Exception:
            continue
        if "AM" not in text and "PM" not in text:
            continue
        flight = parse_flight(text)
        if not flight:
            continue
        key = (flight["dep_datetime"], flight["arr_datetime"],
               flight["airline"], flight["stops"], flight["price_usd"])
        results[key] = flight


def save_csv(results):
    path = OUT_DIR / f"{ORIGIN}_{DESTINATION}_{DATE}.csv"
    now = datetime.now()
    days_before = (date.fromisoformat(DATE) - now.date()).days
    scraped_at = now.strftime("%Y-%m-%d %H:%M:%S")

    rows = sorted(results.values(), key=lambda r: (r["price_usd"], r["dep_datetime"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({
                "scraped_at": scraped_at,
                "days_before": days_before,
                "date": DATE,
                "origin": ORIGIN,
                "destination": DESTINATION,
                **r,
            })
    return path, rows


def main():
    results = {}

    chrome_proc = start_chrome()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{PORT}")
        context = browser.contexts[0]
        page = context.new_page()
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.goto(URL, wait_until="domcontentloaded")

        for label in ("Accept all", "Reject all"):
            if click_if_exists(page.get_by_role("button", name=label), 1500):
                break

        # تبويب Cheapest
        page.get_by_role("tab", name=re.compile("Cheapest")).click(timeout=10000)
        page.wait_for_timeout(2500)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(750)
        read_rows(page, results)

        # View more flights
        for _ in range(3):
            if not click_if_exists(
                page.get_by_role("button", name=re.compile("more flights", re.I)), 2000
            ):
                break
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, 600)
            read_rows(page, results)

        page.screenshot(path="result.png", full_page=True)
        page.close()

    if CLOSE_CHROME_AT_END and chrome_proc is not None:
        chrome_proc.terminate()

    if not results:
        print("No flights found (شوف result.png)")
        return

    path, rows = save_csv(results)
    for r in rows:
        print(f"${r['price_usd']}  {r['dep_time']}->{r['arr_time']}  {r['airline']}  "
              f"{r['duration_min']}min  stops={r['stops']}")
    print("\nCHEAPEST:", f"${rows[0]['price_usd']}")
    print(f"saved {len(rows)} flights -> {path}")


if __name__ == "__main__":
    main()