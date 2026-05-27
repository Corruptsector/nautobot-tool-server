# Nautobot Tool Server

A lightweight Python HTTP server that wraps the [Nautobot](https://nautobot.readthedocs.io/) network inventory REST API and Prometheus into a single clean JSON interface. Built to be registered as a tool inside [Open WebUI](https://openwebui.com/) so an LLM can query and manage live network inventory and device telemetry during a conversation.

## How it works

```
Open WebUI (LLM tool call)
        │
        ▼
nautobot-tool-server  (:8000)
        ├──▶  Nautobot REST API  (:8080)   — inventory, devices, tenants, locations
        └──▶  Prometheus HTTP API (:9090)  — live device metrics
```

The server has no database of its own — every request proxies live data from Nautobot or Prometheus, flattens the responses into concise JSON, and returns them.

## Files

| File | Description |
|------|-------------|
| `server.py` | The tool server — run this |
| `nautobot_ow_tool.py` | Open WebUI Python function tool — proxies through `server.py` |
| `nautobot_ow_tool_serverless.py` | Open WebUI Python function tool — talks directly to Nautobot, no server needed |
| `register_ow_tool.py` | One-shot script to register the tool into Open WebUI |
| `docker-compose.yml` | Runs the tool server as a container |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/devices` | List devices. Filters: `location`, `tenant`, `role`, `name`, `limit` |
| `GET` | `/devices/<name>` | Single device detail by exact hostname |
| `GET` | `/tenants` | List all tenants |
| `GET` | `/locations` | List all locations. Optional filter: `type` (e.g. `Site`, `Region`) |
| `GET` | `/summary` | Total counts — devices, tenants, locations |
| `GET` | `/metrics/device/<name>` | Live Prometheus metrics for a device by hostname |
| `GET` | `/device-creation-context` | All data needed to create a device. Optional filter: `?tenant=<name>` |
| `POST` | `/devices` | Create a device — full workflow in one call |

### Location filtering

The `location` filter on `/devices` accepts partial names — `?location=Auckland CBD` will match `ANZ Bank New Zealand Auckland CBD`, `BNZ Auckland CBD`, etc. The server resolves partial names to location IDs via Nautobot's fuzzy `q=` search before filtering.

### Device creation

`GET /device-creation-context` returns everything the LLM needs before creating a device:
- Tenants (filtered by optional `?tenant=` query)
- Locations for each tenant (derived from existing device assignments)
- Device types already used by the tenant (shown as suggestions)
- All available device types and manufacturers
- IPAM prefixes, namespaces, and device roles
- A workflow guide pointing at `POST /devices`

`POST /devices` handles the full creation workflow in a single call — no UUIDs required, just names:

```json
{
  "name": "anz-auckland-sw01",
  "tenant": "ANZ Bank New Zealand",
  "location": "Auckland CBD",
  "role": "Access Switch",
  "device_type": "Catalyst 9300",
  "status": "Active",
  "management_ip": "10.1.1.1/24"
}
```

The server will:
1. Resolve all names → IDs
2. Create the device type + manufacturer if they don't exist
3. Create the covering IPAM prefix if it doesn't exist
4. Create the management IP address
5. Create a `mgmt0` interface on the device
6. Assign the IP to the interface via `ipam/ip-address-to-interface/`
7. Set the IP as the device's `primary_ip4`

`manufacturer` is only required if `device_type` doesn't already exist. `management_ip` is optional.

### Example responses

**`GET /devices?tenant=Kiwibank&location=Wellington`**
```json
{
  "count": 2,
  "returned": 2,
  "offset": 0,
  "has_more": false,
  "devices": [
    {
      "name": "kwb-wlg-cen-rtr-01",
      "status": "Active",
      "location": "Kiwibank Wellington Central",
      "tenant": "Kiwibank",
      "role": "Core Router",
      "device_type": "Cisco ASR 1001-X",
      "platform": null,
      "primary_ip": null
    }
  ]
}
```

**`POST /devices`**
```json
{
  "device": {
    "id": "43e2d788-...",
    "name": "anz-auckland-sw01",
    "status": "Active",
    "tenant": "ANZ Bank New Zealand",
    "location": "APAC → New Zealand → ANZ Bank New Zealand Auckland CBD",
    "role": "Access Switch",
    "device_type": "Catalyst 9300"
  },
  "management_ip": {
    "id": "d6ec64af-...",
    "address": "10.1.1.1/24",
    "interface": "mgmt0",
    "prefix": "10.1.1.0/24"
  }
}
```

**`GET /metrics/device/kwb-wlg-cen-rtr-01`**
```json
{
  "device": "kwb-wlg-cen-rtr-01",
  "metrics": {
    "cpu_percent": 34.7,
    "memory_percent": 51.2,
    "up": 1.0,
    "uptime_seconds": 1209600,
    "bgp_peers": 4,
    "tenant": "Kiwibank",
    "location": "Kiwibank Wellington Central",
    "role": "Core Router",
    "interfaces_rx_mbps": { "GigabitEthernet0/0": 312.4 },
    "interfaces_tx_mbps": { "GigabitEthernet0/0": 145.7 }
  }
}
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NAUTOBOT_URL` | `http://nautobot:8080` | Nautobot base URL |
| `NAUTOBOT_TOKEN` | *(dev token)* | Nautobot API token |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus base URL |
| `PORT` | `8000` | Port this server listens on |

## Running

### Docker Compose (recommended)

A `docker-compose.yml` is included. Set your environment variables and start:

```bash
NAUTOBOT_URL=http://your-nautobot:8080 \
NAUTOBOT_TOKEN=your-token-here \
PROMETHEUS_URL=http://your-prometheus:9090 \
docker-compose up -d
```

Or create a `.env` file:

```env
NAUTOBOT_URL=http://your-nautobot:8080
NAUTOBOT_TOKEN=your-token-here
PROMETHEUS_URL=http://your-prometheus:9090
PORT=8000
```

Then just `docker-compose up -d`. The server listens on port 8000 by default.

`server.py` is bind-mounted read-only — changes take effect with `docker-compose restart`, no rebuild needed.

### Local development

```bash
pip install requests
NAUTOBOT_URL=http://localhost:8080 NAUTOBOT_TOKEN=your-token python server.py
```

## Registering as an Open WebUI tool

### Automatic (recommended)

Run `register_ow_tool.py` once — it waits for Open WebUI to be healthy, creates the admin account on first run, and registers the tool. Safe to re-run (skips if already registered).

```bash
pip install requests
OW_URL=http://localhost:4000 \
OW_ADMIN_EMAIL=admin@example.com \
OW_ADMIN_PASSWORD=yourpassword \
python register_ow_tool.py
```

Or as a Docker Compose one-shot service:

```yaml
openwebui-setup:
  image: networktocode/nautobot:2.3-py3.11
  entrypoint: ["python"]
  command: ["/scripts/register_ow_tool.py"]
  volumes:
    - ./:/scripts:ro
  environment:
    OW_URL: http://open-webui:8080
    OW_ADMIN_EMAIL: admin@example.com
    OW_ADMIN_PASSWORD: yourpassword
  depends_on:
    open-webui:
      condition: service_healthy
  restart: "no"
```

### Manual

There are two tool files — choose one:

| File | When to use |
|------|-------------|
| `nautobot_ow_tool.py` | You're running `server.py` (or the Docker Compose service) |
| `nautobot_ow_tool_serverless.py` | You want Open WebUI to talk directly to Nautobot — no tool server needed |

Go to **Open WebUI → Workspace → Tools → + Add Tool** and paste in the contents of your chosen file.

For the serverless variant, set the **Valves** (`NAUTOBOT_URL`, `NAUTOBOT_TOKEN`, `PROMETHEUS_URL`) after adding the tool — these appear as editable fields in the tool's settings page.

## Open WebUI tool methods

Once registered, the LLM has access to these 8 functions:

| Method | Description |
|--------|-------------|
| `list_tenants()` | List all tenants — call this first to get exact names |
| `list_locations(type)` | List locations, optionally filtered by type |
| `list_devices(tenant, location, role)` | List devices — at least one filter required |
| `get_device(name)` | Full detail on a single device by hostname |
| `get_inventory_summary()` | Total counts of devices, tenants, locations |
| `get_device_metrics(name)` | Live CPU, memory, uptime, BGP peers, interface Mbps |
| `get_device_creation_context(tenant)` | All context needed before creating a device |
| `create_device(name, tenant, location, role, device_type, ...)` | Full device creation workflow |

## Nautobot version notes

Tested against **Nautobot 2.3**. Key API differences from Nautobot 1.x baked into the server:

- Device roles are at `/api/extras/roles/` (not `/api/dcim/roles/`)
- IP-to-interface assignment uses `POST /api/ipam/ip-address-to-interface/` (not a PATCH with `assigned_object_type`)
- IP addresses require a namespace (use the `Global` namespace if not using VRFs)
- Interfaces require `status: Active` on creation
- Locations are not tenant-tagged — tenant's locations are derived from device assignments

## Dependencies

- Python 3.8+
- [`requests`](https://pypi.org/project/requests/) — the only third-party dependency

The `networktocode/nautobot:2.3-py3.11` Docker image already includes `requests`.
