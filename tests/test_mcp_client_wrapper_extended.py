"""Extended tests for MCPClientWrapper referencing JS test script scenarios.

Coverage (no real network):
 - geocode (get_geo_location)
 - regeocode (get_regeocode)
 - driving, walking, transit, bicycling directions
 - text search (search_pois)
 - around search (search_around) + poi detail (get_poi_detail)
 - weather (already covered lightly elsewhere but repeated for flow cohesion)
 - distance (get_distance)

Excluded per request: ip_location.

Strategy: monkeypatch `requests.get` / `requests.post` with URL pattern matching.
All endpoints return minimal plausible AMap-like JSON shapes.
"""

from __future__ import annotations

import re
import pytest

from App.mcp_client_wrapper import MCPClientWrapper


@pytest.fixture
def mock_amap(monkeypatch):
    import requests

    # Reusable sample POIs
    pois = [
        {
            "id": f"POI_{i}",
            "name": f"餐厅{i}",
            "address": f"地址{i}",
            "location": f"110.30{i},20.05{i}",
            "distance": str(100 + i),
            "type": "餐饮服务",
            "tel": "123456789",
            "business_time": "08:00-22:00",
        }
        for i in range(5)
    ]

    def fake_get(url, params=None, timeout=10, **kwargs):  # noqa: D401
        params = params or {}
        # Geocode
        if "geocode/geo" in url:
            return _resp({
                "status": "1",
                "geocodes": [{"formatted_address": params.get("address", ""), "location": "110.312589,20.055793"}],
            })
        # Regeocode
        if "geocode/regeo" in url:
            return _resp({
                "status": "1",
                "regeocode": {"formatted_address": "海南省海口市某处"},
            })
        # Weather
        if "weatherInfo" in url:
            return _resp({
                "status": "1",
                "forecasts": [
                    {
                        "city": params.get("city", "海口"),
                        "reporttime": "2025-09-29 12:00:00",
                        "casts": [
                            {"date": "2025-09-29", "dayweather": "晴", "nightweather": "晴", "daytemp": "30", "nighttemp": "23"},
                            {"date": "2025-09-30", "dayweather": "多云", "nightweather": "多云", "daytemp": "29", "nighttemp": "22"},
                        ],
                    }
                ],
            })
        # Text search
        if re.search(r"/v5/place/text", url):
            return _resp({"status": "1", "pois": pois})
        # Around search
        if re.search(r"/v5/place/around", url):
            return _resp({"status": "1", "pois": pois})
        # POI detail
        if re.search(r"/v5/place/detail", url):
            ids = params.get("ids", "")
            poi = next((p for p in pois if p["id"] == ids), pois[0])
            return _resp({"status": "1", "pois": [poi]})
        # Distance
        if "/v3/distance" in url:
            return _resp({
                "status": "1",
                "results": [
                    {"distance": "2500", "duration": "600"},
                ],
            })
        # Driving
        if "/v3/direction/driving" in url:
            return _resp({"status": "1", "route": {"paths": [{"distance": "5000", "duration": "900"}]}})
        # Walking
        if "/v3/direction/walking" in url:
            return _resp({"status": "1", "route": {"paths": [{"distance": "1800", "duration": "1500"}]}})
        # Transit integrated
        if "/v3/direction/transit/integrated" in url:
            return _resp({"status": "1", "route": {"transits": [{"distance": "5200", "duration": "1800"}]}})
        # Bicycling (v4)
        if "/v4/direction/bicycling" in url:
            return _resp({"status": "1", "data": {"paths": [{"distance": "3500", "duration": "1000", "steps": [1, 2, 3]}]}})
        return _resp({"status": "1"})

    def fake_post(url, json=None, timeout=10, stream=False, **kwargs):  # noqa: D401
        # Simulate remote MCP unavailable so REST path is exercised consistently
        return _resp({"error": "unavailable"}, status=503)

    def _resp(payload, status=200):  # helper
        class R:
            def __init__(self, data, status_code):
                self._data = data
                self.status_code = status_code
            def json(self):
                return self._data
            def raise_for_status(self):
                if not (200 <= self.status_code < 300):
                    raise RuntimeError("HTTP error")
        return R(payload, status)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)

    return {}


@pytest.fixture
def wrapper(mock_amap):
    return MCPClientWrapper(api_key="dummy", enable_remote=True)


def test_geocode(wrapper):
    data = wrapper.get_geo_location("海南省海口市美兰区华彩·海口湾广场", "海口")
    assert data["geocodes"][0]["location"].startswith("110.312")


def test_regeocode(wrapper):
    data = wrapper.get_regeocode("110.312589,20.055793")
    assert "regeocode" in data


def test_driving(wrapper):
    data = wrapper.get_driving_directions("110.312589,20.055793", "110.330162,20.022889")
    assert data["route"]["paths"][0]["distance"] == "5000"


def test_walking(wrapper):
    data = wrapper.get_walking_directions("110.312589,20.055793", "110.330162,20.022889")
    assert data["route"]["paths"][0]["duration"] == "1500"


def test_transit(wrapper):
    data = wrapper.get_transit_directions("110.312589,20.055793", "110.330162,20.022889", "海口", "海口")
    assert data["route"]["transits"][0]["distance"] == "5200"


def test_bicycling(wrapper):
    data = wrapper.get_bicycling_directions("110.312589,20.055793", "110.330162,20.022889")
    assert data["data"]["paths"][0]["steps"] == [1, 2, 3]


def test_text_search(wrapper):
    data = wrapper.search_pois("咖啡", "海口")
    assert len(data["pois"]) == 5


def test_around_and_detail(wrapper):
    around = wrapper.search_around("110.312589,20.055793", keywords="餐厅", radius=1000)
    first_id = around["pois"][0]["id"]
    detail = wrapper.get_poi_detail(first_id)
    assert detail["pois"][0]["id"] == first_id


def test_weather(wrapper):
    data = wrapper.get_weather("海口")
    assert data["forecasts"][0]["casts"][0]["dayweather"] == "晴"


def test_distance(wrapper):
    data = wrapper.get_distance("110.312589,20.055793", "110.330162,20.022889")
    assert data["results"][0]["distance"] == "2500"
