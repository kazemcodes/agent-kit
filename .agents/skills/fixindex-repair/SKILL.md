---
name: fixindex-repair
description: Diagnose mobile motherboard and software faults using FixIndex remote MCP first, then research with verifiable provenance and contribute drafts back via MCP.
---

# FixIndex Hardware Diagnostic & Repair Workflow

Use this skill whenever a technician or user asks about a smartphone hardware or software fault (in English or Persian). No repository access is required — every lookup and submission runs over FixIndex's remote HTTP/MCP endpoints.

**Companion skills (load alongside this skill when relevant):**
- `mobile-repair-technician` for phone/tablet teardown, micro-soldering, and diagnostic checklists.
- `electronics` for circuit-level analysis, power/protection rail checks, and staged bench verification.
- `electronics-sourcing` when sourcing or cross-referencing replacement ICs, MOSFETs, or passives.

## Authentication (Mandatory for ALL Requests)

Every request to **any** `/api/v1/*` REST endpoint or `/api/v1/mcp` server **requires** a personal FixIndex API key (`fx_live_...`) or JWT bearer token:

1. Register or sign in at `https://kazemmoridi.ir/register` and open `https://kazemmoridi.ir/dashboard` (`API / MCP tokens`).
2. Generate an API key:
   - `read:knowledge` for diagnostics, search, devices, testpoints, and error codes.
   - `read:knowledge` + `contribute:agent` if the agent may submit drafts or enrich dossiers via `upsert_device` / `submit_repair` / `ask_question`.
3. Send the token on every REST and MCP request:
   - `Authorization: Bearer fx_live_YOUR_API_KEY` (or `X-API-KEY: fx_live_YOUR_API_KEY`)

- **Streamable HTTP MCP (Primary):** `https://kazemmoridi.ir/api/v1/mcp`
- **SSE MCP (Legacy fallback):** `https://kazemmoridi.ir/api/v1/mcp/sse`
- **REST API Base:** `https://kazemmoridi.ir/api/v1`
- **OpenAPI 3.1 Spec:** `https://kazemmoridi.ir/api/v1/openapi.json`
- **API Key Portal:** `https://kazemmoridi.ir/dashboard`

## Quick Remote Setup (No Repo Access Needed)

### Hermes Agent
```bash
mkdir -p ~/.hermes/skills/fixindex-repair
curl -fsSL "https://kazemmoridi.ir/skill.md" -o ~/.hermes/skills/fixindex-repair/SKILL.md
hermes mcp add fixindex --url "https://kazemmoridi.ir/api/v1/mcp" --auth header
hermes mcp test fixindex
```
Provide `Authorization: Bearer fx_live_YOUR_API_KEY` when prompted or in your Hermes MCP headers config.

### Claude Code / Claude Desktop / Codex / Cursor / Antigravity
Install the skill directly onto your machine:
```bash
mkdir -p .agents/skills/fixindex-repair .claude/skills/fixindex-repair
curl -fsSL "https://kazemmoridi.ir/skill.md" -o .agents/skills/fixindex-repair/SKILL.md
cp .agents/skills/fixindex-repair/SKILL.md .claude/skills/fixindex-repair/SKILL.md
```

And configure your MCP client (`mcp.json`):
```json
{
  "mcpServers": {
    "fixindex": {
      "url": "https://kazemmoridi.ir/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer fx_live_YOUR_API_KEY"
      }
    }
  }
}
```
If a desktop client only supports stdio servers, bridge it with `npx -y mcp-remote "https://kazemmoridi.ir/api/v1/mcp" --header "Authorization: Bearer fx_live_YOUR_API_KEY"`.

## 11-Tool Diagnostic Sequence

1. **Check FixIndex MCP First (Never Guess Before Checking):**
   - Call `diagnose_symptom` with the raw user query (supports English and Persian, e.g. `"Xiaomi Poco F3 نه میکروفن و نه صدا کار میکنه"`).
   - Follow up based on what you hold:
     - `search_devices` and `get_device_detail` for board identifiers, regional variants, specs, and testpoints.
     - `lookup_error_code` for iTunes (`4013`, `9`), Sahara, or Odin error codes.
     - `search_testpoints` (`mode_type`: `edl`, `isp`, `dfu`, etc.) for pinout pads and instructions.
     - `search_repairs` and `get_repair_article` for peer-reviewed repair guides.
     - `search_questions` for community troubleshooting threads.

2. **When `coverage_status` is `partial`/`missing`, or the sections you need are empty:**
   - `coverage_status: found` only means the device matched — it does not guarantee `testpoints`, `diode_tables`, `ic_crossref`, or `repairs` are populated. Check the sections themselves before treating coverage as complete; an empty list still calls for external research.
   - Research verifiable external primary/technical sources using your **own** web search, browser, PDF, and document tools (do not assume any local repository scripts exist).
   - For locating, extracting and merging board schematics, see `references/schematic-acquisition.md`.
   - For reading the acquired PDF and mapping a symptom to a specific IC, pin or net, see `references/schematic-board-diagnostics.md`.
   - Prioritize official specs, board/schematic identifiers, iFixit teardowns, and verifiable repair documentation.

