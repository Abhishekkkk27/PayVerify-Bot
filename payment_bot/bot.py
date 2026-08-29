"""
PayVerify Bot — Telegram UPI payment verification bot.

Verifies UPI payments by matching amount + unique payment code
against Gmail payment notification emails.  Never trusts user-provided
screenshots or UTRs as proof.

Usage:
    python bot.py
"""

import asyncio
import imaplib
import email
import email.header
import logging
import os
import re
import secrets
import string
import threading
import uuid
from decimal import Decimal, InvalidOperation
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database
from qr_generator import generate_qr

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# ── Conversation states ──────────────────────────────────────────────────────

AWAITING_AMOUNT = 0

# ── Payment-code generator ───────────────────────────────────────────────────

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


def _generate_payment_code() -> str:
    """Generate a unique 6-character alphanumeric payment code using secrets."""
    for _ in range(100):  # virtually impossible to need more than 1 attempt
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if not database.payment_code_exists(code):
            return code
    raise RuntimeError("Unable to generate a unique payment code")


# ── Amount validation ────────────────────────────────────────────────────────

def _validate_amount(text: str) -> tuple[bool, Optional[Decimal], str]:
    """
    Validate a user-supplied amount string.

    Returns (ok, decimal_amount_or_None, error_message).
    """
    text = text.strip()
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return False, None, "Please enter a valid numeric amount."

    if amount <= 0:
        return False, None, "Amount must be greater than 0."

    if amount > config.MAX_AMOUNT:
        return False, None, f"Maximum amount is {config.MAX_AMOUNT} INR."

    # Check decimal places
    sign, digits, exponent = amount.as_tuple()
    if isinstance(exponent, int) and exponent < -config.MAX_DECIMAL_PLACES:
        return False, None, f"Maximum {config.MAX_DECIMAL_PLACES} decimal places allowed."

    # Normalise (remove trailing zeros for display while keeping precision)
    amount = amount.quantize(Decimal("0.01")) if exponent and exponent < 0 else amount

    return True, amount, ""


# ── User display name helper ─────────────────────────────────────────────────

def _display_name(update: Update) -> str:
    """Return the best available display name for the user."""
    user = update.effective_user
    if user is None:
        return "Unknown"
    if user.username:
        return f"@{user.username}"
    if user.first_name:
        return user.first_name
    return str(user.id)


# ══════════════════════════════════════════════════════════════════════════════
# Gmail IMAP verification
# ══════════════════════════════════════════════════════════════════════════════

_UTR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"UTR[\s:]*(\d{12,16})", re.IGNORECASE),
    re.compile(r"UPI\s*(?:Ref|Reference)\s*(?:No\.?|Number)?[\s:]*(\d{12,16})", re.IGNORECASE),
    re.compile(r"Transaction\s*(?:Ref|Reference|ID|Id)[\s:]*(\w{12,22})", re.IGNORECASE),
    re.compile(r"Ref(?:erence)?\s*(?:No\.?|Number)?[\s:]*(\d{12,16})", re.IGNORECASE),
]


def _decode_header_value(raw: Optional[str]) -> str:
    """Safely decode an RFC-2047 encoded email header."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    decoded: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _extract_text(msg: email.message.Message) -> str:
    """Extract plain-text body from a (possibly multipart) email."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try text/html
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        return ""


