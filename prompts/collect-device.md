# Task: collect and ingest a device profile

You are filling one device record for FixIndex. Work from primary sources, cite
them, and never invent a measurement.

## Inputs

- Device: Samsung Galaxy A10 (`SM-A105F`) — not the Galaxy A10s (`SM-A107F`)
- Payload: `agent-kit/data/samsung-galaxy-a10.json`
- Schema: `agent-kit/schema/device-payload.schema.json`

## Steps

1. Read the current payload and the schema.
2. Verify each spec against a page you can open: marketing name, release year,
   chipset, display, memory, battery, regional model codes.
3. Replace or confirm the two provenance URLs. Add one entry per source you
   actually used. Do not keep a URL you did not open.
4. Add `testpoints` only if a source shows the pad location and the mode. If
   none does, leave the array empty.
5. Validate:
   `php artisan agent:ingest-device agent-kit/data/samsung-galaxy-a10.json --dry-run`
6. Ingest:
   `php artisan agent:ingest-device agent-kit/data/samsung-galaxy-a10.json`
7. Check the result:
   `curl -s http://127.0.0.1:8123/api/v1/devices/samsung-galaxy-a10`
   and open `/devices/samsung-galaxy-a10`.

## Output

Report: what you verified, what you could not verify, which URLs you kept,
whether any testpoint was added, and the ingest JSON summary.
