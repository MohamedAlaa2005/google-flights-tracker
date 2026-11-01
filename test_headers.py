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

        def on_req(req):
            if 'availability' in req.url:
                print('REQUEST URL:', req.url)
                print('REQUEST HEADERS:', req.headers)

        def on_res(res):
            if 'availability' in res.url:
                print('RESPONSE STATUS:', res.status)
                print('RESPONSE HEADERS:', res.headers)
                try:
                    print('RESPONSE TEXT:', res.text()[:300])
                except Exception as e:
                    print('Error reading text:', e)

        page.on('request', on_req)
        page.on('response', on_res)

        page.goto(url, timeout=30000)
        time.sleep(8)
        browser.close()
finally:
    proc.terminate()