def _extract_utr(text: str) -> Optional[str]:
    """Attempt to extract a UTR / transaction reference from email text."""
    for pattern in _UTR_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def verify_payment_via_gmail(amount: Decimal, payment_code: str) -> dict:
    """
    Search Gmail IMAP inbox for a payment notification matching
    the given amount AND payment code.

    Returns a dict:
        {"verified": bool, "utr": str|None, "sender": str|None, "error": str|None}
    """
    result: dict = {"verified": False, "utr": None, "sender": None, "error": None}

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_EMAIL, config.GMAIL_APP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        logger.error("Gmail authentication failed: %s", exc)
        result["error"] = "Gmail authentication failed"
        return result
    except Exception as exc:
        logger.error("Gmail connection error: %s", exc)
        result["error"] = "Gmail connection error"
        return result

    try:
        mail.select("INBOX", readonly=True)

        # Search recent emails (last 50) to avoid downloading the entire mailbox
        status, data = mail.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            logger.info("No emails found in inbox")
            return result

        all_ids = data[0].split()
        # Check only the most recent 50 emails
        recent_ids = all_ids[-50:]

        amount_str = str(amount)
        # Also match without trailing zeros  e.g. "50" for Decimal("50.00")
        amount_plain = amount_str.rstrip("0").rstrip(".")

        for email_id in reversed(recent_ids):  # newest first
            try:
                status, msg_data = mail.fetch(email_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0]
                if isinstance(raw_email, tuple):
                    raw_bytes = raw_email[1]
                else:
                    continue

                msg = email.message_from_bytes(raw_bytes)

                subject = _decode_header_value(msg.get("Subject"))
                body = _extract_text(msg)
                full_text = f"{subject}\n{body}"

                # Both amount AND payment code must appear in the email
                code_found = payment_code in full_text

                amount_found = (
                    amount_str in full_text
                    or amount_plain in full_text
                )

                if code_found and amount_found:
                    utr = _extract_utr(full_text)
                    sender_raw = _decode_header_value(msg.get("From"))
                    result["verified"] = True
                    result["utr"] = utr
                    result["sender"] = sender_raw
                    logger.info(
                        "Payment verified via email — code=%s amount=%s utr=%s",
                        payment_code, amount, utr,
                    )
                    break

            except Exception:
                logger.exception("Error processing email ID %s", email_id)
                continue

    except Exception:
        logger.exception("Error searching Gmail inbox")
        result["error"] = "Error searching Gmail inbox"
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Telegram command & callback handlers
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start — welcome message with Pay button."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pay", callback_data="pay_start")],
    ])
    await update.message.reply_text(
        "Welcome! 💳\n\nPress the button below to make a payment.",
        reply_markup=keyboard,
    )
    return ConversationHandler.END


async def cmd_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /pay — prompt for amount."""
    await update.message.reply_text("Enter amount (₹):")
    return AWAITING_AMOUNT


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /help — show available commands."""
    text = (
        "Available commands:\n\n"
        "/start  — Welcome screen\n"
        "/pay    — Start a new payment\n"
        "/status — Check your latest payment status\n"
        "/cancel — Cancel your pending payment\n"
        "/help   — Show this help message\n"
    )
    await update.message.reply_text(text)
    return ConversationHandler.END


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /status — show the latest pending payment."""
    user_id = update.effective_user.id
    payment = database.get_pending_payment_by_user(user_id)
    if not payment:
        await update.message.reply_text("You have no pending payments.")
        return ConversationHandler.END

    text = (
        f"Payment Status\n\n"
        f"Amount: \u20b9{payment['amount']}\n"
        f"Code: {payment['payment_code']}\n"
        f"Status: {payment['status']}\n"
        f"Created: {payment['created_at']}\n"
    )
    await update.message.reply_text(text)
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel — cancel all pending payments for the user."""
    user_id = update.effective_user.id
    count = database.cancel_pending_payments(user_id)
    if count:
        await update.message.reply_text(f"Cancelled {count} pending payment(s).")
    else:
        await update.message.reply_text("You have no pending payments to cancel.")

    # Clear any conversation state
    context.user_data.clear()
    return ConversationHandler.END


# ── Callback: Pay button from /start ─────────────────────────────────────────

