import subprocess
import time
from playwright.sync_api import sync_playwright

url = 'https://hopper.com/en-US/air/shop?tripType=one-way&departureDate=2026-11-05&origin=ADD&originType=Airport&destination=DXB&destinationType=Airport&adults=2&step=outbound'

cmd = [
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    '--remote-debugging-port=9222',
    '--user-data-dir=C:\\Users\\ALEX STORE\\.hopper_chrome_profile',
    '--no-first-run',
    '--no-default-browser-check',
    '--start-maximized'
]
proc = subprocess.Popen(cmd)
time.sleep(2)

try:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://localhost:9222')
        context = browser.contexts[0]
        page = context.new_page()

        page.goto('https://hopper.com', timeout=30000)
        time.sleep(2)

        # Generate token and explicitly set cookie
        page.evaluate("""
            new Promise((resolve) => {
                if (window.grecaptcha && window.grecaptcha.enterprise) {
                    window.grecaptcha.enterprise.ready(async () => {
                        const token = await window.grecaptcha.enterprise.execute('6Ld6N2ApAAAAAMmHFsSXpV4sdp1CobljCw70z207', {action: 'session'});
                        document.cookie = `recaptcha-ca-t=${token}; path=/; domain=.hopper.com; secure`;
                        resolve(token);
                    });
                } else {
                    resolve(null);
                }
            })
        """)
        time.sleep(1)

        cookies = context.cookies()
        rc_cookie = [c for c in cookies if c['name'] == 'recaptcha-ca-t']
        print("recaptcha-ca-t cookie set:", bool(rc_cookie))

        captured = []
        def on_res(r):
            if 'availability' in r.url:
                print(f"[{r.status}] {r.url}")
                if r.status == 200:
                    data = r.json()
                    fares = data.get('flights', {}).get('fares', [])
                    print(f"SUCCESS! Total live fares found: {len(fares)}")
                    if fares:
                        fares.sort(key=lambda f: f.get('priceBreakdown', {}).get('total', 999999))
                        print(f"⭐ Lowest Price: ${fares[0]['priceBreakdown']['total']}")
                        captured.append(fares[0])

        page.on('response', on_res)
        page.goto(url, timeout=30000)
        time.sleep(6)
        print("Done. Total captured:", len(captured))
        browser.close()
finally:
    proc.terminate()
