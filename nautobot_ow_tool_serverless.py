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
        PROMETHEUS_DEVICE_LABEL: str = "device"
        GRAFANA_URL: str = ""
        GRAFANA_DATASOURCE: str = "prometheus"

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

    def _device_selector(self, name):
        lbl = self.valves.PROMETHEUS_DEVICE_LABEL
        return f'{lbl}="{name}"'

    def _grafana_explore_url(self, expr):
        if not self.valves.GRAFANA_URL:
            return None
        left = json.dumps({
            "datasource": self.valves.GRAFANA_DATASOURCE,
            "queries": [{"expr": expr, "refId": "A"}],
            "range": {"from": "now-1h", "to": "now"},
        }, separators=(",", ":"))
        return f"{self.valves.GRAFANA_URL.rstrip('/')}/explore?orgId=1&left={urllib.parse.quote(left)}"

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
            "monitoring":  (d.get("custom_fields") or {}).get("monitoring"),
        }

    # ── Tool methods ──────────────────────────────────────────────────────────

    def list_tenants(self, limit: int = 50, offset: int = 0) -> str:
        """
        List all tenants (customers / organisations) in Nautobot.
        Paginated — use limit and offset to page through results.
        """
        data     = self._nb_get("tenancy/tenants/", {"limit": limit, "offset": offset})
        returned = len(data["results"])
        return json.dumps({
            "count":    data["count"],
            "returned": returned,
            "offset":   offset,
            "has_more": (offset + returned) < data["count"],
            "tenants":  [{"name": t["name"]} for t in data["results"]],
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

    def list_devices(self, location: str = "", tenant: str = "", role: str = "",
                     limit: int = 50, offset: int = 0) -> str:
        """
        List network devices. At least one filter is required — location, tenant, or role.
        Always call list_tenants() first to get the exact tenant name, then
        list_locations(tenant=...) to get the exact location, then call this.
        Location accepts partial names. Tenant must be exact.
        Paginated — check has_more and increment offset by limit to fetch the next page.
        """
        if not any([location, tenant, role]):
            return json.dumps({
                "error": "At least one filter is required (location, tenant, or role). "
                         "Call list_tenants() first, then list_locations(tenant=...), then call this."
            }, indent=2)

        params = {"limit": limit, "offset": offset, "depth": 1}
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
            "offset":   offset,
            "has_more": (offset + returned) < data["count"],
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

    def get_device_interfaces(self, name: str) -> str:
        """
        List all interfaces on a device by its exact hostname.
        Returns interface name, type, status, enabled state, and assigned IP addresses.
        """
        dev = self._nb_get("dcim/devices/", {"name": name})
        if not dev["results"]:
            return json.dumps({"error": f"Device '{name}' not found"}, indent=2)
        device_id = dev["results"][0]["id"]
        data = self._nb_get("dcim/interfaces/", {"device_id": device_id, "depth": 1, "limit": 100})
        interfaces = []
        for iface in data["results"]:
            assignments = self._nb_get("ipam/ip-address-to-interface/", {"interface": iface["id"], "depth": 1})
            ips = [(a.get("ip_address") or {}).get("address") for a in assignments.get("results", []) if a.get("ip_address")]
            interfaces.append({
                "name":    iface["name"],
                "type":    (iface.get("type") or {}).get("label"),
                "status":  (iface.get("status") or {}).get("name"),
                "enabled": iface.get("enabled", True),
                "ips":     ips,
            })
        return json.dumps({"device": name, "count": len(interfaces), "interfaces": interfaces}, indent=2)

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

    def get_tenant_summary(self, tenant: str) -> str:
        """
        Get a breakdown of devices for a specific tenant, grouped by role, device type,
        and location. Use this to answer questions like "what devices does customer X use?"
        or "how many routers does customer X have?".
        """
        t_data = self._nb_get("tenancy/tenants/", {"name": tenant})
        if not t_data["results"]:
            return json.dumps({"error": f"Tenant '{tenant}' not found"}, indent=2)
        tenant_id = t_data["results"][0]["id"]

        devs = self._nb_get("dcim/devices/", {"tenant_id": tenant_id, "limit": 500, "depth": 1})
        by_role, by_type, by_location = {}, {}, {}
        for d in devs.get("results", []):
            role = (d.get("role")        or {}).get("name", "Unknown")
            dt   = (d.get("device_type") or {}).get("display", "Unknown")
            loc  = (d.get("location")    or {}).get("name",    "Unknown")
            by_role[role]         = by_role.get(role, 0) + 1
            by_type[dt]           = by_type.get(dt, 0) + 1
            by_location[loc]      = by_location.get(loc, 0) + 1

        return json.dumps({
            "tenant":       tenant,
            "total_devices": devs["count"],
            "by_role":      by_role,
            "by_device_type": by_type,
            "by_location":  by_location,
        }, indent=2)

    def list_prefixes(self, tenant: str = "", limit: int = 50, offset: int = 0) -> str:
        """
        List IPAM prefixes. Optionally filter by tenant name.
        Paginated — use limit and offset to page through results.
        """
        params = {"limit": limit, "offset": offset}
        if tenant:
            t_data = self._nb_get("tenancy/tenants/", {"name": tenant})
            if not t_data["results"]:
                return json.dumps({"error": f"Tenant '{tenant}' not found"}, indent=2)
            params["tenant_id"] = t_data["results"][0]["id"]
        data     = self._nb_get("ipam/prefixes/", params)
        returned = len(data["results"])
        return json.dumps({
            "count":    data["count"],
            "returned": returned,
            "offset":   offset,
            "has_more": (offset + returned) < data["count"],
            "prefixes": [{
                "prefix":      p["prefix"],
                "status":      (p.get("status")    or {}).get("value", ""),
                "namespace":   (p.get("namespace") or {}).get("name", "Global"),
                "tenant":      (p.get("tenant")    or {}).get("name"),
                "description": p.get("description", ""),
            } for p in data["results"]],
        }, indent=2)

    def get_device_metrics(self, name: str) -> str:
        """
        Get live Prometheus metrics for a specific device by its exact hostname.
        Always discovers what metrics are available rather than assuming fixed names —
        works with any exporter (SNMP, built-in, etc.). Uses PROMETHEUS_DEVICE_LABEL
        valve to match the device (default: 'device'; set to 'instance' or
        'exported_instance' for SNMP exporter).
        """
        return self.get_available_metrics(name)

    def get_available_metrics(self, name: str) -> str:
        """
        Discover all Prometheus metric series available for a device by its hostname.
        Returns every metric name, its current value, and a Grafana Explore link (if
        GRAFANA_URL is configured). Useful when the Prometheus exporter is unknown
        (e.g. SNMP exporter) and you need to know what data exists before querying.
        Uses PROMETHEUS_DEVICE_LABEL valve to find the device (default: 'device';
        set to 'instance' or 'exported_instance' for SNMP exporter).
        """
        sel = self._device_selector(name)
        url = (
            f"{self.valves.PROMETHEUS_URL}/api/v1/series?"
            + urllib.parse.urlencode({"match[]": f"{{{sel}}}"})
        )
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                series_data = json.loads(resp.read())
        except Exception as e:
            return json.dumps({"error": str(e)}, indent=2)

        series = series_data.get("data", [])
        if not series:
            return json.dumps({
                "error": f"No metrics found for device '{name}' using label "
                         f"'{self.valves.PROMETHEUS_DEVICE_LABEL}={name}'",
                "hint": "Check PROMETHEUS_DEVICE_LABEL valve — try 'instance' or 'exported_instance' for SNMP exporter",
            }, indent=2)

        metric_names = sorted({s["__name__"] for s in series})

        sample_values = {}
        for metric in metric_names[:20]:
            expr    = f'{metric}{{{sel}}}'
            res     = self._prom_query(expr)
            results = res.get("data", {}).get("result", [])
            if results:
                labels = {k: v for k, v in results[0]["metric"].items() if not k.startswith("__")}
                labels.pop(self.valves.PROMETHEUS_DEVICE_LABEL, None)
                entry = {
                    "value":        results[0]["value"][1],
                    "labels":       labels,
                    "series_count": len(results),
                }
                grafana_url = self._grafana_explore_url(expr)
                if grafana_url:
                    entry["grafana_url"] = grafana_url
                sample_values[metric] = entry

        result = {
            "device":        name,
            "label_used":    f"{self.valves.PROMETHEUS_DEVICE_LABEL}={name}",
            "total_metrics": len(metric_names),
            "metrics":       sample_values,
        }
        if self.valves.GRAFANA_URL:
            result["grafana_all"] = self._grafana_explore_url(f'{{{sel}}}')

        return json.dumps(result, indent=2)

    def get_metric_grafana_link(self, device: str, metric: str) -> str:
        """
        Generate a Grafana Explore link for a specific metric and device.
        Use this after get_available_metrics() when the user wants to view a
        particular metric in Grafana. Requires GRAFANA_URL to be configured.
        metric: exact metric name (e.g. 'ifHCInOctets', 'device_cpu_percent')
        device: device hostname as it appears in Prometheus
        """
        if not self.valves.GRAFANA_URL:
            return json.dumps({"error": "GRAFANA_URL valve is not configured"}, indent=2)
        sel  = self._device_selector(device)
        expr = f'{metric}{{{sel}}}'
        return json.dumps({
            "device":      device,
            "metric":      metric,
            "expr":        expr,
            "grafana_url": self._grafana_explore_url(expr),
        }, indent=2)

    def get_metrics_by_location(self, location: str, role: str = "", tenant: str = "") -> str:
        """
        Get live Prometheus metrics for a device at a specific location, identified by
        location name and role (e.g. "Core Router"). Use this when you know the location
        but not the exact hostname — e.g. "metrics for the router at Wellington Central".
        If multiple devices match, returns the list so you can pick one.
        """
        params = {"depth": 1, "limit": 10}
        if role:
            params["role"] = role
        if tenant:
            params["tenant"] = tenant

        loc_data = self._nb_get("dcim/locations/", {"q": location, "limit": 10})
        loc_ids  = [loc["id"] for loc in loc_data.get("results", [])]
        if not loc_ids:
            return json.dumps({"error": f"No locations matched '{location}'"}, indent=2)
        params["location"] = loc_ids

        devs = self._nb_get("dcim/devices/", params)
        if not devs["results"]:
            return json.dumps({"error": f"No devices found at '{location}'" + (f" with role '{role}'" if role else "")}, indent=2)

        if len(devs["results"]) > 1:
            return json.dumps({
                "note": "Multiple devices matched — use get_device_metrics(name) with one of these:",
                "devices": [self._fmt_device(d) for d in devs["results"]],
            }, indent=2)

        name = devs["results"][0]["name"]
        return self.get_available_metrics(name)

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
                    "monitoring":    "monitoring profile — choices: 'if_mib' (default)",
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
        monitoring: str = "if_mib",
    ) -> str:
        """
        Create a new device in Nautobot. Handles the full workflow automatically:
        resolves all names to IDs, creates the device type and manufacturer if they
        don't exist, creates the IP prefix if needed, creates the management IP,
        assigns it to a mgmt0 interface, and sets it as the device primary IPv4.

        Required: name, tenant (exact), location (partial match OK), role, device_type.
        Optional: manufacturer (only needed if device_type doesn't exist yet),
                  status (default: Active), management_ip (CIDR, e.g. "10.1.1.1/24"),
                  monitoring (default: 'if_mib' — monitoring profile custom field).

        Call get_device_creation_context() first to discover valid names.
        """
        try:
            t_data = self._nb_get("tenancy/tenants/", {"name": tenant})
            if not t_data["results"]:
                return json.dumps({"error": f"Tenant '{tenant}' not found"}, indent=2)
            tenant_id = t_data["results"][0]["id"]

            loc_data = self._nb_get("dcim/locations/", {"q": location, "limit": 5})
            if not loc_data["results"]:
                return json.dumps({"error": f"Location '{location}' not found"}, indent=2)
            location_id      = loc_data["results"][0]["id"]
            location_display = loc_data["results"][0].get("display", location)

            role_data = self._nb_get("extras/roles/", {"name": role, "content_types": "dcim.device"})
            if not role_data["results"]:
                role_data = self._nb_get("extras/roles/", {"q": role, "content_types": "dcim.device"})
            if not role_data["results"]:
                return json.dumps({"error": f"Role '{role}' not found"}, indent=2)
            role_id = role_data["results"][0]["id"]

            dt_data = self._nb_get("dcim/device-types/", {"model": device_type})
            if dt_data["results"]:
                device_type_id = dt_data["results"][0]["id"]
            else:
                if not manufacturer:
                    return json.dumps({
                        "error": f"Device type '{device_type}' not found. Provide 'manufacturer' to create it."
                    }, indent=2)
                mfr_data = self._nb_get("dcim/manufacturers/", {"name": manufacturer})
                mfr_id   = mfr_data["results"][0]["id"] if mfr_data["results"] else self._nb_post("dcim/manufacturers/", {"name": manufacturer})["id"]
                device_type_id = self._nb_post("dcim/device-types/", {"model": device_type, "manufacturer": mfr_id})["id"]

            device    = self._nb_post("dcim/devices/", {
                "name":        name,
                "device_type": device_type_id,
                "role":        role_id,
                "location":    location_id,
                "tenant":      tenant_id,
                "status":      status,
            })
            device_id = device["id"]

            if monitoring:
                self._nb_patch(f"dcim/devices/{device_id}/", {"custom_fields": {"monitoring": monitoring}})

            result = {
                "device": {
                    "id":          device_id,
                    "name":        name,
                    "status":      status,
                    "tenant":      tenant,
                    "location":    location_display,
                    "role":        role,
                    "device_type": device_type,
                    "monitoring":  monitoring or None,
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
