from browser.session import BrowserManager
import time
import threading

print("Starting browser...")
bm = BrowserManager()
bm.start()

print("Navigating to search page...")
result_holder = {}
done = threading.Event()
bm._eval_queue.put(("eval", "window.location.href = 'https://ticket.ady.az/az/search/baki-sern-tbilisi/28-07-2026';", result_holder, done))
done.wait()

print("Waiting for page load...")
time.sleep(10)

js_code = """
async () => {
    return document.body.innerText;
}
"""
print("Extracting text...")
text = bm.evaluate(js_code)
print(text[:2000])

bm.stop()
print("Done.")
