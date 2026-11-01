# Hopper Scraper & Supabase Chrome Extension

This extension connects your live Chrome browser directly to your local scraper server and Supabase database. Because it runs directly inside your verified browser session, it bypasses all Cloudflare and reCAPTCHA protections.

## How to Install (Takes 10 Seconds)
1. Open Google Chrome and go to `chrome://extensions/`.
2. Toggle on **Developer mode** in the top right corner.
3. Click **Load unpacked** in the top left.
4. Select the `extension` folder from this project:
   `e:\downloads\hopper-flight-price-scraper-&-intelligence-tracker\extension`
5. That's it!

## How to Use
1. Open any Hopper flight search page (e.g. `ADD -> DXB`, `YYZ -> LHR`, `DOH -> DXB`).
2. A floating widget will appear in the bottom-right corner showing:
   - Current lowest price (e.g. **$477**)
   - Real airline name (e.g. **Ethiopian Airlines**)
   - Prediction rating (e.g. **Great price**)
3. Click **"🚀 Sync to Supabase"** to instantly save the exact real data to your Supabase `one_way_flights` or `round_trip_flights` table.
4. Click **"+1 Day ❯"** to immediately jump to the next day and sync the next date.

