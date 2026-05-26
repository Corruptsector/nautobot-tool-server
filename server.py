#!/usr/bin/env python3
"""Nautobot tool server — exposes Nautobot inventory as a simple HTTP API."""

import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "http://nautobot:8080")
NAUTOBOT_TOKEN = os.environ.get("NAUTOBOT_TOKEN", "0123456789abcdef0123456789abcdef01234567")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
PORT = int(os.environ.get("PORT", "8000"))

NB_HEADERS = {
    "Authorization": f"Token {NAUTOBOT_TOKEN}",
    "Accept": "application/json",
}


def prom_query(promql):
    r = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def nb_get(endpoint, params=None):
    r = requests.get(f"{NAUTOBOT_URL}/api/{endpoint}", headers=NB_HEADERS, params=params, timeout=15)
    if r.status_code == 400:
        raise ValueError(f"Invalid filter: {r.json()}")
    r.raise_for_status()
    return r.json()


def fmt_device(d):
    return {
        "name": d["name"],
        "status": d["status"]["name"] if d.get("status") else None,
        "location": d["location"]["display"] if d.get("location") else None,
        "tenant": d["tenant"]["name"] if d.get("tenant") else None,
        "role": d["role"]["name"] if d.get("role") else None,
        "device_type": d["device_type"]["display"] if d.get("device_type") else None,
        "platform": d["platform"]["name"] if d.get("platform") else None,
        "primary_ip": d["primary_ip4"]["address"] if d.get("primary_ip4") else None,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            if path == "/health":
                self.respond({"status": "ok"})

            elif path == "/devices":
                params = {"limit": int(qs.get("limit", 100)), "depth": 1}
                for k in ("tenant", "role", "name"):
                    if qs.get(k):
                        params[k] = qs[k]
                if qs.get("location"):
                    # Resolve partial location name → IDs via q= fuzzy search
                    loc_data = nb_get("dcim/locations/", {"q": qs["location"], "limit": 50})
                    loc_ids = [loc["id"] for loc in loc_data.get("results", [])]
                    if loc_ids:
                        params["location"] = loc_ids  # requests sends repeated params
                    else:
                        self.respond({"count": 0, "devices": [], "note": f"No locations matched '{qs['location']}'"})
                        return
                data = nb_get("dcim/devices/", params)
                self.respond({
                    "count": data["count"],
                    "devices": [fmt_device(d) for d in data["results"]],
                })

            elif path.startswith("/devices/"):
                name = urllib.parse.unquote(path[len("/devices/"):])
                data = nb_get("dcim/devices/", {"name": name, "depth": 1})
                if not data["results"]:
                    self.respond({"error": f"Device '{name}' not found"}, 404)
                else:
                    self.respond(fmt_device(data["results"][0]))

            elif path == "/tenants":
                data = nb_get("tenancy/tenants/")
                self.respond({
                    "count": data["count"],
                    "tenants": [{"name": t["name"]} for t in data["results"]],
                })

            elif path == "/locations":
                params = {"depth": 1}
                if qs.get("type"):
                    types = nb_get("dcim/location-types/", {"name": qs["type"]})
                    if types["results"]:
                        params["location_type"] = types["results"][0]["id"]
                data = nb_get("dcim/locations/", params)
                self.respond({
                    "count": data["count"],
                    "locations": [{
                        "name": loc["name"],
                        "type": loc["location_type"]["name"] if loc.get("location_type") else None,
                        "parent": loc["parent"]["name"] if loc.get("parent") else None,
                    } for loc in data["results"]],
                })

            elif path == "/summary":
                devices = nb_get("dcim/devices/", {"limit": 1})
                tenants = nb_get("tenancy/tenants/", {"limit": 1})
                locations = nb_get("dcim/locations/", {"limit": 1})
                self.respond({
                    "total_devices": devices["count"],
                    "total_tenants": tenants["count"],
                    "total_locations": locations["count"],
                })

            elif path.startswith("/metrics/device/"):
                device_name = urllib.parse.unquote(path[len("/metrics/device/"):])
                metrics = {}
                scalar_queries = {
                    "cpu_percent":      f'device_cpu_percent{{device="{device_name}"}}',
                    "memory_percent":   f'device_memory_percent{{device="{device_name}"}}',
                    "up":               f'device_up{{device="{device_name}"}}',
                    "uptime_seconds":   f'device_uptime_seconds{{device="{device_name}"}}',
                    "bgp_peers":        f'device_bgp_peers{{device="{device_name}"}}',
                }
                for key, q in scalar_queries.items():
                    res = prom_query(q)
                    result = res.get("data", {}).get("result", [])
                    if result:
                        val = result[0]["value"][1]
                        metrics[key] = float(val)
                        # carry through labels for context
                        if key == "up":
                            lbl = result[0].get("metric", {})
                            metrics["tenant"]   = lbl.get("tenant", "")
                            metrics["location"] = lbl.get("location", "")
                            metrics["role"]     = lbl.get("role", "")

                # Interface traffic
                for direction in ("rx", "tx"):
                    q = f'device_interface_{direction}_bps{{device="{device_name}"}}'
                    res = prom_query(q)
                    ifaces = {}
                    for r2 in res.get("data", {}).get("result", []):
                        iface = r2["metric"].get("interface", "?")
                        bps = float(r2["value"][1])
                        ifaces[iface] = round(bps / 1e6, 2)  # Mbps
                    if ifaces:
                        metrics[f"interfaces_{direction}_mbps"] = ifaces

                if not metrics:
                    self.respond({"error": f"No metrics found for device '{device_name}'"}, 404)
                else:
                    self.respond({"device": device_name, "metrics": metrics})

            else:
                self.respond({
                    "error": "Not found",
                    "endpoints": ["/health", "/devices", "/devices/<name>", "/tenants", "/locations", "/summary", "/metrics/device/<name>"],
                }, 404)

        except Exception as e:
            self.respond({"error": str(e)}, 500)

    def respond(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[tool-server] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Nautobot tool server on :{PORT}  (Nautobot: {NAUTOBOT_URL})")
    server.serve_forever()
