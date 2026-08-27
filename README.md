# JoinedUp Shift Auto-Claimer

Automatically watches your Gmail for shift emails containing "Tyneside"
and claims them on JoinedUp before anyone else can.

---

## How It Works

1. Polls your Gmail every 30 seconds for unread emails
2. If an email contains "Tyneside" and has a JoinedUp link → triggers the bot
3. Bot opens JoinedUp, logs in with your credentials
4. Selects the latest time slot (if multiple offered)
5. Types your full name and submits
6. Saves a screenshot as proof of booking

---

## Setup (One Time Only)

### Step 1 — Install Python
Download from https://www.python.org/downloads/ (version 3.10 or newer)
Make sure to tick "Add Python to PATH" during install.

### Step 2 — Install dependencies
Open a terminal (Command Prompt or PowerShell on Windows) in this folder and run:

```
pip install -r requirements.txt
playwright install chromium
```

### Step 3 — Set up Gmail API access

1. Go to: https://console.cloud.google.com/
2. Create a new project (name it anything, e.g. "ShiftBot")
3. Go to "APIs & Services" → "Enable APIs"
4. Search for and enable: **Gmail API**
5. Go to "APIs & Services" → "Credentials"
6. Click "Create Credentials" → "OAuth 2.0 Client ID"
7. Application type: **Desktop app**
8. Download the JSON file and rename it to: **gmail_credentials.json**
9. Place gmail_credentials.json in this folder
 
### Step 4 — Configure the bot

1. Copy `config.example.json` to `config.json`
2. Open `config.json` and fill in:
   - `full_name` — your full name exactly as JoinedUp expects
   - `joinedup_email` — your JoinedUp login email
   - `joinedup_password` — your JoinedUp password
   - `sender_email` — the email address Angard/JoinedUp sends from
     (check the "From:" field in one of their emails)

### Step 5 — First run (Gmail authorisation)

```
python shift_bot.py
```

On the first run, a browser window will open asking you to sign in to Google
and grant permission to read your Gmail. Do this once — it saves a token so
you never have to do it again.

---

## Running the Bot

Every time you want the bot active:

```
python shift_bot.py
```

Keep the terminal window open. The bot will keep running and checking every
30 seconds. To stop it, press `Ctrl + C`.

### To run it automatically when your PC starts (Windows):
1. Press `Win + R`, type `shell:startup`, press Enter
2. Create a shortcut to: `pythonw shift_bot.py`
   (using `pythonw` runs it silently in the background)

---

## Logs & Screenshots

- **shift_bot.log** — full log of everything the bot does
- **screenshots/** — a screenshot is saved every time a shift is claimed
  (or when an error occurs, to help diagnose issues)

---

## Adjusting Keywords

To watch for more keywords (e.g. also grab "Gateshead" shifts), edit config.json:

```json
"trigger_keywords": ["Tyneside", "Gateshead"]
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Bot finds email but can't submit | Set `"headless": false` in config.json to watch it run |
| Wrong name field being filled | Check screenshots/error_general.png for clues |
| Not detecting emails | Check `sender_email` in config.json matches exactly |
| Gmail auth fails | Delete gmail_token.json and re-run to re-authorise |