3. **Mandatory Online Sync Rule (Always Push Grounded Findings Back via MCP):**
   - **Never leave verified knowledge trapped in chat or local JSON files.** Whenever you resolve a fault, map a board's ICs/nets from a schematic, or research a device that was `missing` or `partial` in FixIndex, **proactively push** the grounded findings back to FixIndex online via MCP (`upsert_device` for device specs/ICs/failure modes/bench checks, and `submit_repair` for concrete fault-to-fix cases) in the same session — do not wait to be asked.
   - Every pushed record MUST include verifiable `provenance` URLs (`https://...`) from sources you actually fetched/read and MUST have zero PII.
   - **`upsert_device` schema gotchas (avoid validation retries):** `device.profile.failure_modes[].sources[]` must be an array of **string IDs** referencing `device.profile.evidence[].id` (not inline objects); `device.profile.ic_crossref[]` requires `ic` (not `part_number`); `variants[]` requires `hardware_code` (not `model_code`); bench checks live under `device.profile.checks` (not `bench_checks`).
   - If the token lacks `contribute:agent` (403) or validation fails, report the exact error and what was/wasn't synced; when it succeeds, report the live slug/ID and its quarantine status (`agent_draft` / `pending_review`).

4. **Fast Charge Handshake Drop & USB D+/D- Active Short Isolation:**
   - **9V/Turbo -> 5V / ~0.7A Fallback:** If the phone powers on, negotiates **9V** momentarily (or climbs to ~2A), then drops back to **5V / ~0.6–0.7A**, the USB-C `CC`/`DP`/`DM` handshake initially succeeded — isolate in order: (1) verify battery SoC is **<70%** (a ~100% battery naturally tapers to ~0.3–0.6A in CV phase and cannot test fast charge), check CIT battery temp, Fastboot reboot, and test direct OEM charger/cable (4-pin USB meters can block 5-pin proprietary protocols like Xiaomi Mi Turbo and cap draw at ~5V/1.6A), (2) main-to-sub-board B2B flex & `CONN_THERM` pin (high resistance causes VBUS sag under load), (3) known-good discharged OEM battery & `BATT_THERM`/`BATT_ID`/`VBATT_SNS` lines (high battery internal resistance causes erratic percentage jumps and spikes `VBATT` toward ~4.4V at 2A, tripping charger cutoff), (4) 2:1 charge-pump IC (e.g. `BQ25968` / pin-compatible `LN8000`) flying caps (`CFH`), external OVP FET (`OVP_DRIVE`), current-sense resistor (`IBATT_SENSE_P/M`, e.g. 2 mΩ), and charge-pump rework — on boards where the charge pump sits under underfill beside the SoC, CPU reball heat often floats it; removing the **2:1 charge pump** leaves normal boot and 5V charging intact (clean isolation), whereas removing the **primary charger PMIC** (e.g. `PM8150B`) kills `VPH_PWR` and stops boot/charge completely, (5) main charger PMIC `BOOT_CAP` & inductor.
   - **Active D-/D+ Short Only When Powered ON (Never Inject Voltage):** If `D-` (or `D+`) measures normal diode/ohms when OFF but drops to `0` (short) when powered ON, passive parts (port, flex, TVS) are NOT hard-shorted — **never inject voltage on D+/D-** because there is no passive short to heat and injection destroys the USB switch, audio IC, or CPU PHY.
   - **USB/UART Switch Bypass:** Check the PCB bitmap for the exact `D+`/`D-` switch IC before assuming an `FSA4480` audio switch — many Qualcomm boards place a 12-ball **USB/DBG_UART debug switch** in series that pulls `D-` low when mis-switched to UART on boot. Remove the switch IC and bridge its factory `NC` bypass pads (`USB_DP <-> USB1_HS_DP`, `USB_DM <-> USB1_HS_DM`) to eliminate switchover shorts cleanly.
   - **Parallel-Branch Diode Masking, EDL 9008 Proof & Tray Recognition:** A PC USB connect chime with no enumeration means `VBUS` + `D+` pull-up are alive while `D-` is open or pulled low. When `USB_DM` splits through separate resistors to the charger PMIC (`*_DM_DET`) and SoC (`USB1_HS_DM`), a diode reading on the common node masks an open/damaged CPU ball because the PMIC branch still reads ~0.52V — lift the `*_DM_DET` resistor to test the CPU branch independently. If **EDL 9008** enumerates on PC, the SoC USB PHY and physical `D+`/`D-` path are intact; stop suspecting CPU reball faults and check charger PMIC USB-role detection or OS state. If Windows taskbar/tray displays the **device model name** while `This PC` is empty, USB hardware is 100% healthy — switch Android USB mode from Charging to `File Transfer` or update the Windows MTP driver.

