import subprocess
import time
from playwright.sync_api import sync_playwright

url = 'https://hopper.com/en-US/air/shop?tripType=one-way&departureDate=2026-11-11&origin=DOH&originType=Airport&destination=DXB&destinationType=Airport&adults=2&step=outbound'

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

        page.goto(url, timeout=30000)
        time.sleep(3)

        print('Looking for Update button...')
        update_btn = page.locator('button:has-text("Update")')
        if update_btn.is_visible():
            print('Clicking Update button...')
            update_btn.click()
            time.sleep(5)

        page.screenshot(path='after_update.png')
        content = page.content()
        print('Has no flights message:', 'There are no flights available' in content)
        cards = page.locator('text=/$[0-9]+/').all_inner_texts()
        print('Prices on screen:', cards[:10])
        browser.close()
finally:
    proc.terminate()
