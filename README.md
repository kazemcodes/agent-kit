# FixIndex agent kit

Everything an external agent needs to read the hardware catalog, or to add a
device with its variants and testpoints.

Any agent runtime works: Codex, Claude, a cron job, or a local sync app. The
contract is JSON in, JSON out. No scraping of the HTML site, no database access.

## 1. Read the catalog (HTTP, cached)

Base URL: `https://your-fixindex-host/api/v1`

| Endpoint | Purpose |
| --- | --- |
| `GET /devices?q=&brand=` | Search devices by model, brand, alias, or board code. |
| `GET /devices/{slug}` | Device profile with variants, specs, and testpoints. |
| `GET /testpoints?q=&mode=&device_id=` | Pinouts with readings and safety notes. |
| `GET /testpoints/{slug}` | One pinout. |
| `GET /error-codes?q=&platform=` | Error code dictionary. |
| `GET /error-codes/{code}` | One error code with diagnostic steps. |
| `GET /repairs?query=&brand=&device_id=&limit=` | Published repair cases. |

Responses carry `ETag` and `Cache-Control`. Send `If-None-Match` on repeat
reads and expect `304` when nothing changed. Public reads are limited to 120
requests per minute per IP.

```bash
curl -s "https://your-fixindex-host/api/v1/devices?q=SM-A105F" \
  -H "Accept: application/json"
```

## 2. Write a device (idempotent)

The payload schema is in [`schema/device-payload.schema.json`](schema/device-payload.schema.json).
A complete example is in [`data/samsung-galaxy-a10.json`](data/samsung-galaxy-a10.json).

### Remote agents: HTTP

```bash
curl -s -X POST "https://your-fixindex-host/api/v1/agent/devices"   -H "Authorization: Bearer $FIXINDEX_TOKEN"   -H "Content-Type: application/json"   -H "Accept: application/json"   --data @agent-kit/data/samsung-galaxy-a10.json
```

`201` on create, `200` on update, `409` when a hardware code or testpoint slug
belongs to another device, `422` for a validation error, `413` above 512 KB.
Requires a JWT with the agent role or an API key.

### Host operators: CLI

```bash
# Validate only
php artisan agent:ingest-device agent-kit/data/samsung-galaxy-a10.json --dry-run

# Write
php artisan agent:ingest-device agent-kit/data/samsung-galaxy-a10.json

# From a pipe
cat payload.json | php artisan agent:ingest-device -
```

The command prints one JSON object:

```json
{
  "status": "ok",
  "dry_run": false,
  "device": { "id": "dev-samsung-galaxy-a10", "slug": "samsung-galaxy-a10", "action": "create" },
  "variants": 5,
  "testpoints": 0,
  "provenance": 2
}
```

Provenance is merged, never pruned: a URL you drop from a payload stays on the
record. Remove a wrong citation deliberately (it is the site's evidence trail)
rather than expecting the next ingest to drop it.

Rules:

- **Idempotent.** Devices match by `slug`, variants by `hardware_code`,
  testpoints by `slug`. Running the same file twice changes nothing.
- **Partial updates.** Fields you omit keep their stored value. Send
  `specs` as an object; nested keys you provide replace the stored object.
- **Provenance is mandatory.** At least one `provenance[].url` is required.
  Cite a datasheet, a service manual, a schematic page, or a specs page you
  actually read. Never invent measurements.
- **No silent overwrite across devices.** A hardware code or testpoint slug
  already owned by another device is rejected.
- Unknown top-level keys are ignored; unknown keys inside `specs` are stored.

## 3. What to collect for a new device

1. Marketing name and family (`brand`, `model_family`).
2. Platform (`Android` or `iOS`), release year.
3. Board identifier or service code (Samsung `SM-...`, Apple `820-...`).
4. Regional variants: hardware code, market, chipset.
5. Specs: SoC, display, RAM, storage, battery.
6. Testpoints only if you have a real source for the pad location, the mode
   (EDL / ISP / DFU), and the expected reading. Otherwise leave the array empty.
7. At least one provenance URL per claim group.

## 4. Local sync

[`scripts/sync-device.sh`](scripts/sync-device.sh) pushes one payload, or every
`.json` file in a folder, to a running FixIndex instance. It needs only bash and
curl; Python is used for a JSON sanity check when present.

```bash
FIXINDEX_URL=https://fixindex.example FIXINDEX_TOKEN=fx_live_... ./agent-kit/scripts/sync-device.sh agent-kit/data/

# Validate without sending
DRY_RUN=1 ./agent-kit/scripts/sync-device.sh agent-kit/data/
```

A local app can poll the API, diff against its own store, and post a payload
back through this script. Keep it one-way per field, and always send a stable
`device.slug` when updating an existing device.

## 5. MCP smoke client

[`mcp-client.php`](mcp-client.php) calls the remote MCP server directly, so you
can check what an AI client actually receives:

```bash
php artisan serve            # in another terminal

php agent-kit/mcp-client.php tools/list
php agent-kit/mcp-client.php search_devices '{"query":"SM-A105F"}'
php agent-kit/mcp-client.php get_device_detail '{"identifier":"samsung-galaxy-a10"}'
php agent-kit/mcp-client.php lookup_error_code '{"code":"4013"}'
```

Set `FIXINDEX_MCP_URL` to point at another host. Every tool argument must be
sent - a missing required argument is reported by the tool's own message.

## 6. Safety

- Do not scrape the HTML pages for data you can read from the API.
- Do not write credentials into payloads or logs.
- Keep payloads under 512 KB.
