from browser.session import BrowserManager
from network.api_client import ADYApiClient
import json

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
        print("Raw response from get_traintrip:", raw)
        return raw

print("Starting browser...")
bm = BrowserManager()
bm.start()
client = ModifiedApiClient(bm)

print("Fetching trip dates (Baku -> Tbilisi)...")
dates = client.get_trip_dates(232, 170)
print(f"Got {len(dates) if dates else 0} dates.")

if dates:
    test_date_val = dates[0].trip_date_val
    print(f"Fetching traintrip for {test_date_val}...")
    trip = client.get_traintrip(232, 170, test_date_val)

bm.stop()
print("Done.")
