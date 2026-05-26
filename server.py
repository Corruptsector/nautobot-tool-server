#!/usr/bin/env python3
"""
Nautobot Tool Server
====================
A lightweight HTTP API that bridges Nautobot (network inventory) and Prometheus
(device metrics) into a single JSON interface. Designed to be registered as a
tool inside Open WebUI so an LLM can query live network inventory and telemetry.

Configuration (environment variables):
  NAUTOBOT_URL    Base URL of the Nautobot instance   (default: http://nautobot:8080)
  NAUTOBOT_TOKEN  Nautobot API token for auth          (default: dev token)
  PROMETHEUS_URL  Base URL of the Prometheus instance  (default: http://prometheus:9090)
  PORT            Port this server listens on          (default: 8000)

Endpoints:
  GET  /health                      Liveness check
  GET  /devices                     List devices, filters: location, tenant, role, name
  GET  /devices/<name>              Single device detail by exact hostname
  GET  /tenants                     List all tenants
  GET  /locations                   List all locations, optional filter: type
  GET  /summary                     Total counts of devices, tenants, locations
  GET  /metrics/device/<name>       Live Prometheus metrics for a device by hostname
  GET  /device-creation-context     All data needed to create a device (tenants, locations,
                                    roles, device types, prefixes, namespaces, workflow guide)
                                    Optional filter: ?tenant=<name>
  POST /devices                     Create a device with full workflow (resolves names,
                                    creates device type / prefix / IP if needed, assigns
                                    management IP and sets primary_ip4)
"""

import ipaddress
import json
import os
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

NAUTOBOT_URL   = os.environ.get("NAUTOBOT_URL",   "http://nautobot:8080")
NAUTOBOT_TOKEN = os.environ.get("NAUTOBOT_TOKEN", "0123456789abcdef0123456789abcdef01234567")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
PORT           = int(os.environ.get("PORT", "8000"))

NB_HEADERS = {
    "Authorization": f"Token {NAUTOBOT_TOKEN}",
    "Accept": "application/json",
}


def prom_query(promql):
    """Execute an instant PromQL query and return the full Prometheus API response."""
    r = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def nb_get(endpoint, params=None):
    """
    GET a Nautobot REST API endpoint.
    Raises ValueError on 400 (invalid filter) so callers get a readable message
    rather than a raw Nautobot validation error.
    """
    r = requests.get(f"{NAUTOBOT_URL}/api/{endpoint}", headers=NB_HEADERS, params=params, timeout=15)
    if r.status_code == 400:
        raise ValueError(f"Invalid filter: {r.json()}")
    r.raise_for_status()
    return r.json()


