# Agent-first loop backlog

Each iteration takes the first unchecked item, ships it with tests, then ticks it
here. If an item is blocked, move to the next one and note why.

## Guardrails

- Read `AGENTS.md` before changing anything.
- Keep `pint`, `php artisan test`, and `npm run build` green.
- Never invent device data or provenance. Measurements need a source.
- Do not run migrations or database writes without the user's approval.
- One item per iteration, small and reviewable.

## Backlog

- [x] **HTTP ingestion for remote agents.** `UpsertDeviceProfileUseCase` is
  shared by `agent:ingest-device` and `POST /api/v1/agent/devices`
  (`auth.agent`, 201/200/409/413/422). Tests in
  `tests/Feature/Api/AgentDeviceIngestionTest.php`.
- [ ] **Device search across aliases and board codes.** Confirm the device
  repository matches aliases and `board_identifier`, not only brand and model.
- [x] **Sync script for local apps.** `agent-kit/scripts/sync-device.sh` accepts
  one payload or a directory, checks JSON, POSTs to `/api/v1/agent/devices`, and
  prints the response. `DRY_RUN=1` shows what would be sent.
- [ ] **Provenance in the device UI.** Show the stored provenance sources on
  `/devices/{slug}` with a "last verified" date, so technicians can judge a
  record before trusting it.
- [ ] **Cache invalidation coverage.** Add a test that an agent write bumps
  `catalog:version` and that the next catalog read reflects the change.
- [ ] **Payload abuse tests.** Oversized payload, unknown fields, duplicate
  hardware codes, cross-device slug conflicts, and a 422 body that names the
  offending field.
- [ ] **Zero-data UI states.** A device with no variants, no specs, and no
  testpoints should still read well on `/devices` and `/devices/{slug}`.
- [x] **A105F live run.** `agent-kit/data/samsung-galaxy-a10.json`
  ingested; the record is corrected to Galaxy A10 (SM-A105F) with verified
  specs, regional variants, known issues, and provenance.
- [ ] **A10s (SM-A107F) record.** Galaxy A10s was previously conflated with
  SM-A105F; it is a separate Helio P22 device and still needs its own payload.

## Blocked on the user

- Database migration + A105F ingestion (done):
  `php artisan agent:ingest-device agent-kit/data/samsung-galaxy-a10.json`
