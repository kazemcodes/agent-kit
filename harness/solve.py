"""FixIndex Autonomous Hardware Diagnostic & Research Harness CLI.

Usage:
    python agent-kit/harness/solve.py "Xiaomi Poco F3 نه میکروفن و نه صدا کار میکنه" --ingest
    python agent-kit/harness/solve.py "Samsung Galaxy A52 not charging" --engine hermes --ingest
    python agent-kit/harness/solve.py "iPhone 13 Pro error 4013" --dry-run --save-payload out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
CRAWLERS_DIR = HARNESS_DIR.parent / "crawlers"
sys.path.insert(0, str(HARNESS_DIR))
sys.path.insert(0, str(CRAWLERS_DIR))

from collectors import gather_all_evidence  # noqa: E402
from common import FixIndexClient, load_config  # noqa: E402
from extractor import format_report, synthesize_solution  # noqa: E402
from query_parser import parse_query  # noqa: E402


def _load_client(config_path: str | None, dry_run: bool) -> FixIndexClient | None:
    cfg_file = Path(config_path) if config_path else CRAWLERS_DIR / "config.json"
    if not cfg_file.exists():
        return None
    cfg = load_config(str(cfg_file))
    fx = cfg.get("fixindex") or {}
    base_url = fx.get("base_url") or "http://127.0.0.1:8000"
    api_key = fx.get("api_key") or ""
    return FixIndexClient(base_url, api_key, dry_run=dry_run)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="FixIndex Autonomous Hardware Diagnostic Harness")
    parser.add_argument("query", nargs="?", default="", help="Symptom or fault description (English or Persian)")
    parser.add_argument("--device", help="Explicit device name (e.g. 'Xiaomi Poco F3')")
    parser.add_argument("--symptom", help="Explicit symptom override")
    parser.add_argument("--config", help="Path to crawlers config.json")
    parser.add_argument(
        "--engine",
        default="auto",
        choices=["auto", "hermes", "claude", "codex", "openai", "ollama", "deterministic"],
        help="Synthesis reasoning engine (default: auto)",
    )
    parser.add_argument("--model", help="Optional model override for Hermes/OpenAI/Ollama")
    parser.add_argument("--deep", action="store_true", help="Allow multi-turn Hermes verification (with --engine hermes)")
    parser.add_argument("--ingest", action="store_true", help="Upsert device dossier and submit draft repair case to FixIndex")
    parser.add_argument("--dry-run", action="store_true", help="Preview payloads without writing to FixIndex")
    parser.add_argument("--save-payload", help="Save generated device payload JSON to a file")
    parser.add_argument("--skip-youtube", action="store_true", help="Skip YouTube yt-dlp collector for faster web-only run")
    parser.add_argument("--max-pages", type=int, default=8, help="Maximum web research pages to fetch (default: 8)")
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    args = parser.parse_args()

    raw_query = (args.query or "").strip()
    if not raw_query and args.device:
        raw_query = f"{args.device} {args.symptom or ''}".strip()
    if not raw_query:
        parser.error("Provide a diagnostic query or --device.")

    parsed = parse_query(raw_query, explicit_device=args.device, explicit_symptom=args.symptom)
    client = _load_client(args.config, dry_run=args.dry_run)

    print(
        f"[harness] Target: {parsed.brand} {parsed.model_family} ({parsed.slug}) | "
        f"Faults: {', '.join(parsed.fault_domains)} | Engine: {args.engine}",
        file=sys.stderr,
    )

    bundle = gather_all_evidence(
        parsed,
        client=client,
        max_pages=max(1, args.max_pages),
        skip_youtube=args.skip_youtube,
    )

    allow_llm = args.engine != "deterministic"
    result = synthesize_solution(
        bundle,
        allow_llm=allow_llm,
        engine=args.engine,
        model=args.model,
        deep=args.deep,
    )

    if args.save_payload:
        out_path = Path(args.save_payload)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result["device_payload"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[saved] {out_path}", file=sys.stderr)

    if args.ingest:
        if not client:
            print("[error] Cannot --ingest without agent-kit/crawlers/config.json (missing API key).", file=sys.stderr)
            return 2

        dev_payload = result["device_payload"]
        dev_obj = dev_payload["device"]
        enrich_resp = client.enrich_device(
            device=dev_obj,
            sections=dev_obj["profile"],
            provenance=dev_payload["provenance"],
            variants=dev_payload.get("variants"),
            testpoints=dev_payload.get("testpoints"),
        )
        sub = result["repair_submission"]
        sub_resp = client.submit_case(
            device=dev_obj,
            title=sub["title"],
            text=sub["solution"]["description"],
            source_url=sub["provenance_sources"][0]["url"],
            source_title=sub["provenance_sources"][0].get("title", "Harness Source"),
            key_parts=("harness-case", parsed.slug, *parsed.fault_domains),
            language=sub.get("language", "en"),
            symptoms=sub.get("symptoms"),
            error_codes=sub.get("error_codes"),
            diagnostic_steps=sub.get("diagnostic_steps"),
            required_tools=sub["solution"].get("required_tools"),
            safety_hazards=sub["solution"].get("safety_hazards"),
            provenance_sources=sub.get("provenance_sources"),
            outcome=sub["solution"].get("outcome", "REPORTED_EXPERIENCE"),
        )

        mcp_verify = None
        if not args.dry_run:
            mcp_verify = client.mcp_call("diagnose_symptom", {"query": parsed.raw_query})

        result["ingestion"] = {
            "device": enrich_resp,
            "repair_submission": sub_resp,
            "mcp_verification_ok": bool(mcp_verify and not (mcp_verify.get("result") or {}).get("isError")),
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))
        if result.get("ingestion"):
            print("\n=== FixIndex Ingestion Summary ===")
            print(json.dumps(result["ingestion"], ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
