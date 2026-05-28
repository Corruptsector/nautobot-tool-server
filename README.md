# Nautobot Tool (Open WebUI)

An Open WebUI Python function tool that lets an LLM query and manage a [Nautobot](https://nautobot.readthedocs.io/) network inventory and live Prometheus metrics — no intermediate server required.

## How it works

```
Open WebUI (LLM tool call)
        │
        ├──▶  Nautobot REST API   — inventory, devices, tenants, locations
        └──▶  Prometheus HTTP API — live device metrics
```

The tool runs inside Open WebUI and calls Nautobot and Prometheus directly. Configuration is via **Valves** — editable fields on the tool's settings page in the UI.

## File

| File | Description |
|------|-------------|
| `nautobot_ow_tool_serverless.py` | The tool — load this into Open WebUI |

## Installing

### From GitHub (easiest)

In Open WebUI go to **Workspace → Tools → + Add Tool → Load from URL** and paste:

```
https://raw.githubusercontent.com/Corruptsector/nautobot-tool-server/master/nautobot_ow_tool_serverless.py
```

### Manual

Go to **Workspace → Tools → + Add Tool** and paste in the contents of `nautobot_ow_tool_serverless.py`.

### Configure Valves

After adding the tool, click the gear icon and set:

| Valve | Description |
|-------|-------------|
| `NAUTOBOT_URL` | Base URL of your Nautobot instance (e.g. `http://nautobot:8080`) |
| `NAUTOBOT_TOKEN` | Nautobot API token |
| `PROMETHEUS_URL` | Base URL of Prometheus (e.g. `http://prometheus:9090`) |

### Enable in chat

Click the **+** button in the chat input area and toggle the Nautobot tool on. The LLM will then call tool functions automatically based on your questions.

## Tool methods

| Method | Description |
|--------|-------------|
| `list_tenants(limit, offset)` | List all tenants — paginated |
| `list_locations(type, tenant)` | List locations. Filter by type (Site/Region/Country) or tenant |
| `list_devices(tenant, location, role, limit, offset)` | List devices — paginated, at least one filter required |
| `get_device(name)` | Full detail on a single device by hostname |
| `get_device_interfaces(name)` | List all interfaces on a device with assigned IPs |
| `get_inventory_summary()` | Total counts of devices, tenants, locations |
| `get_tenant_summary(tenant)` | Device breakdown for a tenant by role, device type, and location |
| `list_prefixes(tenant, limit, offset)` | List IPAM prefixes, optionally filtered by tenant |
| `get_device_metrics(name)` | Live Prometheus metrics for a device by hostname |
| `get_metrics_by_location(location, role, tenant)` | Metrics for a device at a location — resolves hostname automatically |
| `get_device_creation_context(tenant)` | All context needed before creating a device |
| `create_device(name, tenant, location, role, device_type, ...)` | Full device creation workflow |

## Example questions

- *"Give me a breakdown of the devices used by Kiwibank"* → `get_tenant_summary`
- *"List all devices for ANZ at Auckland CBD"* → `list_devices`
- *"What are the metrics for the router at Kiwibank Wellington?"* → `get_metrics_by_location`
- *"What interfaces does anz-akl-cbd-rtr-01 have?"* → `get_device_interfaces`
- *"Create an access switch for Kiwibank in Wellington"* → `get_device_creation_context` then `create_device`

## Device creation

`create_device` handles the full workflow in one call — no UUIDs required:

```
name, tenant, location, role, device_type
  → resolves all names to IDs
  → creates device type + manufacturer if missing
  → creates IPAM prefix if missing
  → creates management IP, assigns to mgmt0, sets as primary_ip4
```

Call `get_device_creation_context(tenant=...)` first to discover valid names.

## Nautobot version notes

Tested against **Nautobot 2.3**. Key API differences from 1.x:

- Device roles are at `/api/extras/roles/` (not `/api/dcim/roles/`)
- IP-to-interface assignment uses `POST /api/ipam/ip-address-to-interface/`
- IP addresses require a namespace (`Global` used by default)
- Interfaces require `status: Active` on creation
- Locations are not tenant-tagged — tenant locations are derived from device assignments

## Dependencies

Python standard library only (`urllib`, `json`, `ipaddress`). No `pip install` required — works in any Open WebUI container.
