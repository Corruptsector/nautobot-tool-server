"""
title: Nautobot (serverless)
author: system
description: Query and manage the Nautobot network inventory. Talks directly to Nautobot and Prometheus — no tool server required. Configure NAUTOBOT_URL, NAUTOBOT_TOKEN, and PROMETHEUS_URL in Valves.
"""

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from pydantic import BaseModel


class Tools:
    class Valves(BaseModel):
        NAUTOBOT_URL: str = "http://nautobot:8080"
        NAUTOBOT_TOKEN: str = ""
        PROMETHEUS_URL: str = "http://prometheus:9090"

    def __init__(self):
        self.valves = self.Valves()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _nb_headers(self):
        return {
            "Authorization": f"Token {self.valves.NAUTOBOT_TOKEN}",
            "Accept": "application/json",
        }

    def _nb_get(self, endpoint, params=None):
        url = f"{self.valves.NAUTOBOT_URL}/api/{endpoint}"
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        req = urllib.request.Request(url, headers=self._nb_headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"Nautobot {endpoint} returned {e.code}: {e.read().decode()}")

    def _nb_post(self, endpoint, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.valves.NAUTOBOT_URL}/api/{endpoint}",
            data=data,
            headers={**self._nb_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"Nautobot POST {endpoint} returned {e.code}: {e.read().decode()}")

    def _nb_patch(self, endpoint, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.valves.NAUTOBOT_URL}/api/{endpoint}",
            data=data,
            headers={**self._nb_headers(), "Content-Type": "application/json"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise ValueError(f"Nautobot PATCH {endpoint} returned {e.code}: {e.read().decode()}")

    def _prom_query(self, promql):
        url = (
            f"{self.valves.PROMETHEUS_URL}/api/v1/query?"
            + urllib.parse.urlencode({"query": promql})
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())

    def _fmt_device(self, d):
        return {
            "name":        d["name"],
            "status":      (d.get("status")      or {}).get("name"),
            "location":    (d.get("location")    or {}).get("display"),
            "tenant":      (d.get("tenant")      or {}).get("name"),
            "role":        (d.get("role")        or {}).get("name"),
            "device_type": (d.get("device_type") or {}).get("display"),
            "platform":    (d.get("platform")    or {}).get("name"),
            "primary_ip":  (d.get("primary_ip4") or {}).get("address"),
        }

    # ── Tool methods ──────────────────────────────────────────────────────────

    def list_tenants(self) -> str:
        """
        List all tenants (customers / organisations) in Nautobot.
        """
        data = self._nb_get("tenancy/tenants/")
        return json.dumps({
            "count":   data["count"],
            "tenants": [{"name": t["name"]} for t in data["results"]],
        }, indent=2)

    def list_locations(self, type: str = "", tenant: str = "") -> str:
        """
        List locations in Nautobot.
        Optional: filter by type name (Region, Country, or Site).
        Optional: filter by tenant name — returns only locations that tenant has devices in.
        """
        if tenant:
            t_data = self._nb_get("tenancy/tenants/", {"name": tenant})
            if not t_data["results"]:
                return json.dumps({"error": f"Tenant '{tenant}' not found"}, indent=2)
            tenant_id = t_data["results"][0]["id"]
            devs = self._nb_get("dcim/devices/", {"tenant_id": tenant_id, "limit": 200, "depth": 1})
            seen = {}
            for d in devs.get("results", []):
                loc = d.get("location") or {}
                if loc.get("id") and loc["id"] not in seen:
                    seen[loc["id"]] = {
                        "name":   loc.get("name") or loc.get("display", ""),
                        "type":   (loc.get("location_type") or {}).get("name"),
                        "parent": (loc.get("parent") or {}).get("name"),
                    }
            locations = list(seen.values())
            return json.dumps({"count": len(locations), "locations": locations}, indent=2)

        params = {"depth": 1}
        if type:
            types = self._nb_get("dcim/location-types/", {"name": type})
            if types["results"]:
                params["location_type"] = types["results"][0]["id"]
        data = self._nb_get("dcim/locations/", params)
        return json.dumps({
            "count": data["count"],
            "locations": [{
                "name":   loc["name"],
                "type":   (loc.get("location_type") or {}).get("name"),
                "parent": (loc.get("parent")        or {}).get("name"),
            } for loc in data["results"]],
        }, indent=2)

    def list_devices(self, location: str = "", tenant: str = "", role: str = "") -> str:
        """
        List network devices. At least one filter is required — location, tenant, or role.
        IMPORTANT: always call list_tenants() first to get the exact tenant name, then
        list_locations() to get the exact location, then call this with both filters set.
        Location accepts partial names (e.g. "Wellington Central").
        Tenant must be exact (e.g. "ANZ Bank New Zealand").
        Results are paginated — check has_more and use offset to fetch the next page.
        """
        if not any([location, tenant, role]):
            return json.dumps({
                "error": "At least one filter is required (location, tenant, or role). "
                         "Call list_tenants() first, then list_locations(), then call this."
            }, indent=2)

        params = {"limit": 50, "offset": 0, "depth": 1}
        if tenant:
            params["tenant"] = tenant
        if role:
            params["role"] = role

        if location:
            loc_data = self._nb_get("dcim/locations/", {"q": location, "limit": 50})
            loc_ids  = [loc["id"] for loc in loc_data.get("results", [])]
            if not loc_ids:
                return json.dumps({"count": 0, "devices": [], "note": f"No locations matched '{location}'"}, indent=2)
            params["location"] = loc_ids

        data     = self._nb_get("dcim/devices/", params)
        returned = len(data["results"])
        return json.dumps({
            "count":    data["count"],
            "returned": returned,
            "offset":   0,
            "has_more": returned < data["count"],
            "devices":  [self._fmt_device(d) for d in data["results"]],
        }, indent=2)

    def get_device(self, name: str) -> str:
        """
        Get detailed information about a specific network device by its exact hostname.
        """
        data = self._nb_get("dcim/devices/", {"name": name, "depth": 1})
        if not data["results"]:
            return json.dumps({"error": f"Device '{name}' not found"}, indent=2)
        return json.dumps(self._fmt_device(data["results"][0]), indent=2)

    def get_inventory_summary(self) -> str:
        """
        Return a high-level summary of the Nautobot inventory: total device count,
        tenant count, and location count.
        """
        devices   = self._nb_get("dcim/devices/",    {"limit": 1})
        tenants   = self._nb_get("tenancy/tenants/", {"limit": 1})
        locations = self._nb_get("dcim/locations/",  {"limit": 1})
        return json.dumps({
            "total_devices":   devices["count"],
            "total_tenants":   tenants["count"],
            "total_locations": locations["count"],
        }, indent=2)

    def get_device_metrics(self, name: str) -> str:
        """
        Get live Prometheus metrics for a specific device by its exact hostname.
        Returns CPU percent, memory percent, up/down status, uptime, BGP peer count
        (routers only), and per-interface receive/transmit throughput in Mbps.
        """
        metrics = {}
        scalar_queries = {
            "cpu_percent":    f'device_cpu_percent{{device="{name}"}}',
            "memory_percent": f'device_memory_percent{{device="{name}"}}',
            "up":             f'device_up{{device="{name}"}}',
            "uptime_seconds": f'device_uptime_seconds{{device="{name}"}}',
            "bgp_peers":      f'device_bgp_peers{{device="{name}"}}',
        }
        for key, q in scalar_queries.items():
            res    = self._prom_query(q)
            result = res.get("data", {}).get("result", [])
            if result:
                metrics[key] = float(result[0]["value"][1])
                if key == "up":
                    lbl = result[0].get("metric", {})
                    metrics["tenant"]   = lbl.get("tenant", "")
                    metrics["location"] = lbl.get("location", "")
                    metrics["role"]     = lbl.get("role", "")

        for direction in ("rx", "tx"):
            res    = self._prom_query(f'device_interface_{direction}_bps{{device="{name}"}}')
            ifaces = {}
            for r in res.get("data", {}).get("result", []):
                ifaces[r["metric"].get("interface", "?")] = round(float(r["value"][1]) / 1e6, 2)
            if ifaces:
                metrics[f"interfaces_{direction}_mbps"] = ifaces

        if not metrics:
            return json.dumps({"error": f"No metrics found for device '{name}'"}, indent=2)
        return json.dumps({"device": name, "metrics": metrics}, indent=2)

    def get_device_creation_context(self, tenant: str = "") -> str:
        """
        Fetch everything needed before creating a new device: matching tenants, their
        locations (derived from existing devices), device types already used by the tenant
        (as suggestions), available IPAM prefixes, all device roles, all device types,
        manufacturers, and IPAM namespaces.
        Also returns a workflow guide showing the exact fields to pass to create_device().
        Call this first when asked to create a device — it gives you all the names you need.
        Optional: pass a tenant name to filter results (e.g. "ANZ").
        """
        roles_data = self._nb_get("extras/roles/", {"content_types": "dcim.device", "limit": 50})
        roles = [{"id": r["id"], "name": r["name"]} for r in roles_data.get("results", [])]

        mfr_data = self._nb_get("dcim/manufacturers/", {"limit": 50})
        manufacturers = [{"id": m["id"], "name": m["name"]} for m in mfr_data.get("results", [])]
        mfr_by_id = {m["id"]: m["name"] for m in mfr_data.get("results", [])}

        dt_data = self._nb_get("dcim/device-types/", {"limit": 50, "depth": 1})
        all_device_types = [
            {
                "id":           dt["id"],
                "model":        dt.get("model", ""),
                "manufacturer": (dt.get("manufacturer") or {}).get("name", ""),
            }
            for dt in dt_data.get("results", [])
        ]

        ns_data = self._nb_get("ipam/namespaces/", {"limit": 20})
        namespaces = [{"id": n["id"], "name": n["name"]} for n in ns_data.get("results", [])]

        t_params = {"limit": 50}
        if tenant:
            t_params["q"] = tenant
        tenants_data = self._nb_get("tenancy/tenants/", t_params)
        tenants = [{"id": t["id"], "name": t["name"]} for t in tenants_data.get("results", [])]

        locations_by_tenant = {}
        prefixes_by_tenant = {}
        suggested_device_types = {}

        for t in tenants[:5]:
            tid     = t["id"]
            tid_str = str(tid)
            try:
                devs      = self._nb_get("dcim/devices/", {"tenant_id": tid, "limit": 100, "depth": 1})
                seen_locs = {}
                seen_dts  = {}
                for d in devs.get("results", []):
                    loc = d.get("location") or {}
                    if loc.get("id") and loc["id"] not in seen_locs:
                        seen_locs[loc["id"]] = {"id": loc["id"], "name": loc.get("name") or loc.get("display", "")}
                    dt = d.get("device_type") or {}
                    if dt.get("id") and dt["id"] not in seen_dts:
                        mfr_id = (dt.get("manufacturer") or {}).get("id", "")
                        seen_dts[dt["id"]] = {"id": dt["id"], "model": dt.get("model", ""), "manufacturer": mfr_by_id.get(mfr_id, "")}
                locations_by_tenant[tid_str]    = list(seen_locs.values())
                suggested_device_types[tid_str] = list(seen_dts.values())
            except Exception:
                locations_by_tenant[tid_str]    = []
                suggested_device_types[tid_str] = []

            try:
                pfxs = self._nb_get("ipam/prefixes/", {"tenant_id": tid, "limit": 30})
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

        return json.dumps({
            "tenants":                          tenants,
            "device_roles":                     roles,
            "manufacturers":                    manufacturers,
            "all_device_types":                 all_device_types,
            "ipam_namespaces":                  namespaces,
            "locations_by_tenant":              locations_by_tenant,
            "prefixes_by_tenant":               prefixes_by_tenant,
            "suggested_device_types_by_tenant": suggested_device_types,
            "workflow_guide": {
                "description": "Pass these fields to create_device()",
                "fields": {
                    "name":          "device hostname (required)",
                    "tenant":        "tenant name (required)",
                    "location":      "location name, partial match OK (required)",
                    "role":          "role name e.g. 'Access Switch' (required)",
                    "device_type":   "model name e.g. 'Catalyst 9300' (required)",
                    "manufacturer":  "required only if device_type doesn't exist yet",
                    "status":        "'Active'|'Planned'|'Staged' (default: 'Active')",
                    "management_ip": "CIDR e.g. '10.1.1.1/24' (optional)",
                },
                "notes": [
                    "Names are resolved to IDs automatically — no UUIDs needed",
                    "If device_type is unknown, provide manufacturer to create it",
                    "If management_ip prefix doesn't exist it will be created",
                    "management_ip is assigned to a 'mgmt0' interface and set as primary_ip4",
                ],
            },
        }, indent=2)

    def create_device(
        self,
        name: str,
        tenant: str,
        location: str,
        role: str,
        device_type: str,
        status: str = "Active",
        manufacturer: str = "",
        management_ip: str = "",
    ) -> str:
        """
        Create a new device in Nautobot. Handles the full workflow automatically:
        resolves all names to IDs, creates the device type and manufacturer if they
        don't exist, creates the IP prefix if needed, creates the management IP,
        assigns it to a mgmt0 interface, and sets it as the device primary IPv4.

        Required: name, tenant (exact), location (partial match OK), role, device_type.
        Optional: manufacturer (only needed if device_type doesn't exist yet),
                  status (default: Active), management_ip (CIDR, e.g. "10.1.1.1/24").

        Call get_device_creation_context() first to discover valid names.
        """
        try:
            # Resolve tenant
            t_data = self._nb_get("tenancy/tenants/", {"name": tenant})
            if not t_data["results"]:
                return json.dumps({"error": f"Tenant '{tenant}' not found"}, indent=2)
            tenant_id = t_data["results"][0]["id"]

            # Resolve location (fuzzy)
            loc_data = self._nb_get("dcim/locations/", {"q": location, "limit": 5})
            if not loc_data["results"]:
                return json.dumps({"error": f"Location '{location}' not found"}, indent=2)
            location_id      = loc_data["results"][0]["id"]
            location_display = loc_data["results"][0].get("display", location)

            # Resolve role
            role_data = self._nb_get("extras/roles/", {"name": role, "content_types": "dcim.device"})
            if not role_data["results"]:
                role_data = self._nb_get("extras/roles/", {"q": role, "content_types": "dcim.device"})
            if not role_data["results"]:
                return json.dumps({"error": f"Role '{role}' not found"}, indent=2)
            role_id = role_data["results"][0]["id"]

            # Resolve device type — create if not found
            dt_data = self._nb_get("dcim/device-types/", {"model": device_type})
            if dt_data["results"]:
                device_type_id = dt_data["results"][0]["id"]
            else:
                if not manufacturer:
                    return json.dumps({
                        "error": f"Device type '{device_type}' not found. "
                                 "Provide 'manufacturer' to create it."
                    }, indent=2)
                mfr_data = self._nb_get("dcim/manufacturers/", {"name": manufacturer})
                mfr_id   = mfr_data["results"][0]["id"] if mfr_data["results"] else self._nb_post("dcim/manufacturers/", {"name": manufacturer})["id"]
                device_type_id = self._nb_post("dcim/device-types/", {"model": device_type, "manufacturer": mfr_id})["id"]

            # Create device
            device    = self._nb_post("dcim/devices/", {
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
                    "tenant":      tenant,
                    "location":    location_display,
                    "role":        role,
                    "device_type": device_type,
                }
            }

            if management_ip:
                ns_data      = self._nb_get("ipam/namespaces/", {"name": "Global"})
                namespace_id = ns_data["results"][0]["id"] if ns_data["results"] else None

                prefix_str = str(ipaddress.ip_interface(management_ip).network)
                pfx_data   = self._nb_get("ipam/prefixes/", {"prefix": prefix_str})
                if not pfx_data["results"]:
                    pfx_body = {"prefix": prefix_str, "status": "Active", "tenant": tenant_id}
                    if namespace_id:
                        pfx_body["namespace"] = namespace_id
                    self._nb_post("ipam/prefixes/", pfx_body)

                ip_body = {"address": management_ip, "status": "Active", "tenant": tenant_id}
                if namespace_id:
                    ip_body["namespace"] = namespace_id
                ip_id = self._nb_post("ipam/ip-addresses/", ip_body)["id"]

                iface_id = self._nb_post("dcim/interfaces/", {
                    "device": device_id,
                    "name":   "mgmt0",
                    "type":   "virtual",
                    "status": "Active",
                })["id"]

                self._nb_post("ipam/ip-address-to-interface/", {
                    "ip_address": ip_id,
                    "interface":  iface_id,
                })

                self._nb_patch(f"dcim/devices/{device_id}/", {"primary_ip4": ip_id})

                result["management_ip"] = {
                    "id":        ip_id,
                    "address":   management_ip,
                    "interface": "mgmt0",
                    "prefix":    prefix_str,
                }

            return json.dumps(result, indent=2)

        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)
