"""Small read-only travel search adapters for configured providers."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from connectors.registry import ConnectorManager


def _request(url: str, *, headers: dict[str, str], data: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode() if data else None,
        headers={"User-Agent": "Sentience/1.0", **headers},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def search_flights(manager: ConnectorManager, origin: str, destination: str, date: str, adults: int = 1, currency: str = "USD", max_price: str = "") -> dict[str, Any]:
    if manager.connectors.get("flights", {}).get("status") != "connected":
        raise RuntimeError("Flights are not connected. Use connector > Add > Flights.")
    client_id = manager.get_secret("amadeus-client-id") or ""
    client_secret = manager.get_secret("amadeus-client-secret") or ""
    auth = _request("https://test.api.amadeus.com/v1/security/oauth2/token", headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
    })
    manager.save_secret("amadeus-access-token", auth["access_token"])
    def location_code(value: str) -> str:
        if len(value) == 3 and value.isalpha():
            return value.upper()
        lookup = _request(
            "https://test.api.amadeus.com/v1/reference-data/locations?" + urllib.parse.urlencode({"subType": "CITY,AIRPORT", "keyword": value, "page[limit]": "5"}),
            headers={"Authorization": f"Bearer {auth['access_token']}"},
        )
        locations = lookup.get("data", [])
        if not locations:
            raise RuntimeError(f"No Amadeus airport/city match found for {value!r}. Try an airport code.")
        exact = next((item for item in locations if str(item.get("name", "")).casefold() == value.casefold() or str(item.get("address", {}).get("cityName", "")).casefold() == value.casefold()), locations[0])
        return str(exact.get("iataCode", "")).upper()
    origin_code, destination_code = location_code(origin), location_code(destination)
    params = {"originLocationCode": origin_code, "destinationLocationCode": destination_code, "departureDate": date, "adults": str(max(1, min(adults, 9))), "currencyCode": currency.upper(), "max": "10"}
    if max_price:
        params["maxPrice"] = max_price
    data = _request("https://test.api.amadeus.com/v2/shopping/flight-offers?" + urllib.parse.urlencode(params), headers={"Authorization": f"Bearer {auth['access_token']}"})
    offers = []
    for offer in data.get("data", [])[:10]:
        itinerary = (offer.get("itineraries") or [{}])[0]
        segments = itinerary.get("segments", [])
        offers.append({
            "price": offer.get("price", {}).get("total"),
            "currency": offer.get("price", {}).get("currency", currency.upper()),
            "stops": max(0, len(segments) - 1),
            "segments": [{"carrier": segment.get("carrierCode"), "flight_number": f"{segment.get('carrierCode', '')}{segment.get('number', '')}", "from": segment.get("departure", {}).get("iataCode"), "departure": segment.get("departure", {}).get("at"), "to": segment.get("arrival", {}).get("iataCode"), "arrival": segment.get("arrival", {}).get("at")} for segment in segments],
        })
    return {"provider": "Amadeus test environment", "searched_route": f"{origin_code} to {destination_code}", "offers": offers, "booking_url": "https://www.google.com/travel/flights?q=" + urllib.parse.quote(f"Flights from {origin_code} to {destination_code} on {date}"), "note": "Sandbox search results can be incomplete and are not bookable offers. Verify current prices and availability with the airline or booking site."}


def search_trains(manager: ConnectorManager, origin: str, destination: str, date: str) -> dict[str, Any]:
    if manager.connectors.get("trains", {}).get("status") != "connected":
        raise RuntimeError("Trains are not connected. Use connector > Add > Trains.")
    key = manager.get_secret("railcore-api-key") or ""
    headers = {"X-RailCore-Key": key}
    def station_code(place: str) -> str:
        result = _request("https://ir.railcore.tech/v1/stations/search?" + urllib.parse.urlencode({"q": place, "limit": "5"}), headers=headers)
        stations = result.get("data", {}).get("results", result.get("results", []))
        if not stations:
            raise RuntimeError(f"No Indian RailCore station match found for {place!r}.")
        return str(stations[0].get("station_code", ""))
    origin_code, destination_code = station_code(origin), station_code(destination)
    params = urllib.parse.urlencode({"from": origin_code, "to": destination_code, "date": date})
    result = _request("https://ir.railcore.tech/v1/journeys/search?" + params, headers=headers)
    data = result.get("data", result)
    trains = data.get("trains", [])
    return {"provider": "RailCore", "coverage": "India", "origin_station": data.get("from_station_code", origin_code), "destination_station": data.get("to_station_code", destination_code), "date": date, "trains": trains[:10], "booking_url": "https://www.irctc.co.in/nget/train-search", "note": "RailCore provides independent read-only railway information, not ticket sales. Confirm availability and book on IRCTC."}


