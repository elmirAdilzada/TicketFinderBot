from browser.session import BrowserManager
from network.api_client import ADYApiClient

class ModifiedApiClient(ADYApiClient):
    def _playwright_execute_fetch(self, url: str, payload: dict):
        import json
        js_code = f'''
        async () => {{
            try {{
                const p = {json.dumps(payload)};
                p.g_token = ""; // FORCE EMPTY TOKEN

                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 15000);

                const r = await fetch('{url}', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }},
                    body: JSON.stringify(p),
                    signal: controller.signal
                }});
                
                clearTimeout(timeoutId);

                const j = await r.json();
                return JSON.stringify({{status: r.status, data: j}});

            }} catch(e) {{
                return JSON.stringify({{status: 500, error: e.toString()}});
            }}
        }}
        '''
        try:
            val = self._browser.evaluate(js_code, timeout=30.0)
            if val:
                parsed = json.loads(val)
                if parsed.get("status") == 200 and "data" in parsed:
                    return parsed["data"]
                print("Error:", parsed)
            return None
        except Exception as exc:
            print("Exception:", exc)
            return None

print("Starting browser...")
bm = BrowserManager()
bm.start()
client = ModifiedApiClient(bm)

print("Fetching trip dates with EMPTY token (Baku -> Tbilisi)...")
dates = client.get_trip_dates(232, 170)
print(f"Got {len(dates) if dates else 0} dates.")
bm.stop()
print("Done.")
