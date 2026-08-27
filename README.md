# ⚡ ShiftBot — JoinedUp Auto-Claimer

> *Because being first matters.*

ShiftBot watches your Gmail 24/7 and automatically replies to Royal Mail shift offers on JoinedUp (Beeline) the moment they land — faster than any human can click.

---

## 🚀 What It Does

- 📧 **Monitors Gmail** every 5 seconds for new shift offer emails
- 🔍 **Filters smartly** — only acts on real shift offers, ignores confirmations, completions and automated messages
- 🤖 **Logs into JoinedUp automatically** and submits your reply in seconds
- ⏱️ **Picks the longest shift** when multiple time slots are offered
- 🔁 **Retries on 404s** — when the server is overloaded from traffic, it keeps hammering until it gets through
- 📱 **Pushover alerts** — loud siren on your iPhone the moment a shift is claimed
- 🚨 **Allocation alerts** — emergency alarm when Angard books you onto a shift
- 🕐 **Age filtering** — only replies to shifts posted within the last N hours

---

## 🧠 How It Works

```
Gmail receives shift offer email
        ↓  (within 5 seconds)
Bot detects "Reply on JoinedUp" button
        ↓
Extracts the shift link
        ↓
Opens JoinedUp in a headless browser
        ↓
Logs in with your credentials
        ↓
Types your name + best time slot
        ↓
Clicks Reply
        ↓
🔔 iPhone alarm fires — shift claimed!
```

---

## 🛠️ Tech Stack

| Tool | Purpose |
|---|---|
| Python 3.10+ | Core language |
| Playwright | Browser automation |
| Gmail API | Email monitoring |
| Pushover API | iPhone push notifications |
| Google OAuth2 | Secure Gmail authentication |

---

## ⚙️ Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Gmail API credentials
- Go to [Google Cloud Console](https://console.cloud.google.com)
- Create a project → enable Gmail API
- Create OAuth 2.0 credentials → download as `gmail_credentials.json`

### 3. Configure
```bash
cp config.example.json config.json
```
Fill in your details:
```json
{
  "full_name": "Your Full Name",
  "joinedup_email": "your@email.com",
  "joinedup_password": "yourpassword",
  "trigger_keywords": ["Tyneside"],
  "max_email_age_minutes": 360,
  "pushover_app_token": "your_token",
  "pushover_user_key": "your_key",
  "poll_interval_seconds": 5,
  "headless": true
}
```

### 4. Run
```bash
python shift_bot.py
```

---

## 📱 Pushover Notifications

1. Download **Pushover** from the App Store
2. Create account at [pushover.net](https://pushover.net)
3. Create a new application → copy the API token
4. Add both your User Key and API token to `config.json`

| Alert Type | Priority | Sound |
|---|---|---|
| Shift claimed | High | Siren |
| Shift allocated to you | Emergency (repeats) | Siren |

---

## 🔒 Security

**Never commit these files:**
- `config.json` — contains your passwords
- `gmail_credentials.json` — Google API key
- `gmail_token.json` — Gmail access token

All are covered by `.gitignore` ✅

---

## 📂 Project Structure

```
shift-bot/
├── shift_bot.py          # Main bot
├── config.json           # Your settings (gitignored)
├── config.example.json   # Template
├── requirements.txt      # Python dependencies
├── gmail_credentials.json # Google OAuth (gitignored)
├── gmail_token.json      # Auto-generated token (gitignored)
├── seen_emails.json      # Tracks processed emails (gitignored)
├── seen_allocations.json # Tracks allocation alerts (gitignored)
├── shift_bot.log         # Activity log
└── screenshots/          # Proof of claims
```

---

## ⚠️ Disclaimer

This tool is for personal productivity use. Use responsibly and in accordance with your agency's terms of service.

---

*Built with Python, Playwright, and the desire to never miss a shift again.*