def nb_post(endpoint, body):
    """POST to a Nautobot REST API endpoint and return the created object."""
    r = requests.post(
        f"{NAUTOBOT_URL}/api/{endpoint}",
        headers={**NB_HEADERS, "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def nb_patch(endpoint, body):
    """PATCH a Nautobot REST API endpoint and return the updated object."""
    r = requests.patch(
        f"{NAUTOBOT_URL}/api/{endpoint}",
        headers={**NB_HEADERS, "Content-Type": "application/json"},
        json=body,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fmt_device(d):
    """
    Flatten a Nautobot device object (depth=1) into a compact dict.
    Nautobot returns deeply nested objects; this pulls the readable fields up
    to the top level so the LLM gets clean, concise data.
    """
    return {
        "name":        d["name"],
        "status":      d["status"]["name"]        if d.get("status")      else None,
        "location":    d["location"]["display"]   if d.get("location")    else None,
        "tenant":      d["tenant"]["name"]         if d.get("tenant")      else None,
        "role":        d["role"]["name"]           if d.get("role")        else None,
        "device_type": d["device_type"]["display"] if d.get("device_type") else None,
        "platform":    d["platform"]["name"]       if d.get("platform")    else None,
        "primary_ip":  d["primary_ip4"]["address"] if d.get("primary_ip4") else None,
    }


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")
        qs     = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            if path == "/health":
                # Simple liveness probe used by Docker healthcheck
                self.respond({"status": "ok"})

            elif path == "/devices":
                # Require at least one filter — scanning all devices is too expensive
                # at scale (50k+ devices). The caller must narrow by location, tenant,
                # role, or name before we touch the devices table.
                if not any(qs.get(k) for k in ("location", "tenant", "role", "name")):
                    self.respond({
                        "error": "At least one filter is required (location, tenant, role, or name). "
                                 "Call /tenants first to find a tenant, then /locations?tenant=<name> "
                                 "to find a location, then /devices?tenant=<name>&location=<name>."
                    }, 400)
                    return

                limit  = int(qs.get("limit", 50))
                offset = int(qs.get("offset", 0))

                # depth=1 tells Nautobot to expand nested objects (status, role, etc.)
                # so fmt_device() can read their names without extra API calls.
                params = {"limit": limit, "offset": offset, "depth": 1}
                for k in ("tenant", "role", "name"):
                    if qs.get(k):
                        params[k] = qs[k]

                if qs.get("location"):
                    # Resolve partial location name → IDs via q= fuzzy search.
                    # Locations don't carry a tenant field, so we can't narrow here —
                    # the tenant filter on the devices query below handles that instead.
                    # Passing all matching location IDs + tenant to Nautobot's devices
                    # API is still an efficient indexed lookup.
                    loc_data = nb_get("dcim/locations/", {"q": qs["location"], "limit": 50})
                    loc_ids  = [loc["id"] for loc in loc_data.get("results", [])]
                    if loc_ids:
                        params["location"] = loc_ids
                    else:
                        self.respond({"count": 0, "devices": [], "note": f"No locations matched '{qs['location']}'"})
                        return

                data     = nb_get("dcim/devices/", params)
                returned = len(data["results"])
                self.respond({
                    "count":    data["count"],   # total matching in Nautobot
                    "returned": returned,         # devices in this page
                    "offset":   offset,
                    "has_more": (offset + returned) < data["count"],
                    "devices":  [fmt_device(d) for d in data["results"]],
                })

            elif path.startswith("/devices/"):
                # Look up a single device by exact hostname
                name = urllib.parse.unquote(path[len("/devices/"):])
                data = nb_get("dcim/devices/", {"name": name, "depth": 1})
                if not data["results"]:
                    self.respond({"error": f"Device '{name}' not found"}, 404)
                else:
                    self.respond(fmt_device(data["results"][0]))

            elif path == "/tenants":
                data = nb_get("tenancy/tenants/")
                self.respond({
                    "count":   data["count"],
                    "tenants": [{"name": t["name"]} for t in data["results"]],
                })

            elif path == "/locations":
                params = {"depth": 1}
                if qs.get("type"):
                    # Nautobot location_type filter requires an ID, not a name.
                    # Resolve the type name to its ID first.
                    types = nb_get("dcim/location-types/", {"name": qs["type"]})
                    if types["results"]:
                        params["location_type"] = types["results"][0]["id"]
                data = nb_get("dcim/locations/", params)
                self.respond({
                    "count":     data["count"],
                    "locations": [{
                        "name":   loc["name"],
                        "type":   loc["location_type"]["name"] if loc.get("location_type") else None,
                        "parent": loc["parent"]["name"]        if loc.get("parent")        else None,
                    } for loc in data["results"]],
                })

            elif path == "/summary":
                # Fetch with limit=1 — we only need the count field, not the full result set
                devices   = nb_get("dcim/devices/",    {"limit": 1})
                tenants   = nb_get("tenancy/tenants/", {"limit": 1})
                locations = nb_get("dcim/locations/",  {"limit": 1})
                self.respond({
                    "total_devices":   devices["count"],
                    "total_tenants":   tenants["count"],
                    "total_locations": locations["count"],
                })

            elif path.startswith("/metrics/device/"):
                device_name = urllib.parse.unquote(path[len("/metrics/device/"):])
                metrics = {}

                # Scalar metrics: one value per device
                scalar_queries = {
                    "cpu_percent":    f'device_cpu_percent{{device="{device_name}"}}',
                    "memory_percent": f'device_memory_percent{{device="{device_name}"}}',
                    "up":             f'device_up{{device="{device_name}"}}',
                    "uptime_seconds": f'device_uptime_seconds{{device="{device_name}"}}',
                    "bgp_peers":      f'device_bgp_peers{{device="{device_name}"}}',
                }
                for key, q in scalar_queries.items():
                    res    = prom_query(q)
                    result = res.get("data", {}).get("result", [])
                    if result:
                        metrics[key] = float(result[0]["value"][1])
                        # Pull tenant/location/role from the Prometheus labels on the
                        # device_up metric so the LLM has inventory context alongside metrics
                        if key == "up":
                            lbl = result[0].get("metric", {})
                            metrics["tenant"]   = lbl.get("tenant", "")
                            metrics["location"] = lbl.get("location", "")
                            metrics["role"]     = lbl.get("role", "")

                # Interface traffic: one value per interface per direction, converted to Mbps
                for direction in ("rx", "tx"):
                    q      = f'device_interface_{direction}_bps{{device="{device_name}"}}'
                    res    = prom_query(q)
                    ifaces = {}
                    for r2 in res.get("data", {}).get("result", []):
                        iface        = r2["metric"].get("interface", "?")
                        bps          = float(r2["value"][1])
                        ifaces[iface] = round(bps / 1e6, 2)  # bps → Mbps
                    if ifaces:
                        metrics[f"interfaces_{direction}_mbps"] = ifaces

                if not metrics:
                    self.respond({"error": f"No metrics found for device '{device_name}'"}, 404)
                else:
                    self.respond({"device": device_name, "metrics": metrics})

            elif path == "/device-creation-context":
                tenant_q = qs.get("tenant")

                # Global catalog: roles, manufacturers, device types, namespaces
                roles_data = nb_get("extras/roles/", {"content_types": "dcim.device", "limit": 50})
                roles = [{"id": r["id"], "name": r["name"]} for r in roles_data.get("results", [])]

                mfr_data = nb_get("dcim/manufacturers/", {"limit": 50})
                manufacturers = [{"id": m["id"], "name": m["name"]} for m in mfr_data.get("results", [])]
                mfr_by_id = {m["id"]: m["name"] for m in mfr_data.get("results", [])}

                # depth=1 to get manufacturer.name nested inside device types
                dt_data = nb_get("dcim/device-types/", {"limit": 50, "depth": 1})
                all_device_types = [
                    {
                        "id":           dt["id"],
                        "model":        dt.get("model", ""),
                        "manufacturer": (dt.get("manufacturer") or {}).get("name", ""),
                    }
                    for dt in dt_data.get("results", [])
                ]

                ns_data = nb_get("ipam/namespaces/", {"limit": 20})
                namespaces = [{"id": n["id"], "name": n["name"]} for n in ns_data.get("results", [])]

                # Tenants — filtered if ?tenant= was provided
                t_params = {"limit": 50}
                if tenant_q:
                    t_params["q"] = tenant_q
                tenants_data = nb_get("tenancy/tenants/", t_params)
                tenants = [{"id": t["id"], "name": t["name"]} for t in tenants_data.get("results", [])]

                # Per-tenant: locations (derived from device assignments) + prefixes + suggested types
                locations_by_tenant = {}
                prefixes_by_tenant = {}
                suggested_device_types = {}

                for t in tenants[:5]:
                    tid = t["id"]
                    tid_str = str(tid)

                    # Locations aren't tenant-tagged directly — derive from the tenant's devices.
                    # depth=1 expands location.name and device_type.model in one call.
                    try:
                        devs = nb_get("dcim/devices/", {"tenant_id": tid, "limit": 100, "depth": 1})
                        seen_locs, seen_dts = {}, {}
                        for d in devs.get("results", []):
                            loc = d.get("location") or {}
                            if loc.get("id") and loc["id"] not in seen_locs:
                                seen_locs[loc["id"]] = {
                                    "id":   loc["id"],
                                    "name": loc.get("name") or loc.get("display", ""),
                                }
                            dt = d.get("device_type") or {}
                            if dt.get("id") and dt["id"] not in seen_dts:
                                mfr_id = (dt.get("manufacturer") or {}).get("id", "")
                                seen_dts[dt["id"]] = {
                                    "id":           dt["id"],
                                    "model":        dt.get("model", ""),
                                    "manufacturer": mfr_by_id.get(mfr_id, ""),
                                }
                        locations_by_tenant[tid_str]      = list(seen_locs.values())
                        suggested_device_types[tid_str]   = list(seen_dts.values())
                    except Exception:
                        locations_by_tenant[tid_str]    = []
                        suggested_device_types[tid_str] = []

                    try:
                        pfxs = nb_get("ipam/prefixes/", {"tenant_id": tid, "limit": 30})
                        prefixes_by_tenant[tid_str] = [
                            {
                                "id":        p["id"],
                                "prefix":    p["prefix"],
                                "status":    (p.get("status")    or {}).get("value", ""),
                                "namespace": (p.get("namespace") or {}).get("name", "Global"),
                            }
                            for p in pfxs.get("results", [])
                        ]
                    except Exception:
                        prefixes_by_tenant[tid_str] = []

                self.respond({
                    "tenants":                          tenants,
                    "device_roles":                     roles,
                    "manufacturers":                    manufacturers,
                    "all_device_types":                 all_device_types,
                    "ipam_namespaces":                  namespaces,
                    "locations_by_tenant":              locations_by_tenant,
                    "prefixes_by_tenant":               prefixes_by_tenant,
                    "suggested_device_types_by_tenant": suggested_device_types,
                    "workflow_guide": {
                        "description": "POST /devices handles the full creation workflow",
                        "post_body": {
                            "name":         "string — device hostname (required)",
                            "tenant":       "string — tenant name (required)",
                            "location":     "string — location name, partial match OK (required)",
                            "role":         "string — role name e.g. 'Access Switch' (required)",
                            "device_type":  "string — model name e.g. 'Catalyst 9300' (required)",
                            "manufacturer": "string — required only if device_type doesn't exist yet",
                            "status":       "string — 'Active'|'Planned'|'Staged' (default: 'Active')",
                            "management_ip": "string — CIDR e.g. '10.1.1.1/24' (optional)",
                        },
                        "notes": [
                            "Names are resolved to IDs automatically — no UUIDs needed",
                            "If device_type is unknown, provide manufacturer to create it",
                            "If management_ip prefix doesn't exist it will be created",
                            "management_ip is assigned to a 'mgmt0' interface and set as primary_ip4",
                        ],
                    },
                })

            else:
                self.respond({
                    "error":     "Not found",
                    "endpoints": [
                        "GET  /health",
                        "GET  /devices",
                        "GET  /devices/<name>",
                        "GET  /tenants",
                        "GET  /locations",
                        "GET  /summary",
                        "GET  /metrics/device/<name>",
                        "GET  /device-creation-context",
                        "POST /devices",
                    ],
                }, 404)

        except Exception as e:
            self.respond({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path.rstrip("/")

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            self.respond({"error": "Invalid JSON body"}, 400)
            return

        try:
            if path == "/devices":
                name            = body.get("name")
                tenant_name     = body.get("tenant")
                location_name   = body.get("location")
                role_name       = body.get("role")
                dt_model        = body.get("device_type")
                manufacturer    = body.get("manufacturer")
                status          = body.get("status", "Active")
                management_ip   = body.get("management_ip")

                if not all([name, tenant_name, location_name, role_name, dt_model]):
                    self.respond({"error": "Required fields: name, tenant, location, role, device_type"}, 400)
                    return

                # Resolve tenant (exact name match)
                t_data = nb_get("tenancy/tenants/", {"name": tenant_name})
                if not t_data["results"]:
                    self.respond({"error": f"Tenant '{tenant_name}' not found"}, 404)
                    return
                tenant_id = t_data["results"][0]["id"]

                # Resolve location via fuzzy q= search (same approach as /devices GET)
                loc_data = nb_get("dcim/locations/", {"q": location_name, "limit": 5})
                if not loc_data["results"]:
                    self.respond({"error": f"Location '{location_name}' not found"}, 404)
                    return
                location_id      = loc_data["results"][0]["id"]
                location_display = loc_data["results"][0].get("display", location_name)

                # Resolve role
                role_data = nb_get("extras/roles/", {"name": role_name, "content_types": "dcim.device"})
                if not role_data["results"]:
                    # Fall back to q= fuzzy search
                    role_data = nb_get("extras/roles/", {"q": role_name, "content_types": "dcim.device"})
                if not role_data["results"]:
                    self.respond({"error": f"Role '{role_name}' not found"}, 404)
                    return
                role_id = role_data["results"][0]["id"]

                # Resolve device type — create if not found (requires manufacturer)
                dt_data = nb_get("dcim/device-types/", {"model": dt_model})
                if dt_data["results"]:
                    device_type_id = dt_data["results"][0]["id"]
                else:
                    if not manufacturer:
                        self.respond({
                            "error": f"Device type '{dt_model}' not found. "
                                     "Provide 'manufacturer' in the request body to create it."
                        }, 404)
                        return
                    mfr_data = nb_get("dcim/manufacturers/", {"name": manufacturer})
                    if mfr_data["results"]:
                        mfr_id = mfr_data["results"][0]["id"]
                    else:
                        mfr    = nb_post("dcim/manufacturers/", {"name": manufacturer})
                        mfr_id = mfr["id"]
                    dt             = nb_post("dcim/device-types/", {"model": dt_model, "manufacturer": mfr_id})
                    device_type_id = dt["id"]

                # Create the device
                device    = nb_post("dcim/devices/", {
                    "name":        name,
                    "device_type": device_type_id,
                    "role":        role_id,
                    "location":    location_id,
                    "tenant":      tenant_id,
                    "status":      status,
                })
                device_id = device["id"]

                result = {
                    "device": {
                        "id":          device_id,
                        "name":        name,
                        "status":      status,
                        "tenant":      tenant_name,
                        "location":    location_display,
                        "role":        role_name,
                        "device_type": dt_model,
                    }
                }

                # Optional: create management IP, assign to mgmt0, set as primary_ip4
                if management_ip:
                    # Get the Global namespace id
                    ns_data      = nb_get("ipam/namespaces/", {"name": "Global"})
                    namespace_id = ns_data["results"][0]["id"] if ns_data["results"] else None

                    # Ensure a covering prefix exists — create one if not
                    net        = ipaddress.ip_interface(management_ip).network
                    prefix_str = str(net)
                    pfx_data   = nb_get("ipam/prefixes/", {"prefix": prefix_str})
                    if not pfx_data["results"]:
                        pfx_body = {"prefix": prefix_str, "status": "Active", "tenant": tenant_id}
                        if namespace_id:
                            pfx_body["namespace"] = namespace_id
                        nb_post("ipam/prefixes/", pfx_body)

                    # Create the IP address
                    ip_body = {"address": management_ip, "status": "Active", "tenant": tenant_id}
                    if namespace_id:
                        ip_body["namespace"] = namespace_id
                    ip    = nb_post("ipam/ip-addresses/", ip_body)
                    ip_id = ip["id"]

                    # Create a mgmt0 interface on the device
                    iface    = nb_post("dcim/interfaces/", {
                        "device": device_id,
                        "name":   "mgmt0",
                        "type":   "virtual",
                        "status": "Active",
                    })
                    iface_id = iface["id"]

                    # Assign the IP to the interface via Nautobot 2.x dedicated endpoint
                    nb_post("ipam/ip-address-to-interface/", {
                        "ip_address": ip_id,
                        "interface":  iface_id,
                    })

                    # Set as device primary IPv4
                    nb_patch(f"dcim/devices/{device_id}/", {"primary_ip4": ip_id})

                    result["management_ip"] = {
                        "id":        ip_id,
                        "address":   management_ip,
                        "interface": "mgmt0",
                        "prefix":    prefix_str,
                    }

                self.respond(result, 201)

            else:
                self.respond({
                    "error":     "Not found",
                    "endpoints": ["POST /devices"],
                }, 404)

        except Exception as e:
            self.respond({"error": str(e)}, 500)

    def respond(self, data, status=200):
        """Serialise data as JSON and write the HTTP response."""
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