5. **Thermal & Overheat Fault Isolation (Flash Disabled vs Fast Charge):**
   - **Isolate by Fast Charge State:** If CIT shows normal battery temperature (~31°C) but the torch/flash is greyed out with "Device is overheated":
     - If **Fast Charging works**: `BATT_THERM`, `CONN_THERM` (Type-C sub-board), and `CHARGER_THERM` are functional. Focus exclusively on chassis/skin thermistors (`QUIET_THERM`, `MDM_SKIN_THERM`, or RF `PA_THERM`), which trigger flashlight thermal foldback without stopping charging.
     - If **Fast Charging is disabled or throttled**: suspect `CONN_THERM` (sub-board flex / B2B connector) or charger IC thermistor.
   - **NTC Bypass Rule:** Never leave a missing/knocked-off NTC open or shorted to GND. An open circuit reads as ADC out-of-range/error; a short (0Ω) reads as extreme overheat (>120°C). If the exact NTC is unavailable, substitute a standard 100kΩ fixed resistor to ground to force the ADC to a constant ~25°C.
   - **Flashlight Drive vs Peripheral Flex Sensors:**
     - The flashlight pin (`FLASH_LED`) is a switched current sink from the PMIC (e.g. PM8150A) and reads **0V in standby**; it only energizes when actively commanded by the Camera HAL. Do NOT diagnose 0V as a dead PMIC; measure diode mode (0.400V–0.650V) to verify continuity.
     - If peripheral sensors on the shared rear camera/flash flex (such as the Ambient Light Sensor / Anti-flicker sensor) fail to ACK over I2C, Android Camera Provider sets torch status to `TORCH_MODE_STATUS_NOT_AVAILABLE` (greyed out icon). Always check rear light sensor responsiveness in CIT / Device Info before replacing PMICs.

6. **Qualcomm & MediaTek No-Boot Stage Isolation (DC Bench / PoP RAM / UFS / BROM):**
   - **Qualcomm BMS Requirement on DC Bench Supply:** Many Qualcomm boards (e.g. `SM8150` / Poco X3 Pro) will **not** boot from a bare 2-wire DC bench supply without BMS `BATT_ID`/`BATT_THERM` lines and hang around ~0.18–0.21A even when healthy — always test with a charged OEM battery (or BMS flex) and compare against a known-good board before condemning the mainboard.
   - **Qualcomm PoP RAM vs UFS vs Sub-PMIC Split:**
     - **USB-only LED blink (battery disconnected):** A blinking notification LED with no battery indicates `xbl.elf` loaded from UFS into CPU SRAM (which runs without RAM), proving CPU + UFS are alive and pointing to **PoP RAM** solder fatigue or chip failure.
     - **EDL 9008 tool stage split:** In servicing tools like TFTUnlock, use the dedicated **`Qualcomm Snapdragon`** tab (not brand `MTP`/`Sideload` tabs) once `QDLoader 9008` is detected: Sahara OK + Firehose/DDR init failure points to **PoP RAM**; Firehose/Ping OK + storage/GPT failure points to **UFS**; full GPT/Info read OK proves CPU/RAM/UFS are healthy (check sub-PMIC rails like `VREG_BOB` or display/backlight).
     - **Sustained flash/heat no-boot:** Where one sub-PMIC (e.g. `PM8150L`) drives both the rear torch LED and display/backlight/`VREG_BOB`, prolonged torch runtime without chassis heatsinking can damage the sub-PMIC (phone vibrates/enumerates on USB with a black screen, or loses `VREG_BOB`) or fatigue PoP RAM joints.
   - **MediaTek Storage Init Failures (BROM Hang):**
     - On MediaTek platforms (Dimensity / Helio), a boot current rising to 0.20A–0.25A and dropping/holding at 0.15A–0.18A (paired with ~0.7A charging draw without screen animation) indicates CPU/PMIC power sequencing has completed, but CPU has fallen into BootROM (BROM) mode due to UFS/eMMC storage init failure (unable to load Preloader).
     - Test PC USB enumeration in Device Manager: `MediaTek USB Port` (VCOM) without pressing volume keys confirms CPU/PMIC health and points directly to storage disconnect or corrupted firmware.
     - On UFS 2.2 BGA packages, never assume power pads (VDD/VCC/VCCQ) are internally bridged across balls. Separate balls power independent charge pumps, logic, and NAND banks; a single missing VDD pad will prevent UFS handshake.

7. **Strict Grounding, Safety & Quarantine Rules:**
   - **Never invent** diode-mode readings, resistor values, pad coordinates, voltages, or IC part numbers not present in a source you read.
   - **Scope every MCP section to the queried device:** `diagnose_symptom` returns a `devices` array alongside `failure_modes`, `bench_checks`, `diode_tables` and `testpoints` that may belong to sibling models. Filter each item by `device_slug`/`device_id` before citing it, and report `review_status: agent_draft` when that is what the record says.
   - **Zero PII:** strip IMEIs (15-digit numbers), serial numbers, customer emails, or phone numbers before calling any write tool.
   - **Quarantine invariant:** `submit_repair` always creates a `pending_review` draft in `/review`; `upsert_device` tags agent-authored failure modes with `review_status: agent_draft` until verified by a human technician. Communicate that status honestly.
   - Order diagnostic recommendations from **lowest risk / fastest isolation** (software/CIT menu, flex cables, lower sub-board) to **highest risk** (audio codec, PMIC, CPU/RAM BGA rework).