async def cb_pay_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """When the user taps the Pay inline button, ask for amount."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("Enter amount (₹):")
    return AWAITING_AMOUNT


# ── Amount entry ─────────────────────────────────────────────────────────────

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Validate amount, generate payment code & QR, send to user."""
    text = update.message.text or ""
    ok, amount, err = _validate_amount(text)

    if not ok or amount is None:
        await update.message.reply_text(f"Invalid amount: {err}\n\nEnter amount (₹):")
        return AWAITING_AMOUNT

    # Generate unique payment code & ID
    payment_code = _generate_payment_code()
    payment_id = uuid.uuid4().hex[:12]
    user = update.effective_user
    username = user.username if user else None

    # Persist to database
    database.create_payment(
        payment_id=payment_id,
        telegram_user_id=user.id,
        username=username,
        amount=amount,
        payment_code=payment_code,
    )

    # Store in conversation context for the verify callback
    context.user_data["active_payment_code"] = payment_code

    # Generate QR
    qr_path: Optional[str] = None
    try:
        qr_path = generate_qr(amount, payment_code)

        caption = (
            f"\U0001f4b3 Payment Request\n\n"
            f"Amount: \u20b9{amount}\n"
            f"Payment Code: {payment_code}\n\n"
            f"Scan the QR and complete the payment.\n\n"
            f"After payment, press Verify."
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Verify Payment",
                callback_data=f"verify_{payment_code}",
            )],
        ])

        with open(qr_path, "rb") as qr_file:
            await update.message.reply_photo(
                photo=qr_file,
                caption=caption,
                reply_markup=keyboard,
            )

    except RuntimeError:
        await update.message.reply_text(
            "An error occurred generating the QR code. Please try again with /pay."
        )
        logger.exception("QR generation error for code %s", payment_code)

    finally:
        # Clean up temp file
        if qr_path and os.path.exists(qr_path):
            try:
                os.remove(qr_path)
            except OSError:
                logger.warning("Could not delete temp QR file: %s", qr_path)

    return ConversationHandler.END


# ── Callback: Verify Payment ─────────────────────────────────────────────────

async def cb_verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the Verify Payment button press."""
    query = update.callback_query
    await query.answer()

    # Extract payment code from callback data
    callback_data = query.data or ""
    if not callback_data.startswith("verify_"):
        await query.message.reply_text("Invalid verification request.")
        return

    payment_code = callback_data[len("verify_"):]
    user_id = update.effective_user.id

    # Fetch payment from database
    payment = database.get_payment_by_code(payment_code)
    if not payment:
        await query.message.reply_text("Payment not found.")
        return

    # Only the creator can verify
    if payment["telegram_user_id"] != user_id:
        await query.message.reply_text("You are not authorised to verify this payment.")
        return

    # Prevent duplicate verification
    if payment["status"] == "VERIFIED":
        await query.message.reply_text(
            f"This payment has already been verified.\n\n"
            f"Amount: \u20b9{payment['amount']}\n"
            f"UTR: {payment['utr'] or 'N/A'}\n"
            f"Code: {payment['payment_code']}"
        )
        return

    # Mark as CHECKING
    database.update_payment_status(payment_code, "CHECKING")

    await query.message.reply_text("Checking payment... Please wait.")

    amount = Decimal(payment["amount"])

    # ── Gmail verification (run in thread to avoid blocking the event loop) ──
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, verify_payment_via_gmail, amount, payment_code
    )

    if result.get("error"):
        database.update_payment_status(payment_code, "PENDING")
        await query.message.reply_text(
            "⚠️ Verification service is temporarily unavailable. Please try again later."
        )
        return

    if result["verified"]:
        utr = result.get("utr")
        sender = result.get("sender")
        database.update_payment_status(payment_code, "VERIFIED", utr=utr, sender=sender)

        user_display = _display_name(update)

        success_text = (
            f"Payment Verified \u2705\n\n"
            f"Amount: \u20b9{amount}\n"
            f"UTR: {utr or 'N/A'}\n"
            f"Payment Code: {payment_code}"
        )
        await query.message.reply_text(success_text)

        # ── Send log to logs channel ─────────────────────────────────────
        log_text = (
            f"\u2705 Payment Verified\n\n"
            f"User: {user_display}\n"
            f"Amount: \u20b9{amount}\n"
            f"UTR: {utr or 'N/A'}\n"
            f"Payment Code: {payment_code}"
        )
        try:
            await context.bot.send_message(
                chat_id=config.LOGS_ID,
                text=log_text,
            )
        except Exception:
            logger.exception("Failed to send log message to %s", config.LOGS_ID)

    else:
        # Payment not found — reset to PENDING so user can retry
        database.update_payment_status(payment_code, "PENDING")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✅ Verify Payment",
                callback_data=f"verify_{payment_code}",
            )],
        ])
        await query.message.reply_text(
            "❌ Payment not detected.\n\n"
            "Please make sure the payment is completed and try Verify again.",
            reply_markup=keyboard,
        )


# ── Fallback for unexpected text during conversation ─────────────────────────

async def fallback_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel during the conversation flow."""
    context.user_data.clear()
    await update.message.reply_text("Payment flow cancelled. Use /pay to start again.")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# Application entry point
