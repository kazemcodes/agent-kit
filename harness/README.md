# FixIndex Autonomous Repair Harness (`agent-kit/harness`)

Bilingual (English + Persian) research and diagnostic harness that resolves mobile repair faults when FixIndex does not yet contain a case, and feeds grounded, provenance-backed dossiers back into FixIndex.

## Architecture

1. **`query_parser.py`**: Parses English, Persian, or mixed queries (e.g. `"Xiaomi Poco F3 نه میکروفن و نه صدا کار میکنه"`), detects brand/model/codename/siblings (`Poco F3` -> `Redmi K40` / `Mi 11X` / `alioth` / `M2012K11AG`), classifies fault domains, and builds multi-angle search queries.
2. **`collectors.py`**: Checks FixIndex `GET /api/v1/diagnose` first, then gathers live evidence from Wikipedia API, iFixit API v2, DuckDuckGo technician forums (`reddit`, `xdaforums`, `ifixit`, `gsmhosting`, `badcaps`, `xiaomi.eu`), and YouTube repair walkthroughs via `yt-dlp`. Runs `PIIScrubber` on every excerpt.
3. **`extractor.py`**: Extracts verified IC part numbers (`WCD9385`, `WSA8830`, `PM8250`, `SM8250`, etc.), rails/buses, and repair themes strictly from fetched sources (never fabricating measurements). Synthesizes ranked failure modes via deterministic grounded trees or optional pluggable LLM/agent engines (`hermes`, `claude`, `codex`, `openai`, `ollama`).
4. **`solve.py`**: CLI runner supporting `--ingest`, `--dry-run`, `--save-payload`, `--engine`, and `--json`.

## Standalone Usage

```powershell
# Research + synthesize + ingest into FixIndex (device dossier + review-queue case)
.\agent-kit\crawlers\.venv\Scripts\python.exe agent-kit/harness/solve.py "Xiaomi Poco F3 نه میکروفن و نه صدا کار میکنه" --ingest

# Dry-run with JSON output saved to disk
.\agent-kit\crawlers\.venv\Scripts\python.exe agent-kit/harness/solve.py "Samsung Galaxy A52 not charging" --dry-run --save-payload agent-kit/data/samsung-galaxy-a52.json
```

## Hybrid Integration with Hermes Agent

Combining the FixIndex Harness (fast deterministic multi-source scraping, IC extraction, schema validation, PII scrubbing, idempotent ingestion) with **Hermes Agent** gives you the best of both worlds:

### 1. Harness -> Hermes Reasoning (`--engine hermes`)
The harness collects and scrubs live Wikipedia, iFixit, forum, and YouTube evidence, then delegates synthesis to Hermes and validates Hermes's JSON strictly against FixIndex schema & provenance rules:

```powershell
.\agent-kit\crawlers\.venv\Scripts\python.exe agent-kit/harness/solve.py "Xiaomi Poco F3 نه میکروفن و نه صدا کار میکنه" --engine hermes --ingest
```

Add `--deep` to allow Hermes multi-turn verification across tools before writing back.

### 2. Hermes -> FixIndex MCP + Repo Skill (Interactive / Telegram / Vision)
Connect Hermes directly to FixIndex's 11-tool MCP server and trust the repo skill at `.agents/skills/fixindex-repair/SKILL.md`:

```powershell
# Streamable HTTP MCP endpoint
hermes mcp add fixindex --url http://127.0.0.1:8000/api/v1/mcp --auth header

# Or local stdio bridge with API key in env
hermes mcp add fixindex --command node --env FIXINDEX_URL=http://127.0.0.1:8000 FIXINDEX_API_KEY=fx_live_YOUR_KEY --args bin/fixindex-mcp.cjs

# Trust the repo-local FixIndex skill
hermes skills trust
```

With `--image board_photo.jpg` or via Hermes's Telegram gateway, Hermes can inspect microscope PCB photos, query FixIndex via `diagnose_symptom`, invoke `solve.py --ingest` when a device is missing, and return grounded Persian/English bench instructions.
