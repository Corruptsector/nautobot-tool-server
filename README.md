# Nautobot Tool Server

A lightweight Python HTTP server that wraps the [Nautobot](https://nautobot.readthedocs.io/) network inventory REST API and Prometheus into a single clean JSON interface. Built to be registered as a tool inside [Open WebUI](https://openwebui.com/) so an LLM can query live network inventory and device telemetry during a conversation.

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

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/devices` | List devices. Optional query params: `location`, `tenant`, `role`, `name`, `limit` |
| `GET` | `/devices/<name>` | Single device detail by exact hostname |
| `GET` | `/tenants` | List all tenants |
| `GET` | `/locations` | List all locations. Optional query param: `type` (e.g. `Site`, `Region`) |
| `GET` | `/summary` | Total counts — devices, tenants, locations |
| `GET` | `/metrics/device/<name>` | Live Prometheus metrics for a device by hostname |

### Location filtering

The `location` filter on `/devices` accepts partial names — `?location=Auckland CBD` will match `ANZ Bank New Zealand Auckland CBD`, `BNZ Auckland CBD`, etc. The server resolves partial names to location IDs via Nautobot's fuzzy `q=` search before filtering.

### Example responses

**`GET /devices?tenant=Kiwibank&location=Wellington`**
```json
{
  "count": 2,
  "devices": [
    {
      "name": "kwb-wlg-cen-rtr-01",
      "status": "Active",
      "location": "Kiwibank Wellington Central",
      "tenant": "Kiwibank",
      "role": "Core Router",
      "device_type": "Cisco ASR 1001-X",
      "platform": "Cisco IOS-XE",
      "primary_ip": null
    }
  ]
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
    "interfaces_rx_mbps": {
      "GigabitEthernet0/0": 312.4,
      "GigabitEthernet0/1": 89.1
    },
    "interfaces_tx_mbps": {
      "GigabitEthernet0/0": 145.7,
      "GigabitEthernet0/1": 42.3
    }
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

### Docker

```bash
docker build -t nautobot-tool-server .
docker run -p 8000:8000 \
  -e NAUTOBOT_URL=http://your-nautobot:8080 \
  -e NAUTOBOT_TOKEN=your-token-here \
  -e PROMETHEUS_URL=http://your-prometheus:9090 \
  nautobot-tool-server
```

### Docker Compose (alongside Nautobot)

```yaml
nautobot-tool-server:
  build: ./nautobot-tool-server
  environment:
    NAUTOBOT_URL: http://nautobot:8080
    NAUTOBOT_TOKEN: your-token-here
    PROMETHEUS_URL: http://prometheus:9090
    PORT: "8000"
  depends_on:
    nautobot:
      condition: service_healthy
```

### Local development

```bash
pip install requests
NAUTOBOT_URL=http://localhost:8080 NAUTOBOT_TOKEN=your-token python server.py
```

## Registering as an Open WebUI tool

The companion tool definition for Open WebUI lives in [`nautobot_ow_tool.py`](../scripts/nautobot_ow_tool.py). Register it via the Open WebUI admin UI or the API:

```bash
curl -X POST http://your-openwebui/api/v1/tools/create \
  -H "Authorization: Bearer $OW_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "nautobot",
    "name": "Nautobot",
    "meta": {"description": "Query Nautobot network inventory and live device metrics"},
    "content": "<contents of nautobot_ow_tool.py>"
  }'
```

## Dependencies

- Python 3.8+
- [`requests`](https://pypi.org/project/requests/) — the only third-party dependency

The base Docker image (`networktocode/nautobot:2.3-py3.11`) already includes `requests`, so no extra `pip install` step is needed.
