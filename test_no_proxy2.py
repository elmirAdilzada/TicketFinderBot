from browser.session import BrowserManager
from network.api_client import ADYApiClient
from config.dynamic_settings import set_setting
import time

set_setting("PROXY_CONFIG", "none") # Force disable proxy

class ModifiedApiClient(ADYApiClient):
    def get_traintrip(self, from_station: int, to_station: int, trip_date: str):
        payload = {
            "from_station": from_station,
            "to_station": to_station,
            "trip_date": trip_date,
            "check": False,
            "is_exclusive": 0,
            "g_token": "",
        }
        raw = self._playwright_execute_fetch("https://ticket.ady.az/ticket-api/get_traintrip", payload)
        print("RAW RESPONSE:", raw)
        return raw

print("Starting browser...")
bm = BrowserManager()
bm.start()
client = ModifiedApiClient(bm)

print("Fetching trip dates (Baku -> Tbilisi)...")
dates = client.get_trip_dates(232, 170)
print(f"Got {len(dates) if dates else 0} dates.")

if dates:
    for i in range(min(5, len(dates))):
        test_date_val = dates[i].trip_date_val
        print(f"Fetching traintrip for {test_date_val}...")
        try:
            trip = client.get_traintrip(232, 170, test_date_val)
            print("Success!", trip)
        except Exception as e:
            print("Failed:", e)
        time.sleep(2)

bm.stop()
print("Done.")
