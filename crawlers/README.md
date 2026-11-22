# FixIndex Crawler Agents

Local collection agents that fill the knowledge base with real repair data:
**Telegram technician groups (client session, not a bot)**, **YouTube repair
videos**, and **repair forums/blogs**. Everything they collect enters through
the authenticated ingestion API and lands in the **human review queue** —
nothing is ever auto-published, and customer PII is scrubbed before it leaves
your machine.

```
 Telegram groups ─┐
 YouTube search  ─┼─► crawlers (this dir) ─► POST /api/v1/agent/* ─► Review Queue ─► Published
 Repair forums   ─┘        │                        (Bearer fx_live_ key)      /review
                     PII scrub + dedup
                     local ledger (.crawl_ledger.db)
```

## What each crawler produces

| Crawler | Output | Where it shows up |
|---|---|---|
| `telegram/` | Community field reports (`profile.community`) + full draft cases when a message contains fault **and** fix language | Device page → "From the repair community"; `/review` |
| `youtube/` | Video metadata cards (`profile.media`, kind `youtube`): thumbnail, channel, duration, views, topic, language | Device page → "Video solutions" with in-page player |
| `web/` | Draft cases extracted from forum/blog articles (robots.txt respected) | `/review` → published articles |

The server merges list sections by identity (video id / URL / connector+pin),
so repeated crawls **accumulate** data instead of overwriting it.

## Setup

Requires Python 3.10+.

```bash
cd agent-kit/crawlers
cp config.example.json config.json        # then edit config.json

# install only the crawlers you want
pip install -r telegram/requirements.txt
pip install -r youtube/requirements.txt
pip install -r web/requirements.txt
```

### 1. FixIndex API key
Create a key with the `contribute:agent` ability (Admin → API Keys, or your
dashboard with the write scope checked) and put it in `config.json →
fixindex.api_key`. `base_url` is your running FixIndex instance.

### 2. Telegram (client, not bot)
1. Get `api_id` / `api_hash` from https://my.telegram.org → API development tools.
2. Put them plus your phone number in `config.json → telegram`.
3. List the groups to crawl (`@username`, invite link, or numeric id).
   **Your account must already be a member** — the crawler only reads what
   you can read.
4. First run asks for the login code once and stores a `.session` file.
   Keep that file private; it is a full account session.

> ⚠️ Use your own account responsibly: automated reading can trigger
> Telegram flood limits (handled with FloodWait backoff here) and, if abused,
> account restrictions. Crawl slowly, only groups you belong to, and respect
> each group's rules and local law. Do not redistribute private group
> content beyond what your review team approves for publication.

### 3. YouTube
No credentials needed — `yt-dlp` reads public search metadata. No videos are
downloaded or rehosted; FixIndex only indexes titles/thumbnails/URLs.

### 4. Web
Add seed URLs per device in `config.json → web.seeds`. robots.txt is checked
for every origin; disallowed origins are skipped.

## Run

```bash
# ALWAYS preview first — prints payloads, submits nothing:
python telegram/crawl_telegram.py --dry-run --limit 50
python youtube/crawl_youtube.py --dry-run --device samsung-galaxy-a14
python web/crawl_web.py --dry-run

# real runs:
python telegram/crawl_telegram.py --limit 300
python youtube/crawl_youtube.py
python web/crawl_web.py
```

Flags: `--config <path>`, `--device <slug>`, `--limit N` (Telegram), `--dry-run`.

## Safety model (defense in depth)

1. **Local**: PII scrubber (IMEI / serial / phone), injection & prohibited-content
   filter, sqlite dedup ledger, rate limiting, robots.txt.
2. **Server**: `contribute:agent` ability required; `ContentSafetyService`
   re-checks every payload (422 on violations); provenance URLs mandatory;
   idempotency keys prevent duplicate drafts.
3. **Human**: all agent content is quarantined in `/review` until a reviewer
   approves it. Community quotes are labeled unverified in the UI.

## Extending

- New device: add an entry to `config.json → devices` (brand, model_family,
  platform, slug matching the catalog, aliases/keywords incl. Persian forms).
- New dossier section: extend `DevicePayloadRules` + `_MERGE_KEYS` in
  `common.py`, then render it in `resources/js/Pages/Devices/Show.svelte`.
- Diode tables / IC crossref / firmware entries can be pushed by any agent
  with the same `enrich_device()` helper (`profile.diode_tables`,
  `profile.ic_crossref`, `profile.firmware`, `profile.schematics`).
