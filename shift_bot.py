"""
JoinedUp Shift Auto-Claimer
============================
Watches Gmail for shift emails containing "Tyneside", then:
  - Mode A: Replies directly to the email with your full name + chosen time slot
  - Mode B: Opens the JoinedUp link, logs in, selects slot, submits name

In both cases, the LONGEST (most hours) shift is always selected.

Requirements:
    pip install playwright google-auth google-auth-oauthlib google-api-python-client
    playwright install chromium
"""

import json
import os
import time
import base64
import re
import logging
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import threading
import hmac
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─────────────────────────────────────────────
# GMAIL PUSH WEBHOOK SERVER
# ─────────────────────────────────────────────
# This runs a tiny HTTP server on your PC.
# Gmail pushes a notification to it the INSTANT a new email arrives.
# Much faster than polling every 5 seconds.

_new_email_event = threading.Event()  # Signal: new email arrived

class GmailWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        """Gmail sends a POST request when a new email arrives."""
        content_length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(content_length)  # Read body (we just need the signal)
        self.send_response(200)
        self.end_headers()
        log.info("Gmail push received - checking emails NOW")
        _new_email_event.set()  # Wake up the main loop immediately

    def log_message(self, format, *args):
        pass  # Suppress default HTTP server logs


def start_webhook_server(port: int = 8765):
    """Start the webhook server in a background thread."""
    server = HTTPServer(("0.0.0.0", port), GmailWebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Webhook server listening on port {port}")
    return server


def setup_gmail_push(service, pubsub_topic: str):
    """
    Tell Gmail to push notifications to our webhook via Google Cloud Pub/Sub.
    Gmail -> Pub/Sub -> our ngrok URL -> webhook server -> instant check
    """
    try:
        result = service.users().watch(
            userId="me",
            body={
                "labelIds": ["INBOX"],
                "topicName": pubsub_topic,
            }
        ).execute()
        log.info(f"Gmail push notifications active. Expiry: {result.get('expiration')}")
        return result
    except Exception as e:
        log.warning(f"Gmail push setup failed (will fall back to polling): {e}")
        return None


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler("shift_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ShiftBot")

# ─────────────────────────────────────────────
# PUSHOVER NOTIFICATIONS
# ─────────────────────────────────────────────
def send_pushover(config: dict, title: str, message: str, priority: int = 1):
    """
    Send a push notification to your iPhone via Pushover.
    Priority 1 = high priority (loud alarm, bypasses quiet hours)
    Priority 2 = emergency (repeats until acknowledged)
    """
    token = config.get("pushover_app_token", "")
    user  = config.get("pushover_user_key", "")

    if not token or not user:
        log.warning("Pushover not configured - skipping notification")
        return

    try:
        import urllib.request, urllib.parse
        data = urllib.parse.urlencode({
            "token":    token,
            "user":     user,
            "title":    title,
            "message":  message,
            "priority": priority,
            "sound":    "siren",   # Loud alarm sound
            "retry":    30,        # For priority 2: retry every 30s
            "expire":   300,       # For priority 2: stop after 5 mins
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
        urllib.request.urlopen(req, timeout=10)
        log.info(f"Pushover notification sent: {title}")
    except Exception as e:
        log.error(f"Pushover failed: {e}")


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
CONFIG_FILE  = Path("config.json")
SEEN_FILE    = Path("seen_emails.json")
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send"
]

def load_config():
    if not CONFIG_FILE.exists():
        log.error("config.json not found. Copy config.example.json to config.json and fill it in.")
        raise SystemExit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def load_seen():
    try:
        if SEEN_FILE.exists():
            with open(SEEN_FILE) as f:
                return set(json.load(f))
    except Exception:
        log.warning("seen_emails.json was corrupted - resetting to empty")
    return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ─────────────────────────────────────────────
# GMAIL AUTH
# ─────────────────────────────────────────────
def get_gmail_service():
    creds = None
    token_path = Path("gmail_token.json")

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("gmail_credentials.json", GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

# ─────────────────────────────────────────────
# EMAIL PARSING
# ─────────────────────────────────────────────
def get_email_body(msg) -> str:
    """Returns plain text + HTML combined so we can search both."""
    payload = msg.get("payload", {})

    def decode(data):
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

    parts_text = []

    def extract_parts(payload):
        if payload.get("body", {}).get("data"):
            parts_text.append(decode(payload["body"]["data"]))
        for part in payload.get("parts", []):
            extract_parts(part)

    extract_parts(payload)
    return chr(10).join(parts_text)


def get_email_headers(msg) -> dict:
    headers = {}
    for h in msg.get("payload", {}).get("headers", []):
        headers[h["name"].lower()] = h["value"]
    return headers


def extract_joinedup_link(body: str):
    all_links = []

    # Find href links first - actual button URLs in HTML emails
    href_matches = re.findall(r'href=["\']?(https?://[^"\'> ]+)', body, re.IGNORECASE)
    all_links.extend(href_matches)

    # Also plain text URLs
    plain_matches = re.findall(r'https?://[^\s"<>]+', body, re.IGNORECASE)
    all_links.extend(plain_matches)

    # Clean trailing punctuation, filter to joinedup/beeline only
    cleaned = []
    seen_urls = set()
    for m in all_links:
        while m and m[-1] in ')\'">.,;':
            m = m[:-1]
        if ('joinedup' in m.lower() or 'beeline' in m.lower()) and m not in seen_urls:
            cleaned.append(m)
            seen_urls.add(m)

    if not cleaned:
        return None

    log.info("JoinedUp links found in email:")
    for lnk in cleaned:
        log.info(f"  {lnk}")

    # Prefer links with shift/exchange/conversation path
    for m in cleaned:
        if any(kw in m.lower() for kw in ["/exchange/", "/message/", "/conversation/", "/shifts/"]):
            log.info(f"Selected link: {m}")
            return m

    log.info(f"Using first link: {cleaned[0]}")
    return cleaned[0]


def contains_keyword(body: str, keywords: list) -> bool:
    return any(kw.lower() in body.lower() for kw in keywords)


def is_shift_offer_email(body: str) -> bool:
    # Returns True ONLY for genuine shift offer emails.
    # A real shift offer always has:
    #   - "Reply on JoinedUp" button
    #   - "View conversation" link at the top
    # Automated messages (confirmations, completions) have different buttons.
    body_lower = body.lower()

    # REJECT automated JoinedUp system emails - these are never shift offers
    reject_phrases = [
        "has booked you onto a shift",
        "booked you onto",
        "your shift stopped",
        "shift stopped",
        "your shift has been",
        "view shift details & confirm",
        "you have confirmed this shift",
        "shift is confirmed",
        "timesheet",
        "invoice",
        "your shift starts in",
        "reminder: your shift",
        "has been cancelled",
    ]
    for phrase in reject_phrases:
        if phrase in body_lower:
            log.info(f"Email ignored - automated message: '{phrase}'")
            return False

    # ACCEPT if it has the "Reply on JoinedUp" button (real shift offers always have this)
    if "reply on joinedup" in body_lower:
        return True

    # ACCEPT if it has both "View conversation" and a shift/exchange link
    if "view conversation" in body_lower:
        return True

    log.info("Email ignored - not a shift offer (no Reply on JoinedUp button found)")
    return False


def fetch_new_emails(service, seen: set, config: dict):
    results = []
    sender_filter = config.get("sender_email", "")
    query = f"from:{sender_filter} is:unread" if sender_filter else "is:unread"

    response = service.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = response.get("messages", [])

    for m in messages:
        mid = m["id"]
        if mid in seen:
            continue

        msg     = service.users().messages().get(userId="me", id=mid, format="full").execute()
        body    = get_email_body(msg)
        headers = get_email_headers(msg)

        if contains_keyword(body, config["trigger_keywords"]) and is_shift_offer_email(body):
            # Only process emails received within the last 60 minutes
            max_age_mins = config.get("max_email_age_minutes", 60)
            internal_date = int(msg.get("internalDate", 0)) / 1000  # ms to seconds
            email_age_mins = (time.time() - internal_date) / 60

            if email_age_mins > max_age_mins:
                log.info(f"Email {mid} skipped - too old ({email_age_mins:.0f} mins ago, limit={max_age_mins} mins)")
                seen.add(mid)
                continue

            log.info(f"Email {mid} is {email_age_mins:.1f} mins old - processing")
            link = extract_joinedup_link(body)
            results.append({
                "id":         mid,
                "body":       body,
                "link":       link,
                "reply_to":   headers.get("reply-to") or headers.get("from", ""),
                "subject":    headers.get("subject", ""),
                "message_id": headers.get("message-id", ""),
                "thread_id":  msg.get("threadId", ""),
            })
            mode = "web form" if link else "email reply"
            log.info(f"Matching shift email found! ID={mid} | Mode={mode}")

        seen.add(mid)

    return results

# ─────────────────────────────────────────────
# TIME SLOT HELPERS
# ─────────────────────────────────────────────
TIME_RANGE_RE = re.compile(
    r'(\d{1,4})(?::(\d{2}))?\s*(am|pm)?\s*[-–]\s*(\d{1,4})(?::(\d{2}))?\s*(am|pm)?',
    re.IGNORECASE
)

def parse_time_to_mins(h_raw: int, m_raw: int, ampm: str) -> int:
    h, m = h_raw, m_raw
    if h > 99:          # compact 4-digit e.g. 2230
        m = h % 100
        h = h // 100
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h != 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
    return h * 60 + m


def slot_duration(raw_text: str) -> float:
    """Return hours for a single time-range string. 0.0 if unparseable."""
    match = TIME_RANGE_RE.search(raw_text)
    if not match:
        return 0.0
    sh, sm, sap, eh, em, eap = match.groups()
    sh_int = int(sh)
    eh_int = int(eh)

    # Handle compact 4-digit times e.g. 0800, 2230
    if sh_int > 99:
        sm = str(sh_int % 100)
        sh_int = sh_int // 100
    if eh_int > 99:
        em = str(eh_int % 100)
        eh_int = eh_int // 100

    # Reject invalid times - hours must be 0-23, minutes 0-59
    sm_int = int(sm or 0)
    em_int = int(em or 0)
    if sh_int > 23 or eh_int > 23 or sm_int > 59 or em_int > 59:
        return 0.0

    start = sh_int * 60 + sm_int
    end   = eh_int * 60 + em_int
    if end <= start:
        end += 24 * 60

    # Reject implausible durations (over 13 hours is suspicious)
    duration = (end - start) / 60.0
    if duration > 13:
        return 0.0

    return duration


def pick_best_slot_from_body(body: str) -> tuple:
    """
    Scan entire email body for all time ranges.
    Return (raw_string, duration_hours) of the longest one.
    """
    body = body.replace(" to ", " - ")
    seen_raw = {}
    for match in TIME_RANGE_RE.finditer(body):
        raw = match.group(0).strip()
        dur = slot_duration(raw)
        if dur > 0:
            seen_raw[raw] = max(seen_raw.get(raw, 0), dur)

    if not seen_raw:
        return None, 0.0

    for raw, dur in sorted(seen_raw.items(), key=lambda x: x[1], reverse=True):
        log.info(f"  Slot found: {raw} -> {dur:.1f}h")

    best_raw = max(seen_raw, key=seen_raw.get)
    return best_raw, seen_raw[best_raw]

# ─────────────────────────────────────────────
# MODE A - REPLY TO EMAIL
# ─────────────────────────────────────────────
def reply_via_email(service, email_info: dict, config: dict) -> bool:
    """
    Replies to the shift email with:
        "<Full Name> - <chosen time slot>"
    e.g. "John Smith - 2230 - 0740"
    """
    full_name = config["full_name"]

    best_slot, best_dur = pick_best_slot_from_body(email_info["body"])

    if best_slot:
        reply_body = f"{full_name} - {best_slot}"
        log.info(f"Selected slot: {best_slot} ({best_dur:.1f}h)")
    else:
        reply_body = full_name
        log.info("No time slots found in email - replying with name only")

    log.info(f"Sending reply: '{reply_body}'")

    subject = email_info["subject"]
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject

    mime_msg = MIMEText(reply_body)
    mime_msg["To"]          = email_info["reply_to"]
    mime_msg["Subject"]     = subject
    mime_msg["In-Reply-To"] = email_info["message_id"]
    mime_msg["References"]  = email_info["message_id"]

    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

    try:
        service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": email_info["thread_id"]}
        ).execute()
        log.info(f"Reply sent to {email_info['reply_to']}")
        return True
    except Exception as e:
        log.error(f"Failed to send reply: {e}")
        return False

# ─────────────────────────────────────────────
# MODE B - JOINEDUP WEB FORM
# ─────────────────────────────────────────────
def claim_via_web(link: str, email_body: str, config: dict) -> bool:
    """
    Opens the JoinedUp link, logs in, picks the longest slot,
    enters full name, and submits.
    """
    ju_email    = config["joinedup_email"]
    ju_password = config["joinedup_password"]
    full_name   = config["full_name"]
    headless    = config.get("headless", False)  # Temporarily visible for debugging

    log.info("Opening JoinedUp link...")

    # Reuse persistent browser session for speed - avoids cold start every time
    if not hasattr(claim_via_web, "_pw") or not claim_via_web._browser.is_connected():
        log.info("Starting browser session...")
        claim_via_web._pw  = sync_playwright().start()
        claim_via_web._browser = claim_via_web._pw.chromium.launch(headless=headless)
        iphone = claim_via_web._pw.devices["iPhone 13"]
        claim_via_web._ctx = claim_via_web._browser.new_context(**iphone)
        log.info("Browser ready.")

    context = claim_via_web._ctx
    page = context.new_page()

    try:
            # Retry loop - JoinedUp gets 404s under heavy traffic when shift drops
            # Keep retrying every 2 seconds for up to 2 minutes to beat the crowd
            MAX_RETRIES = 60
            RETRY_DELAY = 2
            logged_in   = False
            success_page = False

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    log.info(f"Attempt {attempt}/{MAX_RETRIES}...")
                    page.goto(link, timeout=20_000)
                    page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception as nav_err:
                    log.warning(f"Nav error: {nav_err} - retrying...")
                    time.sleep(RETRY_DELAY)
                    continue

                page_text = page.locator("body").inner_text()

                # 404 = server overloaded, keep retrying
                if "404" in page_text or "page not found" in page_text.lower() or "doesn't exist" in page_text.lower():
                    log.warning(f"404 - server busy, retry in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                    continue

                # Step 1: email login
                email_input = page.locator("input[type='email'], input[name='email']")
                if not logged_in and email_input.count() > 0:
                    log.info("Step 1: Entering email...")
                    email_input.first.fill(ju_email)
                    page.wait_for_timeout(200)
                    page.locator("button:has-text('Next'), button[type='submit']").first.click()
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    page.wait_for_timeout(300)

                # Step 2: password login
                if not logged_in and page.locator("input[type='password']").count() > 0:
                    log.info("Step 2: Entering password...")
                    page.locator("input[type='password']").first.fill(ju_password)
                    page.wait_for_timeout(200)
                    page.locator(
                        "button[type='submit'], button:has-text('Log in'), "
                        "button:has-text('Sign in'), button:has-text('Login')"
                    ).first.click()
                    page.wait_for_load_state("networkidle", timeout=10_000)
                    page.wait_for_timeout(500)
                    logged_in = True
                    log.info(f"Logged in: {page.url}")
                    page_text = page.locator("body").inner_text()
                    if "404" in page_text or "page not found" in page_text.lower():
                        log.warning("404 after login - retrying...")
                        time.sleep(RETRY_DELAY)
                        continue

                # Success - reply box is visible
                if page.locator("textarea").count() > 0:
                    log.info(f"Reply page found on attempt {attempt}!")
                    success_page = True
                    break

                log.info(f"No reply box yet - retry in {RETRY_DELAY}s...")
                time.sleep(RETRY_DELAY)

            if not success_page:
                log.error(f"Could not reach reply page after {MAX_RETRIES} attempts")
                page.close()
                return False

            # Build reply text from email time slots
            # If multiple slots, pick the longest e.g. "John Smith - 2230 - 0740"
            # If no slots found, just reply with name
            best_slot, best_dur = pick_best_slot_from_body(email_body)
            if best_slot:
                reply_text = f"{full_name} - {best_slot}"
                log.info(f"Reply text: '{reply_text}' ({best_dur:.1f}h slot)")
            else:
                reply_text = full_name
                log.info(f"Reply text: '{reply_text}' (name only)")

            # Wait for JS app to fully render
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            log.info(f"Final page URL: {page.url}")

            # Save debug screenshot
            os.makedirs("screenshots", exist_ok=True)
            page.screenshot(path="screenshots/debug_before_reply.png")
            log.info("Debug screenshot: screenshots/debug_before_reply.png")

            # The page is a JS app - wait for the textarea to render
            # Don't click any links - the reply box is already on this page
            log.info("Waiting for reply textarea to render...")
            try:
                page.locator("textarea").first.wait_for(state="visible", timeout=20_000)
                textarea_found = True
            except:
                textarea_found = False

            if not textarea_found:
                # Take a fresh screenshot to see current state
                page.screenshot(path="screenshots/debug_no_textarea.png")
                log.warning("Textarea not visible after 20s - see debug_no_textarea.png")
                raise Exception("Could not find textarea on reply page")

            textarea = page.locator("textarea").first
            textarea.click()
            page.wait_for_timeout(500)
            textarea.fill(reply_text)
            log.info(f"Typed into textarea: {reply_text}")

            page.wait_for_timeout(500)

            # Click the Reply button
            reply_btn = page.locator(
                "button:has-text('Reply'), "
                "button[type='submit'], "
                "input[type='submit']"
            ).first
            reply_btn.wait_for(state="visible", timeout=10_000)
            reply_btn.click()
            log.info("Clicked Reply button.")

            # Send phone alarm notification
            send_pushover(
                config,
                title="Shift Claimed!",
                message=f"You replied to a Tyneside shift: {reply_text}",
                priority=1
            )
            page.wait_for_load_state("networkidle", timeout=15_000)
            log.info("Web form submitted!")

            os.makedirs("screenshots", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            page.screenshot(path=f"screenshots/booked_{ts}.png")
            log.info(f"Screenshot saved: screenshots/booked_{ts}.png")

            page.close()
            return True

    except PlaywrightTimeout as e:
        log.error(f"Timeout: {e}")
        os.makedirs("screenshots", exist_ok=True)
        page.screenshot(path="screenshots/error_timeout.png")
        page.close()
        return False

    except Exception as e:
        log.error(f"Web error: {e}")
        try:
            os.makedirs("screenshots", exist_ok=True)
            page.screenshot(path="screenshots/error_general.png")
        except:
            pass
        page.close()
        return False

# ─────────────────────────────────────────────
# ALLOCATION DETECTION
# ─────────────────────────────────────────────
SEEN_ALLOCATIONS_FILE = Path("seen_allocations.json")

def load_seen_allocations():
    try:
        if SEEN_ALLOCATIONS_FILE.exists():
            with open(SEEN_ALLOCATIONS_FILE) as f:
                return set(json.load(f))
    except Exception:
        log.warning("seen_allocations.json was corrupted - resetting to empty")
    return set()

def save_seen_allocations(seen: set):
    with open(SEEN_ALLOCATIONS_FILE, "w") as f:
        json.dump(list(seen), f)

def check_for_allocations(service, seen: set, config: dict):
    """Watch for emails saying a shift was allocated to you."""
    sender_filter = config.get("sender_email", "")
    query = f"from:{sender_filter} is:unread" if sender_filter else "is:unread"

    try:
        response = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
        messages = response.get("messages", [])

        for m in messages:
            mid = m["id"]
            if mid in seen:
                continue
            seen.add(mid)

            msg  = service.users().messages().get(userId="me", id=mid, format="full").execute()
            body = get_email_body(msg)
            body_lower = body.lower()

            # Detect allocation emails
            if "has booked you onto a shift" in body_lower or "booked you onto" in body_lower:
                # Extract shift details from email
                headers = get_email_headers(msg)
                log.info(f"ALLOCATION detected in email {mid}!")
                send_pushover(
                    config,
                    title="Shift Allocated to You!",
                    message="You have been booked onto a shift. Check JoinedUp to confirm.",
                    priority=2  # Emergency - repeats until acknowledged
                )

        save_seen_allocations(seen)
    except Exception as e:
        log.error(f"Allocation check error: {e}")


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    config    = load_config()
    seen      = load_seen()
    seen_all  = load_seen_allocations()
    service   = get_gmail_service()

    poll_interval = config.get("poll_interval_seconds", 30)

    log.info("Shift Bot started!")
    log.info(f"  Keywords  : {config['trigger_keywords']}")
    log.info(f"  Poll every: {poll_interval}s")
    log.info("  Ctrl+C to stop.\n")

    consecutive_errors = 0

    # Start webhook server for instant Gmail push notifications
    pubsub_topic = config.get("pubsub_topic", "")
    use_push = bool(pubsub_topic)

    if use_push:
        start_webhook_server(port=config.get("webhook_port", 8765))
        setup_gmail_push(service, pubsub_topic)
        log.info("Running in PUSH mode - will check instantly when Gmail notifies us")
    else:
        log.info(f"Running in POLL mode - checking every {poll_interval}s")
        log.info("Tip: add 'pubsub_topic' to config.json for instant push notifications")

    def run_check():
        """Run one full email check cycle."""
        nonlocal consecutive_errors, service
        try:
            log.info("Checking for new emails...")
            new_emails = fetch_new_emails(service, seen, config)
            save_seen(seen)
            consecutive_errors = 0

            for email_info in new_emails:
                mid  = email_info["id"]
                link = email_info["link"]
                log.info(f"Processing email {mid}...")
                try:
                    if link:
                        log.info(">> Mode B: JoinedUp web form")
                        success = claim_via_web(link, email_info["body"], config)
                    else:
                        log.warning("No JoinedUp link found - skipping")
                        success = False
                    if success:
                        log.info(f"Shift claimed! (email {mid})")
                    else:
                        log.warning(f"Failed on email {mid} - check screenshots/")
                except Exception as e:
                    log.error(f"Error on email {mid}: {e}")

            try:
                check_for_allocations(service, seen_all, config)
            except Exception as e:
                log.error(f"Allocation check error: {e}")

        except Exception as e:
            consecutive_errors += 1
            log.error(f"Loop error ({consecutive_errors}): {e}")
            if "token" in str(e).lower() or "auth" in str(e).lower():
                log.info("Refreshing Gmail credentials...")
                try:
                    service = get_gmail_service()
                    log.info("Credentials refreshed")
                except Exception as re:
                    log.error(f"Could not refresh: {re}")
            if consecutive_errors >= 5:
                log.warning("5 errors - pausing 2 minutes...")
                time.sleep(120)
                consecutive_errors = 0
nfhj  cv
    while True:
        run_check()

        if use_push:
            # Wait for Gmail push signal OR fall back to polling every 30s
            # (30s fallback in case a push is missed)
            triggered = _new_email_event.wait(timeout=30)
            _new_email_event.clear()
            if triggered:
                log.info("Push triggered - checking immediately!")
            else:
                log.info("Fallback poll (30s)...")
        else:
            log.info(f"Next check in {poll_interval}s...")
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()