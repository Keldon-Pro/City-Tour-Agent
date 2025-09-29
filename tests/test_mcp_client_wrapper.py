"""Tests for MCPClientWrapper.

Design:
 - Unit-style tests avoid real network by monkeypatching `requests`.
 - Focus on: tool listing, weather call fallback, list truncation logic, error on missing key.
 - Integration tests (real API) are optional and only run when REAL_AMAP_API_KEY is set.
"""

from __future__ import annotations

import os
import types
import pytest

from App.mcp_client_wrapper import MCPClientWrapper, MCPClientError


class DummyResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):  # pragma: no cover - trivial
        return self._json

    def raise_for_status(self):  # pragma: no cover - trivial
        if not (200 <= self.status_code < 300):
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def monkeypatched_requests(monkeypatch):
    import requests  # local import to patch the same module the wrapper uses

    def fake_get(url, params=None, timeout=10, **kwargs):  # noqa: D401
        # Weather endpoint simulation
        if "weatherInfo" in url:
            casts = [
                {"date": f"2025-09-{i:02d}", "dayweather": "晴", "nightweather": "晴"}
                for i in range(1, 15)  # 14 entries to test truncation -> should become 10
            ]
            return DummyResponse(
                {
                    "status": "1",
                    "count": "1",
                    "info": "OK",
                    "infocode": "10000",
                    "forecasts": [
                        {
                            "city": params.get("city", "测试城市"),
                            "casts": casts,
                        }
                    ],
                }
            )
        # Geocode endpoint minimal stub
        if "geocode/geo" in url:
            return DummyResponse({"status": "1", "geocodes": [{"formatted_address": params.get("address", "")}]})
        # Generic fallback
        return DummyResponse({"status": "1"})

    def fake_post(url, json=None, timeout=10, stream=False, **kwargs):  # noqa: D401
        # Force remote MCP to "fail" so fallback path is exercised
        return DummyResponse({"error": "not available"}, status_code=503)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return requests


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("AMAP_API_KEY", raising=False)
    monkeypatch.delenv("AMAP_MAPS_API_KEY", raising=False)
    with pytest.raises(MCPClientError):
        MCPClientWrapper()


def test_list_tools(monkeypatched_requests):
    wrapper = MCPClientWrapper(api_key="dummy", enable_remote=False)
    tools = wrapper.list_tools()
    assert "get_weather" in tools["available_tools"]
    assert tools["remote_enabled"] is False or tools["remote_enabled"] is True  # existence check


def test_weather_truncation(monkeypatched_requests):
    wrapper = MCPClientWrapper(api_key="dummy", enable_remote=True)  # remote will 503 -> fallback
    data = wrapper.get_weather("海口")
    assert data["forecasts"], "Forecasts should be present"
    casts = data["forecasts"][0]["casts"]
    assert len(casts) == 10, "List should be truncated to first 10 entries"


@pytest.mark.integration
def test_real_weather_if_key_present():
    real_key = os.getenv("AMAP_API_KEY")
    if not real_key:
        pytest.skip("No real AMAP_API_KEY set")
    wrapper = MCPClientWrapper(enable_remote=False)  # use REST directly
    data = wrapper.get_weather("110000")  # Beijing adcode
    assert data.get("status") == "1"
