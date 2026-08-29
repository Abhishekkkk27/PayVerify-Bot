# PayVerify Bot

A Telegram bot that accepts UPI payments and verifies them automatically by checking Gmail payment notification emails.

## How It Works

```
/start  →  💳 Pay button
        →  User enters INR amount
        →  Bot generates unique payment code + UPI QR
        →  User scans QR and pays via any UPI app
        →  User presses ✅ Verify Payment
        →  Bot checks Gmail inbox for matching email
        →  If amount + payment code match → ✅ Verified
        →  Payment log sent to your Telegram logs channel
```

**The bot never trusts screenshots or user-supplied UTRs.** Verification happens only through matching payment notification emails in your Gmail inbox.

---

## Prerequisites

- **Python 3.12+** — [Download](https://www.python.org/downloads/)
- A **Telegram Bot** (created via [@BotFather](https://t.me/BotFather))
- A **Gmail account** with an App Password
- A **UPI ID** (e.g. `yourname@upi`)

---

## Installation

### 1. Clone or download the project

```bash
cd payment_bot
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it:

- **Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Windows (CMD):**
  ```cmd
  .venv\Scripts\activate.bat
  ```
- **macOS / Linux:**
  ```bash
  source .venv/bin/activate
  ```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## Configuration

### 4. Create your `.env` file

```bash
copy .env.example .env
```

Open `.env` and fill in every value:

```env
BOT_TOKEN=123456:ABC-DEF...
LOGS_ID=-100123456789
UPI_ID=yourname@upi
UPI_NAME=Your Name
GMAIL_EMAIL=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
```

### 5. Get your Telegram BOT_TOKEN

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the token — it looks like `123456789:ABCdefGHI...`.
4. Paste it as `BOT_TOKEN` in `.env`.

### 6. Get your Telegram LOGS_ID

This is the chat or channel where the bot will post verified payment logs.

**Option A — Channel:**
1. Create a Telegram channel (can be private).
2. Add your bot as an admin with "Post Messages" permission.
3. Forward a message from the channel to [@userinfobot](https://t.me/userinfobot) or use [@RawDataBot](https://t.me/RawDataBot) to get the channel ID.
4. Channel IDs typically look like `-100XXXXXXXXXX`.

**Option B — Group:**
1. Create a group and add the bot.
2. Send a message in the group.
3. Use the Telegram Bot API: `https://api.telegram.org/bot<TOKEN>/getUpdates` to find the `chat.id`.

**Option C — Your own chat:**
1. Send any message to your bot.
2. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and find your `chat.id`.

Paste the ID as `LOGS_ID` in `.env`.

### 7. Configure UPI

- `UPI_ID` — Your UPI address (e.g. `merchant@upi`, `9876543210@paytm`).
- `UPI_NAME` — Display name shown on the QR (e.g. `My Store`).

### 8. Create a Gmail App Password

> **You must use a Gmail App Password, NOT your regular Gmail password.**

1. Go to [myaccount.google.com](https://myaccount.google.com/).
2. Navigate to **Security → 2-Step Verification** (enable it if not already).
3. Under 2-Step Verification, find **App passwords**.
4. Generate a new app password for "Mail".
5. Copy the 16-character password (e.g. `abcd efgh ijkl mnop`).
6. Paste it as `GMAIL_APP_PASSWORD` in `.env`.
7. Set `GMAIL_EMAIL` to the same Gmail address.

**Important:** For verification to work, UPI payment notifications must arrive in this Gmail inbox. Most banks/UPI apps can be configured to send email alerts.

---

## Running the Bot

### 9. Start the bot

```bash
python bot.py
```

The bot runs in **polling mode** — no public URL or webhook is needed.

You should see:

```
Bot started — polling mode
```

### 10. Test with a small payment

1. Open Telegram and find your bot.
2. Send `/start` → tap **💳 Pay**.
3. Enter `1` (₹1 test payment).
4. Scan the QR code with any UPI app and complete the ₹1 payment.
5. Wait for the payment notification email to arrive in your Gmail.
6. Tap **✅ Verify Payment**.
7. If the email is found with matching amount + payment code → **✅ Verified**.

---

## How Verification Works

1. When the user presses **Verify**, the bot connects to your Gmail via IMAP (SSL).
2. It searches the **most recent 50 emails** in the inbox.
3. For each email, it checks the subject and body for:
   - The exact **payment amount**.
   - The unique **payment code** (which was set as the UPI transaction note).
4. **Both** must be present in the same email for verification to pass.
5. The bot also attempts to extract the **UTR** (UPI Transaction Reference) from the email.
6. A verified payment log is sent to your configured logs channel.

**The bot never:**
- Accepts user-provided screenshots as proof.
- Accepts user-provided UTR numbers as proof.
- Verifies based on amount alone.
- Verifies based on sender name alone.
- Asks for Gmail passwords, UPI PINs, OTPs, card numbers, CVVs, or bank passwords.

---

## Bot Commands

| Command   | Description                          |
|-----------|--------------------------------------|
| `/start`  | Welcome screen with Pay button       |
| `/pay`    | Start a new payment                  |
| `/status` | Check your latest pending payment    |
| `/cancel` | Cancel your pending payment          |
| `/help`   | Show available commands              |

---

## Project Structure

```
payment_bot/
├── bot.py              # Main bot — handlers, Gmail verification, entry point
├── config.py           # Loads environment variables
├── database.py         # SQLite database layer
├── qr_generator.py     # UPI QR code generation
├── requirements.txt    # Python dependencies
├── .env.example        # Template for environment variables
├── .gitignore          # Files excluded from Git
└── README.md           # This file
```

---

## Security Precautions

### Never commit secrets
- `.env` is in `.gitignore` — never override this.
- Never paste your `BOT_TOKEN`, `GMAIL_APP_PASSWORD`, or `UPI_ID` in code.

### Before pushing to GitHub
1. **Double-check** that `.env` is not tracked: `git status` should not list it.
2. **Never** force-add `.env` with `git add -f .env`.
3. If you accidentally committed secrets:
   - Immediately **revoke** the leaked token/password.
   - Remove the file from Git history using `git filter-branch` or BFG Repo Cleaner.
   - Generate new credentials.

### Gmail security
- Use a **dedicated Gmail account** for bot notifications if possible.
- The App Password grants IMAP access only — it does not expose your full Google account.
- Rotate the App Password periodically.

### UPI safety
- The bot generates QR codes pointing to **your** UPI ID.
- It never asks users for their UPI PIN, OTP, or banking credentials.

---

## Deployment

The bot is designed for **local polling mode** first. For production deployment:

1. Use a VPS or cloud instance (e.g. AWS EC2, DigitalOcean, Railway).
2. Run `python bot.py` inside a `tmux`/`screen` session or use `systemd`.
3. Ensure `.env` is present on the server but not in the repository.
4. The SQLite database file (`payments.db`) is created automatically.

For high-traffic scenarios, consider switching to PostgreSQL and webhook mode.

---

## Troubleshooting

| Problem                           | Solution                                                    |
|-----------------------------------|-------------------------------------------------------------|
| Bot doesn't respond               | Check `BOT_TOKEN` is correct. Check internet connection.    |
| Gmail auth fails                  | Use App Password, not regular password. Check credentials.  |
| Payment not detected              | Ensure the notification email has arrived in Gmail first.    |
| QR won't scan                     | Check `UPI_ID` is a valid, active UPI address.              |
| Logs not appearing                | Check `LOGS_ID` and that the bot is admin in the channel.   |
| "Verification service unavailable"| Gmail IMAP may be temporarily down. Retry in a few minutes. |

---

## License

This project is provided as-is for educational and personal use.
