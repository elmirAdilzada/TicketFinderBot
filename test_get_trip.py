from browser.session import BrowserManager
from network.api_client import ADYApiClient

print("Starting browser...")
bm = BrowserManager()
bm.start()
client = ADYApiClient(bm)

print("Fetching trip for 2026-07-28...")
payload = {
    "from_station": 232,
    "to_station": 170,
    "trip_date": "2026-07-28",
    "is_exclusive": 0,
    "g_token": "",
}
raw = client._playwright_execute_fetch("https://ticket.ady.az/ticket-api/get_trip", payload)
print(raw)

bm.stop()
print("Done.")
