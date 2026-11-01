import json
import os

ROUTES_FILE = os.path.join(os.path.dirname(__file__), "routes.json")

def load_top_250_routes():
    if os.path.exists(ROUTES_FILE):
        with open(ROUTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

TOP_250_ROUTES = load_top_250_routes()

METRO_CITY_CODES = {
    "MIL", "NYC", "LON", "PAR", "TYO", "WAS", "CHI", "BUE", "RIO", "SAO",
    "MOW", "BJS", "SEL", "SPK", "OSA", "YTO", "STO", "ROM", "BER", "IEV",
    "MMA", "BHZ", "REK", "SDF", "QLD"
}

def format_route_location(code: str, label: str = None) -> dict:
    code_upper = code.upper().strip()
    if code_upper in METRO_CITY_CODES:
        return {
            "label": label or code_upper,
            "cityCode": code_upper,
            "FlightsLocation": "City"
        }
    return {
        "label": label or code_upper,
        "iataCode": code_upper,
        "FlightsLocation": "Airport"
    }

def get_route(origin: str, destination: str):
    origin = origin.upper().strip()
    destination = destination.upper().strip()
    for r in TOP_250_ROUTES:
        if (r["origin"] == origin and r["destination"] == destination) or r["id"] == f"{origin}-{destination}":
            return r
    return {
        "id": f"{origin}-{destination}",
        "origin": origin,
        "originCity": origin,
        "originCountry": "International",
        "destination": destination,
        "destinationCity": destination,
        "destinationCountry": "International",
        "region": "Global",
        "rank": 999,
        "estimatedBasePrice": 250
    }

