from browser.session import BrowserManager
from network.api_client import ADYApiClient
from config.dynamic_settings import set_setting
import time

set_setting("PROXY_CONFIG", "none") # Force disable proxy

print("Starting browser...")
bm = BrowserManager()
bm.start()
client = ADYApiClient(bm)

print("Fetching trip dates (Baku -> Tbilisi)...")
dates = client.get_trip_dates(232, 170)
print(f"Got {len(dates) if dates else 0} dates.")

if dates:
    test_date_val = dates[0].trip_date_val
    print(f"Fetching traintrip for {test_date_val}...")
    try:
        trip = client.get_traintrip(232, 170, test_date_val)
        print("Success! Free seats:", trip.total_free_seats if trip else "No trip")
    except Exception as e:
        print("Failed:", e)

bm.stop()
print("Done.")