# ══════════════════════════════════════════════════════════════════════════════

def _build_application() -> Application:
    """Build the Telegram Application with all handlers registered."""
    app = Application.builder().token(config.BOT_TOKEN).build()

    # ── Conversation handler (amount entry) ──────────────────────────────
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("pay", cmd_pay),
            CommandHandler("help", cmd_help),
            CommandHandler("status", cmd_status),
            CallbackQueryHandler(cb_pay_start, pattern=r"^pay_start$"),
        ],
        states={
            AWAITING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", fallback_cancel),
            CommandHandler("start", cmd_start),
            CommandHandler("help", cmd_help),
            CommandHandler("status", cmd_status),
        ],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(conv_handler)

    # ── Standalone callback handler for verify (works outside conversation) ──
    app.add_handler(CallbackQueryHandler(cb_verify_payment, pattern=r"^verify_"))

    return app


# ── Webhook mode (Render production) ─────────────────────────────────────────

def _run_webhook() -> None:
    """
    Start the bot in webhook mode behind a Flask server.

    Flask serves:
      GET  /health        → health check for Render
      POST /<BOT_TOKEN>   → Telegram webhook updates

    A background thread runs the python-telegram-bot event loop to process
    incoming updates forwarded from the Flask route.
    """
    from flask import Flask, request, Response

    webhook_url = f"{config.RENDER_EXTERNAL_URL}/{config.BOT_TOKEN}"
    logger.info("Webhook mode — URL: %s", webhook_url.split('/')[0] + '//***')

    # Create a dedicated event loop for the bot on a background thread
    loop = asyncio.new_event_loop()
    application = _build_application()

    async def _startup() -> None:
        """Initialize the application and register the webhook with Telegram."""
        await application.initialize()
        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)
        await application.start()
        logger.info("Telegram webhook registered successfully")

    loop.run_until_complete(_startup())

    # Run the event loop in a daemon thread so it can process updates
    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    bot_thread = threading.Thread(target=_run_loop, daemon=True)
    bot_thread.start()

    # ── Flask app ────────────────────────────────────────────────────────
    flask_app = Flask(__name__)

    @flask_app.route("/health", methods=["GET"])
    def health():
        return Response("OK", status=200)

    @flask_app.route(f"/{config.BOT_TOKEN}", methods=["POST"])
    def telegram_webhook():
        """Receive Telegram updates via webhook and forward to the bot."""
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, application.bot)

        # Schedule processing on the bot's event loop
        asyncio.run_coroutine_threadsafe(
            application.process_update(update), loop
        )
        return Response("ok", status=200)

    logger.info("Starting Flask on port %s", config.PORT)
    flask_app.run(host="0.0.0.0", port=config.PORT)


# ── Polling mode (local development) ─────────────────────────────────────────

def _run_polling() -> None:
    """Start the bot in long-polling mode for local development."""
    application = _build_application()
    logger.info("Bot started — polling mode (LOCAL_MODE=true)")
    application.run_polling(drop_pending_updates=True)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    """Initialise the database and start the bot in the appropriate mode."""
    database.init_db()

    if config.LOCAL_MODE:
        _run_polling()
    else:
        if not config.RENDER_EXTERNAL_URL:
            logger.critical(
                "RENDER_EXTERNAL_URL is required when LOCAL_MODE is not true. "
                "Set it in your Render environment variables."
            )
            raise SystemExit(1)
        _run_webhook()


if __name__ == "__main__":
    main()
