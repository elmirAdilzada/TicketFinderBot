import os
import time
from playwright.sync_api import sync_playwright

def test_cf():
    with sync_playwright() as pw:
        user_data_dir = os.path.join(os.path.dirname(__file__), "test_profile")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            # channel="chrome", # Try without first to see if we can reproduce
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1920,1080",
            ],
            viewport={"width": 1920, "height": 1080},
        )
        
        try:
            from playwright_stealth import stealth_sync
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            stealth_sync(page)
        except Exception:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("Navigating...")
        page.goto("https://ticket.ady.az", wait_until="domcontentloaded")
        
        for _ in range(10):
            title = page.title()
            print("Title:", title)
            if "Just a moment" not in title and "Attention Required" not in title:
                print("Passed!")
                break
            
            # try to click turnstile
            try:
                iframe = page.frame_locator('iframe').first
                if iframe:
                    print("Found iframe")
                    # page.screenshot(path="cf_challenge.png")
            except Exception as e:
                print("Iframe error:", e)
                
            time.sleep(2)
        
        ctx.close()

if __name__ == "__main__":
    test_cf()
