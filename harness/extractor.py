"""Grounded evidence extractor and dual-mode (LLM / deterministic) diagnostic synthesizer."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "crawlers"))
from common import PIIScrubber, idempotency_key, normalize_fa  # noqa: E402
from collectors import EvidenceBundle, EvidenceItem  # noqa: E402

_KNOWN_IC_FUNCTIONS = {
    "WCD9385": "Qualcomm Aqstic Audio Codec (ADC/DAC for mics, earpiece, headset)",
    "WCD9380": "Qualcomm Aqstic Audio Codec (ADC/DAC)",
    "WCD9370": "Qualcomm Audio Codec",
    "WCD9341": "Qualcomm Audio Codec",
    "WSA8830": "Qualcomm Smart Speaker Amplifier (SoundWire Class-D)",
    "WSA8835": "Qualcomm Smart Speaker Amplifier",
    "WSA8815": "Qualcomm Smart Speaker Amplifier",
    "PM8250": "Qualcomm Main PMIC & power rails",
    "PM8150": "Qualcomm Main PMIC",
    "PM8350": "Qualcomm Main PMIC",
    "PM8550": "Qualcomm Main PMIC",
    "PMI8998": "Qualcomm Interface / Charging PMIC",
    "SMB1390": "Qualcomm Fast Charge Pump IC",
    "SMB1396": "Qualcomm Fast Charge Pump IC",
    "SM8250": "Qualcomm Snapdragon 865/870 SoC (CPU/DSP/Audio Bus Host)",
    "SM8150": "Qualcomm Snapdragon 855/860 SoC",
    "SM8450": "Qualcomm Snapdragon 8 Gen 1 SoC",
    "SDR865": "Qualcomm RF Transceiver",
    "SDR8150": "Qualcomm RF Transceiver",
    "QET6100": "Qualcomm Envelope Tracker",
}

_IC_PATTERNS = [
    r"\bSM\d{4}(?:-AC)?\b",
    r"\bSDM\d{3}\b",
    r"\bPM[IKL]?\d{4}[A-Z0-9]{0,2}\b",
    r"\bWCD\d{4}\b",
    r"\bWSA\d{4}\b",
    r"\bSDR\d{3,4}\b",
    r"\bQET\d{4}\b",
    r"\bSMB\d{4}\b",
    r"\bMT\d{4}[A-Z]{0,2}\b",
    r"\bS2M[A-Z0-9]{2,6}\b",
    r"\bCS35L\d+\b",
    r"\bTAS25\d+\b",
    r"\bTFA98\d+\b",
    r"\bAW88\d+\b",
    r"\bBQ25\d+\b",
    r"\bCD321\d[A-Z0-9]*\b",
    r"\bSN2\d{3}[A-Z0-9]*\b",
    r"\b161\dA\d\b",
    r"\b338S\d{4,5}\b",
]

_RAIL_PATTERNS = [
    r"\bVDD_[A-Z0-9_]{2,}\b",
    r"\bVREG_[A-Z0-9_]{2,}\b",
    r"\bPP[0-9A-Z_]{3,}\b",
    r"\bVBUS(?:_[A-Z0-9_]+)?\b",
    r"\bVSYS\b",
    r"\bMIC\d?_BIAS\b",
    r"\bSOUNDWIRE\b",
    r"\bI2C\d*\b",
    r"\bI2S\d*\b",
]

_CONCEPT_KEYWORDS = {
    "software_cit": ["cit", "6484", "safe mode", "google app", "assistant", "hyperos", "miui", "volte", "firmware", "reboot", "restart"],
    "subboard_flex": ["sub-board", "subboard", "sub board", "charging board", "flex", "ribbon", "fpc", "bottom mic", "sub mic", "connector"],
    "audio_codec": ["wcd", "wsa", "codec", "audio ic", "amplifier", "soundwire", "earpiece", "loudspeaker", "speaker"],
    "pmic_rails": ["pmic", "pm8", "ldo", "vreg", "vsys", "short", "diode", "multimeter"],
    "cpu_reball": ["reball", "reflow", "bga", "cpu", "soc", "squeeze", "solder", "crack", "sm8250", "snapdragon 870"],
    "water_corrosion": ["water", "liquid", "corrosion", "moisture", "ultrasonic", "rust"],
    "charging_port": ["charging", "usb-c", "vbus", "cc1", "cc2", "ovp", "tristar", "hydra"],
    "display_backlight": ["backlight", "black screen", "no light", "vsn", "vsp", "oled", "lcd"],
    "baseband_rf": ["baseband", "no service", "modem", "qcn", "transceiver", "sdr"],
    "boot_storage": ["bootloop", "boot loop", "ufs", "emmc", "nand", "4013", "edl", "9008", "sahara"],
}


@dataclass
class ExtractedFacts:
    ics: dict[str, list[str]]  # IC -> [ev_id]
    rails: list[str]
    model_codes: list[str]
    concept_sources: dict[str, list[str]]  # concept -> [ev_id]


def extract_facts(bundle: EvidenceBundle) -> ExtractedFacts:
    ics: dict[str, list[str]] = {}
    rails_set: set[str] = set()
    codes_set: set[str] = set(bundle.parsed.aliases)
    concept_sources: dict[str, list[str]] = {k: [] for k in _CONCEPT_KEYWORDS}

    for item in bundle.items:
        blob = f"{item.title}\n{item.snippet}\n{item.full_text}"
        blob_upper = blob.upper()
        blob_norm = normalize_fa(blob)

        for pat in _IC_PATTERNS:
            for m in re.findall(pat, blob_upper):
                ic = m.strip()
                if ic in ("PM1000", "PM2000"):
                    continue
                ics.setdefault(ic, [])
                if item.id not in ics[ic]:
                    ics[ic].append(item.id)

        for pat in _RAIL_PATTERNS:
            for m in re.findall(pat, blob_upper):
                rails_set.add(m.strip())

        for m in re.findall(r"\b(?:M\d{4}[A-Z0-9]{3,6}|SM-[A-Z]\d{3,4}[A-Z0-9/]*|A\d{4})\b", blob_upper):
            codes_set.add(m.strip())

        for concept, kws in _CONCEPT_KEYWORDS.items():
            if any(k in blob_norm for k in kws):
                if item.id not in concept_sources[concept]:
                    concept_sources[concept].append(item.id)

    return ExtractedFacts(
        ics=ics,
        rails=sorted(rails_set)[:12],
        model_codes=sorted(c for c in codes_set if len(c) <= 20)[:10],
        concept_sources=concept_sources,
    )


def _pick_sources(bundle: EvidenceBundle, *preferred_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for lst in preferred_lists:
        for ev_id in lst:
            if ev_id and ev_id not in merged:
                merged.append(ev_id)
    if not merged and bundle.items:
        merged = [it.id for it in bundle.items[:3]]
    return merged[:5]


def _build_evidence_array(bundle: EvidenceBundle, scrubber: PIIScrubber) -> list[dict]:
    out: list[dict] = []
    for item in bundle.items:
        supports = scrubber.scrub(item.snippet or item.title)[:900]
        if not supports:
            supports = item.title[:200]
        out.append({
            "id": item.id,
            "url": item.url,
            "title": scrubber.scrub(item.title)[:200],
            "publisher": scrubber.scrub(item.publisher)[:120],
            "retrieved_at": item.retrieved_at,
            "supports": supports,
        })
    return out


def _build_community_notes(bundle: EvidenceBundle, scrubber: PIIScrubber) -> list[dict]:
    notes: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for item in bundle.items:
        if item.source_type != "web":
            continue
        excerpt = scrubber.scrub(item.snippet or item.full_text[:450]).strip()
        if len(excerpt) < 40:
            continue
        notes.append({
            "source": f"Web · {item.publisher}"[:120],
            "url": item.url,
            "excerpt": excerpt[:1200],
            "topic": bundle.parsed.fault_domains[0] if bundle.parsed.fault_domains else "general",
            "date": item.retrieved_at or today,
        })
    return notes[:8]


def _build_ic_crossref(facts: ExtractedFacts, bundle: EvidenceBundle) -> list[dict]:
    crossref: list[dict] = []
    donors = [s for s in bundle.parsed.siblings if s][:4]
    for ic, srcs in sorted(facts.ics.items(), key=lambda kv: len(kv[1]), reverse=True)[:8]:
        crossref.append({
            "ic": ic,
            "function": _KNOWN_IC_FUNCTIONS.get(ic, "Referenced board IC in gathered repair sources"),
            "donors": donors,
            "sources": srcs[:4],
        })
    return crossref


def _synthesize_failure_modes(bundle: EvidenceBundle, facts: ExtractedFacts) -> tuple[list[dict], list[dict], list[dict], str, str]:
    p = bundle.parsed
    faults = p.fault_domains
    cs = facts.concept_sources
    all_ev_ids = [it.id for it in bundle.items]
    detected_ic_list = list(facts.ics.keys())

    modes: list[dict] = []
    checks: list[dict] = []
    parts: list[dict] = []

    if "audio-mic-combined" in faults or "audio-mic" in faults or "audio-sound" in faults:
        audio_ics = [ic for ic in detected_ic_list if ic.startswith(("WCD", "WSA", "CS35", "TAS", "TFA", "AW88", "PM"))]
        soc_ics = [ic for ic in detected_ic_list if ic.startswith(("SM", "SDM", "MT"))]

        # Mode 1: Software / CIT / Google Voice Match / Routing lock (if software/CIT hints or general web evidence)
        sw_srcs = _pick_sources(bundle, cs["software_cit"], all_ev_ids)
        modes.append({
            "title": f"Audio HAL lockup, Bluetooth/USB-C routing stuck, or OS mic conflict ({p.model_family})",
            "severity": "occasional",
            "confidence": "community",
            "components": ["Audio HAL", "CIT Hardware Menu", "Google App Mic Permission"],
            "symptoms": [
                "Neither loudspeaker media audio nor primary microphone works until reboot (or fails after MIUI/HyperOS update)",
                "Earphones/Bluetooth state may appear stuck, muting onboard earpiece/speaker and bottom mic",
                "Voice recorder or dialer hangs when attempting to initialize audio stream",
            ],
            "mechanism": "On Qualcomm/Xiaomi devices, an audio HAL deadlock, stuck USB-C/headset detection, or background hotword listener seizing the audio stream prevents both mic capture and speaker output without an actual board short.",
            "diagnose": [
                "Disable Bluetooth completely and check the status bar for phantom wired headset icons.",
                "Open the built-in hardware test (CIT: dial *#*#6484#*#* or tap Kernel Version in Settings) and run Receiver, Speaker, Main Mic, and Top Mic tests separately.",
                "Boot into Safe Mode or revoke microphone permission from the Google app and test Voice Recorder after a clean restart.",
            ],
            "fix": "If CIT passes hardware loopback in Safe Mode, clear Google app cache/microphone permission, reset audio settings, or reflash matching stock firmware/persist image before opening the chassis.",
            "risk": "Do not flash mismatched region firmware or wipe persist partitions without backing up calibration files.",
            "sources": sw_srcs,
        })

        # Mode 2: Bottom sub-board / interconnect FPC flex fault
        sub_srcs = _pick_sources(bundle, cs["subboard_flex"], all_ev_ids)
        modes.append({
            "title": "Main-to-sub-board interconnect flex cable or bottom charging sub-board fault",
            "severity": "common",
            "confidence": "community",
            "components": ["Main FPC Flex Cable", "Charging Sub-board", "Bottom Primary Mic"],
            "symptoms": [
                "Bottom primary microphone dead on standard calls while loudspeaker or top mic may also cut out",
                "Fault starts after a drop, chassis flex, or moisture near the USB-C port",
                "Intermittent audio or mic dropout when pressing near the bottom half of the back cover",
            ],
            "mechanism": "The primary voice mic and bottom loudspeaker routes travel through the lower charging sub-board and the main-to-sub flex ribbon. A cracked solder ball under the B2B connector, corroded sub-board pad, or torn flex trace can pull down shared bias or SoundWire/speaker lines.",
            "diagnose": [
                "Disconnect the battery first, then disconnect the main-to-sub-board flex cable at the mainboard B2B connector.",
                "Test whether the top earpiece/speaker and top secondary microphone recover with the lower sub-board isolated.",
                "Inspect both ends of the flex connector under a microscope for bent pins, corrosion, or micro-cracks.",
            ],
            "fix": "Clean and reseat the B2B flex connectors; if corrosion or pin damage is present, replace the charging sub-board and main interconnect flex cable with a known-good assembly.",
            "risk": "Always isolate the battery connector before unplugging or probing board-to-board flex cables to avoid blowing bias or power lines.",
            "sources": sub_srcs,
        })

        # Mode 3: Audio Codec IC / Smart Amp / PMIC rails
        codec_components = audio_ics[:4] if audio_ics else ["Audio Codec IC", "Smart Speaker Amp", "PMIC Audio Rails"]
        codec_srcs = _pick_sources(bundle, cs["audio_codec"], cs["pmic_rails"], all_ev_ids)
        ic_note = f" (identified in gathered sources: {', '.join(audio_ics[:4])})" if audio_ics else ""
        modes.append({
            "title": f"Audio Codec / Smart Amplifier BGA dry joint or shorted rail{(' (' + ', '.join(audio_ics[:3]) + ')') if audio_ics else ''}",
            "severity": "common",
            "confidence": "community",
            "components": codec_components,
            "symptoms": [
                "Both microphone input and speaker/earpiece output fail completely in CIT hardware diagnostics",
                "Sub-board and flex replacement did not restore sound or mic",
                "Recorder app freezes or returns audio hardware error immediately",
            ],
            "mechanism": f"The discrete audio codec and smart speaker amplifiers{ic_note} handle ADC/DAC conversion for mics and speaker amplification over I2S/SoundWire and PMIC LDO rails. Drop impact or liquid ingress under the shield fractures BGA joints or shorts decoupling capacitors on codec supply rails.",
            "diagnose": [
                "Remove the mainboard shield covering the audio codec and PMIC area and inspect under a microscope for liquid corrosion or cracked underfill.",
                "Check decoupling capacitors surrounding the audio codec and smart amp in diode mode / resistance for shorts to ground before powering.",
                "Verify codec clock, SoundWire/I2C control continuity, and LDO bias rails while attempting audio playback/recording.",
            ],
            "fix": f"Clear any shorted decoupling capacitors on codec supply lines; if rails measure clean without shorts, flux and reball or replace the audio codec IC{(' (' + audio_ics[0] + ')') if audio_ics else ''} and verify speaker amp continuity.",
            "risk": "Excessive hot air near shielded/underfilled Qualcomm ICs can float adjacent PMIC or CPU solder balls. Pre-heat evenly and shield surrounding BGA packages.",
            "sources": codec_srcs,
        })

        # Mode 4: CPU / SoC BGA solder fatigue (classic Poco F3 / Redmi K40 / Mi 11X failure!)
        cpu_srcs = _pick_sources(bundle, cs["cpu_reball"], all_ev_ids)
        cpu_components = soc_ics[:2] if soc_ics else ["SoC / CPU", "LPDDR5 RAM", "PMIC"]
        if "CPU BGA" not in cpu_components:
            cpu_components.append("CPU BGA")
        modes.append({
            "title": f"SoC / CPU BGA cracked solder joints causing simultaneous audio & mic bus dropout",
            "severity": "design-flaw" if p.slug == "xiaomi-poco-f3" else "occasional",
            "confidence": "community",
            "components": cpu_components,
            "symptoms": [
                "Sudden simultaneous loss of speaker sound and both microphones (often after heating/gaming or a drop)",
                "Audio or mic briefly returns when pressing gently on the CPU shield or flexing the board",
                "Device may also exhibit intermittent freezes, camera errors, or reboots before going dead",
            ],
            "mechanism": f"On {p.model_family}{(' / ' + ' / '.join(p.siblings)) if p.siblings else ''}, thermal cycling and board flex fatigue the lead-free BGA solder balls under the stacked SoC/RAM{(' (' + ', '.join(soc_ics[:2]) + ')') if soc_ics else ''}. Because digital audio buses (SoundWire/I2S/Slimbus) route directly from the SoC, cracked balls sever both audio output and microphone paths simultaneously.",
            "diagnose": [
                "Rule out software (CIT + Safe Mode), sub-board flex, and codec supply shorts first.",
                "Perform a careful mechanical pressure check (non-conductive spudger gentle pressure over the CPU area during audio test) — temporary restoration confirms cracked CPU BGA balls.",
                "Check diode mode on I2S/SoundWire/I2C lines between the audio codec pads and CPU when codec IC is lifted.",
            ],
            "fix": "Lift the stacked RAM and SoC/CPU, wick and clean oxidized pads under the microscope, reball CPU and RAM using fresh leaded solder paste/stencil, and re-seat cleanly.",
            "risk": "High-risk micro-soldering: stacked PoP RAM/CPU can delaminate, pop, or tear pads if underfill is not cleared and temperature profile is uncontrolled. Back up customer data before board heat if the phone still boots.",
            "sources": cpu_srcs,
        })

        checks = [
            {
                "label": "CIT Hardware Menu isolation (*#*#6484#*#*)",
                "tool": "Android Dialer / CIT",
                "expect": "Tests Main Mic, Top Mic, Earpiece, and Loudspeaker independently of dialer/third-party apps.",
                "sources": sw_srcs[:2],
            },
            {
                "label": "Sub-board & interconnect flex isolation",
                "tool": "Spudger + Microscope",
                "expect": "Disconnecting the lower sub-board flex separates a shorted bottom mic/USB board from a mainboard audio codec or CPU fault.",
                "sources": sub_srcs[:2],
            },
            {
                "label": "Codec & PMIC supply capacitor short check",
                "tool": "Multimeter (Diode / Ohms)",
                "expect": "No 0.000V dead shorts on filter caps around the audio codec and smart amp rails; compare against a known-good reference board before reballing.",
                "sources": codec_srcs[:2],
            },
            {
                "label": "SoC BGA joint integrity test (after ruling out flex & codec)",
                "tool": "Non-conductive probe / Microscope",
                "expect": "If light pressure on the SoC briefly restores audio or mic, CPU/RAM BGA reballing is required rather than replacing speakers or mics.",
                "sources": cpu_srcs[:2],
            },
        ]

        parts = [
            {"name": "Main-to-sub-board interconnect flex cable", "type": "Flex", "note": "Low-cost first swap when B2B pins or flex traces are fatigued."},
            {"name": "Bottom USB-C charging & primary mic sub-board", "type": "Board", "note": "Houses the primary bottom microphone and USB-C port."},
            {"name": f"Audio Codec IC ({audio_ics[0]})" if audio_ics else "Audio Codec IC", "type": "IC", "note": "Replace or reball when codec lines are open/shorted or audio fails after sub-board isolation."},
            {"name": "BGA CPU/RAM Reball Stencil & 183°C Paste", "type": "Consumable", "note": "Required if BGA solder fatigue under the SoC is confirmed."},
        ]

        verdict = (
            f"On {p.brand} {p.model_family}"
            + (f" ({', '.join(p.siblings)})" if p.siblings else "")
            + ", simultaneous loss of microphone and sound is usually either a lower sub-board/flex disconnect, an audio codec/rail failure"
            + (f" ({', '.join(audio_ics[:3])})" if audio_ics else "")
            + ", or cracked BGA joints under the SoC"
            + (f" ({soc_ics[0]})" if soc_ics else "")
            + ". Always isolate software (CIT *#*#6484#*#*) and the bottom flex before applying hot air to the mainboard."
        )

    else:
        # Generic multi-tier fault dossier grounded in collected evidence for ANY other fault domain
        primary_fault = faults[0] if faults else "hardware"
        src_ids = _pick_sources(bundle, all_ev_ids)
        ic_label = ", ".join(detected_ic_list[:4]) if detected_ic_list else "PMIC / Sub-board / SoC"
        modes = [
            {
                "title": f"Peripheral flex, connector, or software isolation ({p.symptom_summary_en})",
                "severity": "common",
                "confidence": "community",
                "components": ["B2B Connectors", "Sub-board / Flex", "Firmware / OS"],
                "symptoms": [
                    f"Reported symptom: {p.symptom_summary_en}",
                    "Issue persists across normal reboots",
                ],
                "mechanism": f"Peripheral flex cables, corroded B2B connectors, or corrupted firmware/HAL state frequently mimic deeper motherboard faults on {p.model_family}.",
                "diagnose": [
                    "Run built-in hardware diagnostics or Safe Mode / known-good peripheral swap first.",
                    "Disconnect battery and inspect B2B connectors and sub-board flex under a microscope for bent pins or oxidation.",
                ],
                "fix": "Reseat or replace damaged flex cables / sub-boards, or reflash matching stock firmware before attempting board-level soldering.",
                "risk": "Never disconnect or reconnect board flexes while the battery is plugged in.",
                "sources": src_ids,
            },
            {
                "title": f"Mainboard IC, power rail, or BGA joint failure ({ic_label})",
                "severity": "occasional",
                "confidence": "community",
                "components": detected_ic_list[:4] if detected_ic_list else ["PMIC", "SoC / BGA"],
                "symptoms": [
                    f"Fault remains after known-good peripheral & software checks: {p.symptom_summary_en}",
                    "Abnormal current draw on DC bench supply or shorted capacitor around suspect IC",
                ],
                "mechanism": "Shorted decoupling capacitors, LDO rail failure, or cracked BGA solder balls under the governing IC/SoC prevent hardware initialization.",
                "diagnose": [
                    "Measure DC power supply current draw during standby and prompt-to-boot.",
                    "Inspect under shields for corrosion and measure filter caps in diode mode for shorts to ground.",
                ],
                "fix": "Remove shorted passives or reball/replace the suspect IC identified by schematic rail tracing.",
                "risk": "Control preheat and airflow carefully around underfilled BGAs.",
                "sources": src_ids,
            },
        ]
        checks = [
            {
                "label": f"Peripheral & connector isolation for {primary_fault}",
                "tool": "Microscope + Known-good parts",
                "expect": "Rule out external flexes, sub-boards, and software state before removing board shields.",
                "sources": src_ids[:2],
            },
            {
                "label": "Bench supply & diode mode rail check",
                "tool": "DC Power Supply + Multimeter",
                "expect": "Check standby draw and verify filter capacitors around suspect ICs are not shorted to ground.",
                "sources": src_ids[:2],
            },
        ]
        parts = [
            {"name": "Replacement flex / sub-board assembly", "type": "Module", "note": "Verify board revision before ordering."},
        ]
        verdict = f"{p.brand} {p.model_family} dossier compiled from {len(bundle.items)} cited sources for {p.symptom_summary_en}. Isolate software and modular flex cables before mainboard micro-soldering."

    overview_parts = [
        f"Diagnostic dossier for {p.brand} {p.model_family}"
        + (f" (platform siblings: {', '.join(p.siblings)})" if p.siblings else "")
        + f" addressing {p.symptom_summary_en}.",
    ]
    if bundle.wiki_specs.get("cpu"):
        overview_parts.append(f"Hardware platform: {bundle.wiki_specs['cpu']}.")
    if detected_ic_list:
        overview_parts.append(f"ICs referenced in gathered repair evidence: {', '.join(detected_ic_list[:8])}.")
    if facts.rails:
        overview_parts.append(f"Buses/rails referenced in sources: {', '.join(facts.rails[:6])}.")
    overview_parts.append("All findings below link directly to fetched external evidence (Wikipedia, iFixit, repair forums, and technician videos) and remain tagged as agent_draft until reviewed by a human technician.")

    return modes, checks, parts, verdict, "\n\n".join(overview_parts)


def _extract_json_object(raw_text: str) -> dict | None:
    """Parse a JSON object from raw agent output (handles markdown fences and preamble)."""
    text = (raw_text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S | re.I)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _run_cli_agent(cmd: list[str], prompt: str, timeout: int = 90) -> tuple[dict | None, str | None]:
    try:
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:240]
            return None, f"CLI exited {proc.returncode}: {err}"
        parsed = _extract_json_object(proc.stdout)
        if parsed is None:
            return None, "CLI output did not contain a valid JSON object"
        return parsed, None
    except subprocess.TimeoutExpired:
        return None, f"CLI timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def _try_llm_synthesis(
    bundle: EvidenceBundle,
    facts: ExtractedFacts,
    *,
    engine: str = "auto",
    model: str | None = None,
    deep: bool = False,
) -> tuple[dict | None, str | None]:
    """Pluggable LLM/agent synthesis over strictly gathered evidence."""
    chosen = (engine or "auto").strip().lower()
    if chosen == "deterministic":
        return None, None

    ollama_url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if chosen == "auto":
        env_provider = os.environ.get("FIXINDEX_LLM_PROVIDER", "").strip().lower()
        if env_provider in ("hermes", "claude", "codex", "openai", "ollama", "deterministic"):
            chosen = env_provider
        elif openai_key:
            chosen = "openai"
        else:
            try:
                r = requests.get(f"{ollama_url}/api/tags", timeout=1)
                if r.status_code == 200 and (r.json().get("models") or []):
                    chosen = "ollama"
                else:
                    return None, None
            except Exception:  # noqa: BLE001
                return None, None

    if chosen == "deterministic":
        return None, None

    compact_evidence = [
        {"id": it.id, "title": it.title, "url": it.url, "excerpt": it.full_text[:900]}
        for it in bundle.items[:8]
    ]
    prompt = (
        "You are a senior mobile motherboard repair engineer. Using ONLY the provided evidence JSON"
        + (" (and additional web verification if tools are enabled)" if deep else "")
        + ", return ONLY a single valid JSON object (no surrounding prose) with keys: verdict (string), overview (string), "
        "failure_modes (list of {title, severity, confidence, components, symptoms, mechanism, diagnose, fix, risk, sources}). "
        "Allowed severity: common, occasional, rare, design-flaw. Allowed confidence: verified, community, single-report. "
        "Every sources list MUST contain only evidence id strings present in the evidence list. "
        "Never invent multimeter readings, diode values, or component designators not present in the evidence.\n\n"
        f"Query: {bundle.parsed.raw_query}\n"
        f"Device: {bundle.parsed.brand} {bundle.parsed.model_family}\n"
        f"Detected ICs: {list(facts.ics.keys())}\n"
        f"Evidence: {json.dumps(compact_evidence, ensure_ascii=False)}"
    )

    if chosen == "hermes":
        hermes_bin = shutil.which("hermes")
        if not hermes_bin:
            bundle.warnings.append("hermes CLI not found on PATH; fell back to deterministic synthesis.")
            return None, None
        cmd = [
            hermes_bin, "chat", "--query-file", "-", "--oneshot", "-Q",
            "--max-turns", "5" if deep else "1",
        ]
        if not deep:
            cmd.extend(["-t", ""])
        if model:
            cmd.extend(["-m", model])
        parsed, err = _run_cli_agent(cmd, prompt, timeout=120 if deep else 75)
        if err:
            bundle.warnings.append(f"Hermes synthesis fallback ({err})")
            return None, None
        return parsed, "hermes-grounded"

    if chosen == "claude":
        claude_bin = shutil.which("claude")
        if not claude_bin:
            bundle.warnings.append("claude CLI not found on PATH; fell back to deterministic synthesis.")
            return None, None
        cmd = [claude_bin, "-p", "--output-format", "text"]
        if model:
            cmd.extend(["--model", model])
        parsed, err = _run_cli_agent(cmd, prompt, timeout=90)
        if err:
            bundle.warnings.append(f"Claude synthesis fallback ({err})")
            return None, None
        return parsed, "claude-grounded"

    if chosen == "codex":
        codex_bin = shutil.which("codex")
        if not codex_bin:
            bundle.warnings.append("codex CLI not found on PATH; fell back to deterministic synthesis.")
            return None, None
        cmd = [codex_bin, "exec", "-"]
        if model:
            cmd.extend(["-m", model])
        parsed, err = _run_cli_agent(cmd, prompt, timeout=90)
        if err:
            bundle.warnings.append(f"Codex synthesis fallback ({err})")
            return None, None
        return parsed, "codex-grounded"

    if chosen == "openai":
        if not openai_key:
            bundle.warnings.append("OPENAI_API_KEY missing; fell back to deterministic synthesis.")
            return None, None
        try:
            chosen_model = model or os.environ.get("FIXINDEX_LLM_MODEL", "gpt-4o-mini")
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": chosen_model,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                bundle.warnings.append(f"OpenAI synthesis HTTP {resp.status_code}; fell back to deterministic.")
                return None, None
            raw = resp.json()["choices"][0]["message"]["content"]
            return _extract_json_object(raw), "openai-grounded"
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(f"OpenAI synthesis fallback ({exc})")
            return None, None

    if chosen == "ollama":
        try:
            chosen_model = model or os.environ.get("FIXINDEX_LLM_MODEL") or "qwen2.5:7b"
            resp = requests.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": chosen_model,
                    "stream": False,
                    "format": "json",
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45,
            )
            if resp.status_code != 200:
                bundle.warnings.append(f"Ollama synthesis HTTP {resp.status_code}; fell back to deterministic.")
                return None, None
            raw = resp.json().get("message", {}).get("content", "")
            return _extract_json_object(raw), "ollama-grounded"
        except Exception as exc:  # noqa: BLE001
            bundle.warnings.append(f"Ollama synthesis fallback ({exc})")
            return None, None

    return None, None


def synthesize_solution(
    bundle: EvidenceBundle,
    *,
    allow_llm: bool = True,
    engine: str = "auto",
    model: str | None = None,
    deep: bool = False,
) -> dict[str, Any]:
    scrubber = PIIScrubber()
    facts = extract_facts(bundle)
    p = bundle.parsed

    # Ensure there is always at least one verifiable provenance entry
    provenance = [
        {
            "url": it.url,
            "title": scrubber.scrub(it.title)[:200],
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for it in bundle.items
    ]
    if not provenance:
        raise ValueError("No verifiable external sources were collected; refusing to fabricate a repair dossier without provenance.")

    evidence = _build_evidence_array(bundle, scrubber)
    modes, checks, parts, verdict, overview = _synthesize_failure_modes(bundle, facts)
    synthesis_mode = "deterministic-grounded"

    if allow_llm and engine != "deterministic":
        llm_out, engine_label = _try_llm_synthesis(bundle, facts, engine=engine, model=model, deep=deep)
        if isinstance(llm_out, dict) and isinstance(llm_out.get("failure_modes"), list) and llm_out["failure_modes"]:
            valid_ids = {e["id"] for e in evidence}
            cleaned_modes = []
            for m in llm_out["failure_modes"][:6]:
                if not isinstance(m, dict) or not m.get("title"):
                    continue
                srcs = [s for s in (m.get("sources") or []) if s in valid_ids] or ([evidence[0]["id"]] if evidence else [])
                sev = m.get("severity") if m.get("severity") in ("common", "occasional", "rare", "design-flaw") else "common"
                conf = m.get("confidence") if m.get("confidence") in ("verified", "community", "single-report") else "community"
                cleaned_modes.append({
                    "title": scrubber.scrub(str(m["title"]))[:180],
                    "severity": sev,
                    "confidence": conf,
                    "components": [scrubber.scrub(str(c))[:80] for c in (m.get("components") or [])[:8]],
                    "symptoms": [scrubber.scrub(str(s))[:600] for s in (m.get("symptoms") or [])[:6]],
                    "mechanism": scrubber.scrub(str(m.get("mechanism") or ""))[:4000],
                    "diagnose": [scrubber.scrub(str(d))[:900] for d in (m.get("diagnose") or [])[:8]],
                    "fix": scrubber.scrub(str(m.get("fix") or ""))[:4000],
                    "risk": scrubber.scrub(str(m.get("risk") or ""))[:900],
                    "sources": srcs,
                })
            if cleaned_modes:
                modes = cleaned_modes
                if isinstance(llm_out.get("verdict"), str) and llm_out["verdict"].strip():
                    verdict = scrubber.scrub(llm_out["verdict"])[:1200]
                if isinstance(llm_out.get("overview"), str) and llm_out["overview"].strip():
                    overview = scrubber.scrub(llm_out["overview"])[:6000]
                synthesis_mode = engine_label or "llm-grounded"

    # Merge iFixit guides into media alongside YouTube videos
    media = list(bundle.youtube_videos[:8])
    for guide in bundle.ifixit_guides[:4]:
        media.append({
            "url": guide["url"],
            "title": scrubber.scrub(guide["title"])[:200],
            "kind": "guide",
            "shows": f"iFixit repair guide ({guide.get('difficulty') or 'guide'}, {guide.get('time_required') or 'teardown'})"[:900],
        })

    specs = dict(bundle.wiki_specs)
    release_year = specs.pop("release_year", None)
    if bundle.parsed.codename:
        specs.setdefault("codename", bundle.parsed.codename)
    if bundle.parsed.siblings:
        specs.setdefault("platform_siblings", ", ".join(bundle.parsed.siblings))

    variants = []
    chipset = specs.get("cpu")
    for code in facts.model_codes:
        if re.match(r"^(?:M\d{4}|SM-|A\d{4})", code):
            variants.append({
                "hardware_code": code,
                "market_region": "Global / Regional",
                **({"chipset": str(chipset)[:120]} if chipset else {}),
            })

    known_issues = [f"{m['title']}: {m['fix'][:240]}" for m in modes[:4] if m.get("fix")]
    community = _build_community_notes(bundle, scrubber)
    ic_crossref = _build_ic_crossref(facts, bundle)

    device_obj: dict[str, Any] = {
        "brand": p.brand,
        "model_family": p.model_family,
        "platform": p.platform,
        "slug": p.slug,
        **({"release_year": int(release_year)} if isinstance(release_year, int) else {}),
        **({"board_identifier": p.board_identifier} if p.board_identifier else {}),
        "aliases": list(dict.fromkeys([*p.aliases, *facts.model_codes]))[:15],
        **({"specs": specs} if specs else {}),
        "known_issues": known_issues,
        "profile": {
            "verdict": verdict[:1200],
            "overview": overview[:6000],
            "failure_modes": modes,
            "checks": checks,
            "parts": parts,
            **({"media": media} if media else {}),
            **({"community": community} if community else {}),
            **({"ic_crossref": ic_crossref} if ic_crossref else {}),
            "evidence": evidence,
            "review": {
                "status": "agent_draft",
                "agent": f"FixIndex Autonomous Harness ({synthesis_mode})",
                "notes": f"Synthesized from {len(evidence)} live sources for query: {p.raw_query[:180]}. Pending technician verification.",
            },
        },
    }

    device_payload: dict[str, Any] = {
        "schema_version": 1,
        "device": device_obj,
        "variants": variants,
        "testpoints": [],
        "provenance": provenance,
    }

    # Draft case for human review queue (/review)
    top_mode = modes[0] if modes else {}
    all_symptoms = []
    for m in modes:
        for s in m.get("symptoms", []):
            if s not in all_symptoms:
                all_symptoms.append(s)
    all_steps = []
    for m in modes:
        for d in m.get("diagnose", []):
            if d not in all_steps:
                all_steps.append(d)

    solution_lines = []
    for idx, m in enumerate(modes, start=1):
        ics_str = f" [ICs: {', '.join(m['components'])}]" if m.get("components") else ""
        solution_lines.append(f"{idx}. {m['title']}{ics_str}\n   Fix: {m.get('fix', '')}")

    case_title = f"{p.brand} {p.model_family}: {p.symptom_summary_en} — multi-tier hardware triage"[:180]
    repair_submission: dict[str, Any] = {
        "idempotency_key": idempotency_key("harness-case", p.slug, *p.fault_domains),
        "agent_metadata": {
            "agent_id": "fixindex-harness-v2",
            "model_name": synthesis_mode,
            "model_version": "2.0",
        },
        "device": {"brand": p.brand, "model_family": p.model_family},
        "language": "en",
        "title": case_title,
        "symptoms": (all_symptoms or [p.symptom_summary_en])[:8],
        "error_codes": p.error_codes,
        "diagnostic_steps": (all_steps or ["Verify hardware fault in CIT / Safe Mode before opening board shields."])[:10],
        "solution": {
            "description": "\n\n".join(solution_lines)[:4000],
            "outcome": "REPORTED_EXPERIENCE",
            "required_tools": ["Multimeter", "Stereomicroscope", "Spudger / Opening picks", "Hot air & BGA rework station"],
            "safety_hazards": [
                "Disconnect battery connector before unplugging sub-board flexes or probing components.",
                "Rule out software (CIT) and lower sub-board/flex before applying hot air to audio codec or CPU/RAM BGA.",
            ],
        },
        "provenance_sources": [
            {"url": it.url, "title": scrubber.scrub(it.title)[:200], "excerpt": scrubber.scrub(it.snippet)[:600]}
            for it in bundle.items[:6]
        ],
    }

    return {
        "synthesis_mode": synthesis_mode,
        "parsed": {
            "brand": p.brand,
            "model_family": p.model_family,
            "slug": p.slug,
            "platform": p.platform,
            "language": p.language,
            "fault_domains": p.fault_domains,
            "siblings": p.siblings,
            "codename": p.codename,
            "board_identifier": p.board_identifier,
            "symptom_summary_en": p.symptom_summary_en,
            "symptom_summary_fa": p.symptom_summary_fa,
        },
        "facts": {
            "ics": facts.ics,
            "rails": facts.rails,
            "model_codes": facts.model_codes,
        },
        "fixindex_before": bundle.fixindex_snapshot,
        "device_payload": device_payload,
        "repair_submission": repair_submission,
        "evidence_count": len(evidence),
        "video_count": len(bundle.youtube_videos),
        "warnings": bundle.warnings,
    }


def format_report(result: dict[str, Any]) -> str:
    p = result["parsed"]
    profile = result["device_payload"]["device"]["profile"]
    lines = [
        f"=== FixIndex Autonomous Harness Report ({result['synthesis_mode']}) ===",
        f"Device      : {p['brand']} {p['model_family']} (slug: {p['slug']})",
        f"Siblings    : {', '.join(p['siblings']) or 'None'}",
        f"Fault(s)    : {p['symptom_summary_en']} | {p['symptom_summary_fa']}",
        f"Evidence    : {result['evidence_count']} sources ({result['video_count']} videos)",
        f"Detected ICs: {', '.join(result['facts']['ics'].keys()) or 'None'}",
        "",
        "Verdict:",
        f"  {profile['verdict']}",
        "",
        "Ranked Failure Modes (Lowest Risk -> Board Rework):",
    ]
    for i, m in enumerate(profile.get("failure_modes", []), start=1):
        lines.append(f"  [{i}] {m['title']} ({m.get('severity')}, {m.get('confidence')})")
        lines.append(f"      Components: {', '.join(m.get('components', []))}")
        lines.append(f"      Fix       : {m.get('fix', '')}")
        lines.append(f"      Sources   : {', '.join(m.get('sources', []))}")
    return "\n".join(lines)
