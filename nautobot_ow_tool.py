"""
title: Nautobot
author: system
description: Query and manage the Nautobot network inventory. List devices, tenants, locations, metrics, and create new devices.
"""

import json
import urllib.request
import urllib.parse
import urllib.error

TOOL_SERVER = "http://nautobot-tool-server:8000"


def _get(path, params=None):
    url = f"{TOOL_SERVER}{path}"
    if params:
        filtered = {k: v for k, v in params.items() if v}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{TOOL_SERVER}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


class Tools:
    def list_devices(self, location: str = "", tenant: str = "", role: str = "") -> str:
        """
        List network devices. At least one filter is required — location, tenant, or role.
        IMPORTANT: always call list_tenants() first to get the exact tenant name, then
        list_locations() to get the exact location, then call this with both filters set.
        This keeps queries efficient over large inventories.
        Location accepts partial names (e.g. "Wellington Central").
        Tenant must be exact (e.g. "ANZ Bank New Zealand").
        Results are paginated — check has_more and use offset to fetch the next page.
        """
        result = _get("/devices", {"location": location, "tenant": tenant, "role": role})
        return json.dumps(result, indent=2)

    def get_device(self, name: str) -> str:
        """
        Get detailed information about a specific network device by its exact hostname.
        """
        result = _get(f"/devices/{urllib.parse.quote(name)}")
        return json.dumps(result, indent=2)

    def list_tenants(self) -> str:
        """
        List all tenants (customers / organisations) in Nautobot.
        """
        result = _get("/tenants")
        return json.dumps(result, indent=2)

    def list_locations(self, type: str = "") -> str:
        """
        List all locations in Nautobot. Optionally filter by type name: Region, Country, or Site.
        """
        result = _get("/locations", {"type": type})
        return json.dumps(result, indent=2)

    def get_inventory_summary(self) -> str:
        """
        Return a high-level summary of the Nautobot inventory: total device count,
        tenant count, and location count.
        """
        result = _get("/summary")
        return json.dumps(result, indent=2)

    def get_device_metrics(self, name: str) -> str:
        """
        Get live Prometheus metrics for a specific device by its exact hostname
        (as it appears in Nautobot, e.g. "anz-wlg-cen-rtr-01").
        Returns CPU percent, memory percent, up/down status, uptime, BGP peer count
        (routers only), and per-interface receive/transmit throughput in Mbps.
        """
        result = _get(f"/metrics/device/{urllib.parse.quote(name)}")
        return json.dumps(result, indent=2)

    def get_device_creation_context(self, tenant: str = "") -> str:
        """
        Fetch everything needed before creating a new device: matching tenants, their
        locations (derived from existing devices), device types already used by the tenant
        (as suggestions), available IPAM prefixes, all device roles, all device types,
        manufacturers, and IPAM namespaces.
        Also returns a workflow guide showing the exact body to pass to create_device().
        Call this first when asked to create a device — it gives you all the names you need.
        Optional: pass a tenant name to filter results (e.g. "ANZ").
        """
        result = _get("/device-creation-context", {"tenant": tenant} if tenant else None)
        return json.dumps(result, indent=2)

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

        Required: name, tenant (exact), location (partial match OK), role, device_type (model name).
        Optional: manufacturer (only needed if device_type doesn't exist yet),
                  status (default: Active), management_ip (CIDR, e.g. "10.1.1.1/24").

        Call get_device_creation_context() first to discover valid tenant names, locations,
        roles, and device types.
        """
        body = {
            "name": name,
            "tenant": tenant,
            "location": location,
            "role": role,
            "device_type": device_type,
            "status": status,
        }
        if manufacturer:
            body["manufacturer"] = manufacturer
        if management_ip:
            body["management_ip"] = management_ip

        result, status_code = _post("/devices", body)
        return json.dumps(result, indent=2)
