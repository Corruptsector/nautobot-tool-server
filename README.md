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

In Open WebUI go to **Workspace → Tools → + Add Tool → Load from URL** and paste the raw file URL:

```
https://raw.githubusercontent.com/Corruptsector/nautobot-tool-server/master/nautobot_ow_tool_serverless.py
```

> Raw file: https://raw.githubusercontent.com/Corruptsector/nautobot-tool-server/master/nautobot_ow_tool_serverless.py

### Manual

Go to **Workspace → Tools → + Add Tool** and paste in the contents of `nautobot_ow_tool_serverless.py`.

### Configure Valves

After adding the tool, click the gear icon and set:

| Valve | Default | Description |
|-------|---------|-------------|
| `NAUTOBOT_URL` | `http://nautobot:8080` | Base URL of your Nautobot instance |
| `NAUTOBOT_TOKEN` | *(empty)* | Nautobot API token |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Base URL of Prometheus |
| `PROMETHEUS_DEVICE_LABEL` | `device` | Label used to identify devices in Prometheus. Set to `instance` or `exported_instance` for SNMP exporter |
| `GRAFANA_URL` | *(empty)* | Base URL of Grafana (e.g. `http://grafana:3000`). When set, metric responses include clickable Explore links |
| `GRAFANA_DATASOURCE` | `prometheus` | Grafana datasource name as it appears in Grafana → Data Sources |

### Enable in chat

Click the **+** button in the chat input area and toggle the Nautobot tool on. The LLM will then call tool functions automatically based on your questions.

## Tool methods

| Method | Description |
|--------|-------------|
| `list_tenants(limit, offset)` | List all tenants — paginated |
| `list_locations(type, tenant)` | List locations. Filter by type (Site/Region/Country) or tenant |
| `list_devices(tenant, location, role, limit, offset)` | List devices — paginated, at least one filter required |
| `get_device(name)` | Full detail on a single device by hostname — includes `monitoring` custom field |
| `get_device_interfaces(name)` | List all interfaces on a device with assigned IPs |
| `get_inventory_summary()` | Total counts of devices, tenants, locations |
| `get_tenant_summary(tenant)` | Device breakdown for a tenant by role, device type, and location |
| `list_prefixes(tenant, limit, offset)` | List IPAM prefixes, optionally filtered by tenant |
| `get_device_metrics(name)` | Live Prometheus metrics for a device by hostname |
| `get_metrics_by_location(location, role, tenant)` | Metrics for a device at a location — resolves hostname automatically |
| `get_available_metrics(name)` | Discover all metric series for a device — includes Grafana Explore links if `GRAFANA_URL` is set |
| `get_metric_grafana_link(device, metric)` | Generate a Grafana Explore link for a specific metric and device |
| `get_device_creation_context(tenant)` | All context needed before creating a device |
| `create_device(name, tenant, location, role, device_type, ...)` | Full device creation workflow — sets `monitoring` custom field (default: `if_mib`) |

## Example questions

- *"Give me a breakdown of the devices used by Kiwibank"* → `get_tenant_summary`
- *"List all devices for ANZ at Auckland CBD"* → `list_devices`
- *"What are the metrics for the router at Kiwibank Wellington?"* → `get_metrics_by_location`
- *"What interfaces does anz-akl-cbd-rtr-01 have?"* → `get_device_interfaces`
- *"What metrics are available for anz-akl-cbd-rtr-01?"* → `get_available_metrics` (returns metric list + Grafana links)
- *"Give me a Grafana link for CPU on anz-akl-cbd-rtr-01"* → `get_metric_grafana_link`
- *"Create an access switch for Kiwibank in Wellington"* → `get_device_creation_context` then `create_device`

## Device creation

`create_device` handles the full workflow in one call — no UUIDs required:

```
name, tenant, location, role, device_type
  → resolves all names to IDs
  → creates device type + manufacturer if missing
  → creates IPAM prefix if missing
  → creates management IP, assigns to mgmt0, sets as primary_ip4
  → sets monitoring custom field (default: if_mib)
```

Call `get_device_creation_context(tenant=...)` first to discover valid names.

Optional parameters: `manufacturer`, `status` (default: `Active`), `management_ip` (CIDR), `monitoring` (default: `if_mib`).

## Nautobot version notes

Tested against **Nautobot 2.3**. Key API differences from 1.x:

- Device roles are at `/api/extras/roles/` (not `/api/dcim/roles/`)
- IP-to-interface assignment uses `POST /api/ipam/ip-address-to-interface/`
- IP addresses require a namespace (`Global` used by default)
- Interfaces require `status: Active` on creation
- Locations are not tenant-tagged — tenant locations are derived from device assignments

## Dependencies

Python standard library only (`urllib`, `json`, `ipaddress`). No `pip install` required — works in any Open WebUI container.
