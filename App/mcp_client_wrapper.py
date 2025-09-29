"""MCP Client Wrapper for AMap (Gaode) Remote MCP Server (App package version).

This is the same implementation previously located at project root, now placed
inside the `App` package to keep application-related code grouped together.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, Optional

import requests
try:  # Lazy optional import (python-dotenv is in requirements)
	from dotenv import load_dotenv  # type: ignore
	load_dotenv(override=False)
except Exception:  # pragma: no cover
	pass

LOGGER = logging.getLogger(__name__)


class MCPClientError(Exception):
	"""Custom exception for MCP client failures."""


class MCPClientWrapper:
	"""High-level wrapper to call AMap tools via Remote MCP or direct REST."""

	MCP_BASE_URL = "https://mcp.amap.com/mcp"

	TOOL_NAME_MAP = {
		"get_geo_location": "maps_geocode",
		"get_regeocode": "maps_regeocode",
		"search_pois": "maps_text_search",
		"search_around": "maps_around_search",
		"get_poi_detail": "maps_detail_search",
		"get_weather": "maps_weather",
		"get_distance": "maps_distance",
		"get_walking_directions": "maps_direction_walking",
		"get_driving_directions": "maps_direction_driving",
		"get_transit_directions": "maps_direction_transit",
		"get_bicycling_directions": "maps_direction_bicycling",
		"get_ip_location": "maps_ip_location",
	}

	def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, enable_remote: bool = True):
		self.api_key = (
			api_key
			or os.getenv("AMAP_API_KEY")
			or os.getenv("AMAP_MAPS_API_KEY")
		)
		if not self.api_key:
			raise MCPClientError(
				"AMAP_API_KEY environment variable not set (also tried AMAP_MAPS_API_KEY) and no api_key provided"
			)
		self.timeout = timeout
		self.enable_remote = enable_remote

	def _remote_url(self) -> str:
		return f"{self.MCP_BASE_URL}?key={self.api_key}"

	def _call_remote_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
		if not self.enable_remote:
			return None
		payload = {"tool_name": tool_name, "arguments": arguments}
		try:
			resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=False)
			if resp.status_code != 200:
				LOGGER.debug("Remote MCP call non-200 (%s) -> fallback", resp.status_code)
				return None
			try:
				data = resp.json()
			except Exception:
				LOGGER.debug("Remote MCP response not JSON decodable -> fallback")
				return None
			return data
		except requests.RequestException as e:
			LOGGER.debug("Remote MCP request failed: %s", e)
			return None

	def _truncate_list_fields(self, data: Any) -> Any:
		"""Recursively truncate lists to max 10 items (including nested lists).

		Previous implementation only truncated top-level lists and did not
		descend into list elements (so nested list like forecasts[0]['casts']
		remained untrimmed). This version maps first, then truncates.
		"""
		if isinstance(data, list):
			processed = [self._truncate_list_fields(x) for x in data]
			return processed[:10] if len(processed) > 10 else processed
		if isinstance(data, dict):
			return {k: self._truncate_list_fields(v) for k, v in data.items()}
		return data

	def _call(self, local_method: str, rest_func, **params) -> Optional[Dict[str, Any]]:
		remote_tool = self.TOOL_NAME_MAP.get(local_method)
		result: Optional[Dict[str, Any]] = None
		if remote_tool:
			remote_raw = self._call_remote_mcp_tool(remote_tool, params)
			if remote_raw:
				result_candidate = remote_raw.get("data") if isinstance(remote_raw, dict) else None
				result = result_candidate or remote_raw
		if result is None:
			try:
				result = rest_func(**params)
			except Exception as e:
				LOGGER.error("REST fallback failed for %s: %s", local_method, e)
				return None
		return self._truncate_list_fields(result)

	def list_tools(self) -> Dict[str, Any]:
		return {"available_tools": list(self.TOOL_NAME_MAP.keys()), "remote_mapping": self.TOOL_NAME_MAP, "remote_enabled": self.enable_remote}

	def get_geo_location(self, address: str, city: str = "") -> Optional[Dict[str, Any]]:
		def rest(address: str, city: str = ""):
			url = "https://restapi.amap.com/v3/geocode/geo"
			params = {"address": address, "key": self.api_key}
			if city:
				params["city"] = city
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_geo_location", rest, address=address, city=city)

	def get_regeocode(self, location: str) -> Optional[Dict[str, Any]]:
		def rest(location: str):
			url = "https://restapi.amap.com/v3/geocode/regeo"
			params = {"location": location, "key": self.api_key, "extensions": "all"}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_regeocode", rest, location=location)

	def search_pois(self, keywords: str, city: str = "", page: int = 1, offset: int = 20) -> Optional[Dict[str, Any]]:
		def rest(keywords: str, city: str = "", page: int = 1, offset: int = 20):
			# Reverted to AMap Place v3 text search (v5 -> v3) per request
			url = "https://restapi.amap.com/v3/place/text"
			# v3 uses 'offset' & 'page' instead of 'page_size' & 'page_num'
			params = {"keywords": keywords, "key": self.api_key, "offset": offset, "page": page}
			if city:
				params["city"] = city
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("search_pois", rest, keywords=keywords, city=city, page=page, offset=offset)

	def search_around(self, location: str, keywords: Optional[str] = None, types: str = "", radius: int = 1000, sortrule: str = "distance", page: int = 1, offset: int = 20) -> Optional[Dict[str, Any]]:
		def rest(location: str, keywords: Optional[str] = None, types: str = "", radius: int = 1000, sortrule: str = "distance", page: int = 1, offset: int = 20):
			# Reverted to AMap Place v3 around search (v5 -> v3) per request
			url = "https://restapi.amap.com/v3/place/around"
			# v3 uses 'offset' & 'page'
			params = {"location": location, "radius": radius, "sortrule": sortrule, "key": self.api_key, "offset": offset, "page": page}
			if keywords:
				params["keywords"] = keywords
			if types:
				params["types"] = types
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("search_around", rest, location=location, keywords=keywords, types=types, radius=radius, sortrule=sortrule, page=page, offset=offset)

	def get_poi_detail(self, poi_id: str) -> Optional[Dict[str, Any]]:
		def rest(poi_id: str):
			url = "https://restapi.amap.com/v5/place/detail"
			params = {"ids": poi_id, "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_poi_detail", rest, poi_id=poi_id)

	def get_weather(self, city: str) -> Optional[Dict[str, Any]]:
		def rest(city: str):
			url = "https://restapi.amap.com/v3/weather/weatherInfo"
			params = {"city": city, "extensions": "all", "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_weather", rest, city=city)

	def get_distance(self, origins: str, destination: str, type: str = "1") -> Optional[Dict[str, Any]]:  # noqa: A003
		def rest(origins: str, destination: str, type: str = "1"):
			url = "https://restapi.amap.com/v3/distance"
			params = {"origins": origins, "destination": destination, "type": type, "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_distance", rest, origins=origins, destination=destination, type=type)

	def get_walking_directions(self, origin: str, destination: str) -> Optional[Dict[str, Any]]:
		def rest(origin: str, destination: str):
			url = "https://restapi.amap.com/v3/direction/walking"
			params = {"origin": origin, "destination": destination, "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_walking_directions", rest, origin=origin, destination=destination)

	def get_driving_directions(self, origin: str, destination: str) -> Optional[Dict[str, Any]]:
		def rest(origin: str, destination: str):
			url = "https://restapi.amap.com/v3/direction/driving"
			params = {"origin": origin, "destination": destination, "extensions": "all", "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_driving_directions", rest, origin=origin, destination=destination)

	def get_transit_directions(self, origin: str, destination: str, city: str, cityd: Optional[str] = None) -> Optional[Dict[str, Any]]:
		def rest(origin: str, destination: str, city: str, cityd: Optional[str] = None):
			url = "https://restapi.amap.com/v3/direction/transit/integrated"
			params = {"origin": origin, "destination": destination, "city": city, "key": self.api_key}
			if cityd:
				params["cityd"] = cityd
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_transit_directions", rest, origin=origin, destination=destination, city=city, cityd=cityd)

	def get_bicycling_directions(self, origin: str, destination: str) -> Optional[Dict[str, Any]]:
		def rest(origin: str, destination: str):
			url = "https://restapi.amap.com/v4/direction/bicycling"
			params = {"origin": origin, "destination": destination, "key": self.api_key}
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_bicycling_directions", rest, origin=origin, destination=destination)

	def get_ip_location(self, ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
		def rest(ip: Optional[str] = None):
			url = "https://restapi.amap.com/v3/ip"
			params = {"key": self.api_key}
			if ip:
				params["ip"] = ip
			r = requests.get(url, params=params, timeout=self.timeout)
			r.raise_for_status()
			return r.json()
		return self._call("get_ip_location", rest, ip=ip)

	def ping(self) -> bool:
		try:
			result = self.get_weather("110000")
			return bool(result)
		except Exception:
			return False


def _pretty(obj: Any) -> str:
	try:
		return json.dumps(obj, ensure_ascii=False, indent=2)
	except Exception:
		return str(obj)


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
	try:
		client = MCPClientWrapper()
	except MCPClientError as e:
		print(f"初始化失败: {e}\n请先设置环境变量 AMAP_API_KEY, 例如 (Windows PowerShell):\n  setx AMAP_API_KEY your_api_key_here")
		raise SystemExit(1)
	print("== 可用工具 ==")
	print(_pretty(client.list_tools()))
	tests = [
		("天气", lambda: client.get_weather("110000")),
		("地理编码", lambda: client.get_geo_location("海口市人民政府", "海口")),
	]
	for name, fn in tests:
		try:
			data = fn()
			print(f"-- {name} 成功 --\n{_pretty(data)[:500]}\n")
		except Exception as e:
			print(f"-- {name} 失败: {e}")

__all__ = ["MCPClientWrapper", "MCPClientError"]

