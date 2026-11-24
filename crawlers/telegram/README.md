# Connecting a Telegram Account to FixIndex

This guide walks through connecting a real Telegram user account (via **Telethon / MTProto**) so the crawler and diagnostic harness can search and read the repair channels and groups your account belongs to.

> **Why a user account instead of a Telegram Bot?**
> Most mobile repair channels and technician groups restrict or ban bots, and the Bot API cannot search across channels you've joined. A user session via Telethon works like an official Telegram client and can only access chats your account is already a member of.

---

## 1. Install dependencies

From the `agent-kit` root directory:

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\pip.exe install -r crawlers\telegram\requirements.txt
```

**Linux / macOS:**
```bash
./.venv/bin/pip install -r crawlers/telegram/requirements.txt
```

*(If you don't have `.venv` yet, create it first with `python -m venv .venv`.)*

---

## 2. Get your Telegram `api_id` and `api_hash`

1. Open [https://my.telegram.org](https://my.telegram.org) in your browser.
2. Enter your phone number in international format (e.g., `+989123456789`) and click **Next**.
3. Telegram will send a confirmation code **inside your Telegram app** (from the official "Telegram" chat, not SMS). Paste it to sign in.
4. Click **API development tools**.
5. If this is your first time, fill out the short form:
   - **App title**: `FixIndex Repair` (any name)
   - **Short name**: `fixindexrepair` (alphanumeric, no spaces)
   - **Platform**: `Desktop`
   - **Description**: `Personal hardware repair search`
   - Click **Create application**.
6. Copy the two values shown:
   - **`App api_id`** (a number, e.g. `12345678`)
   - **`App api_hash`** (a 32-character hex string, e.g. `0123456789abcdef0123456789abcdef`)

> **Tip if `my.telegram.org` shows `ERROR` when creating the app:**
> Disable ad-blockers/browser extensions or try from a clean browser window with a stable IP (switching VPN locations mid-login often triggers Telegram's anti-spam block).

---

## 3. Create `crawlers/config.json`

Copy the example config:

**Windows (PowerShell):**
```powershell
Copy-Item crawlers\config.example.json crawlers\config.json
```

**Linux / macOS:**
```bash
cp crawlers/config.example.json crawlers/config.json
```

Open `crawlers/config.json` and fill in the `telegram` section:

```json
"telegram": {
    "api_id": 12345678,
    "api_hash": "YOUR_32_CHAR_API_HASH",
    "phone": "+98XXXXXXXXXX",
    "session_name": "fixindex_crawler",
    "groups": [
        "@public_repair_channel",
        "https://t.me/+PrivateInviteHash",
        -1001234567890
    ],
    "max_messages_per_group": 500,
    "min_message_length": 60,
    "submit_full_cases": true
}
```

### How to specify channels / groups
- **Public channel or group**: `"@channel_username"`
- **Private invite link**: `"https://t.me/+InviteHash"`
- **Numeric chat ID**: `-1001234567890` (you can get this from Telegram Web URLs or by listing your dialogs once logged in)

> Your account must already be a member of any private group/channel you list.

---

## 4. First-time login (creates the `.session` file)

Run a **dry run** first so nothing is submitted to FixIndex while you authenticate:

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\python.exe crawlers\telegram\crawl_telegram.py --dry-run --limit 20
```

**Linux / macOS:**
```bash
./.venv/bin/python crawlers/telegram/crawl_telegram.py --dry-run --limit 20
```

On the first run, Telethon will prompt you in the terminal:
1. **Login code**: Check your Telegram app for the 5-digit code and enter it.
2. **2FA password** *(only if Two-Step Verification is enabled on your account)*: Enter your Telegram cloud password.

Once authenticated, you will see:
```text
Logged in to Telegram (client session).
```

Telethon saves a reusable session file at:
```text
crawlers/telegram/fixindex_crawler.session
```
Future runs will **not** ask for your login code again as long as that `.session` file stays intact.

---

## 5. Verify your connection & list joined channels

To quickly verify the session works and see the channels/groups your account can access, run:

```powershell
.\.venv\Scripts\python.exe -c "import asyncio, json; from telethon import TelegramClient; cfg = json.load(open('crawlers/config.json'))['telegram']; c = TelegramClient('crawlers/telegram/' + cfg.get('session_name', 'fixindex_crawler'), cfg['api_id'], cfg['api_hash']); async def main(): await c.start(phone=cfg['phone']); [print(d.id, getattr(d.entity, 'username', None), '|', d.name) async for d in c.iter_dialogs(limit=30)]; await c.disconnect(); asyncio.run(main())"
```

This prints the first 30 chats/channels with their numeric IDs, `@usernames`, and titles.

---

## 6. Security & Account Safety

- **Never commit secrets or sessions**: Both `crawlers/config.json` and `*.session` are gitignored. A `.session` file grants full access to your Telegram account — treat it like a password.
- **Consider a dedicated account**: If you prefer isolating automation from your personal chats, use a secondary Telegram account that has joined your repair channels.
- **Revoking access**: You can terminate the session anytime from your Telegram app under **Settings → Devices → Active Sessions**.
- **Rate limits (`FloodWaitError`)**: Avoid aggressive scraping. Keep pacing gentle and let the script sleep if Telegram returns a FloodWait.

---

## Troubleshooting

| Symptom | Cause & Fix |
|---|---|
| `ModuleNotFoundError: No module named 'telethon'` | Install requirements into `.venv`: `.\.venv\Scripts\pip.exe install -r crawlers\telegram\requirements.txt` |
| Connection hangs / times out | If Telegram is blocked on your network, turn on your system VPN (TUN mode) or configure a SOCKS5/HTTP proxy in Telethon. |
| `SessionPasswordNeededError` | Your account has 2FA enabled — run interactively in a real terminal so you can type your cloud password. |
| `FloodWaitError: A wait of X seconds is required` | You hit Telegram's rate limit. Wait out the cooldown and reduce `--limit` or request frequency. |
| `Cannot find any entity corresponding to ...` | Check the `@username` spelling, make sure your account is already in the group, or use the numeric ID from Step 5. |
