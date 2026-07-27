from browser.session import BrowserManager

print("Starting browser...")
bm = BrowserManager()
bm.start()

js_code = """
async () => {
    try {
        const html = document.documentElement.innerHTML;
        const matches = [...html.matchAll(/(6L[a-zA-Z0-9_-]{38})/g)];
        return matches.map(m => m[1]);
    } catch(e) {
        return e.toString();
    }
}
"""
sitekeys = bm.evaluate(js_code)
print(f"Found sitekeys in HTML: {sitekeys}")

bm.stop()
print("Done.")
