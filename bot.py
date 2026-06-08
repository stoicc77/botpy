"""
========================================================
  Telegram Channel Username/Link Replacer Bot
  Python version — Deploy on Railway
========================================================
  Replaces:  SSC_COMPILATIONS_2025
  With:      SSC_PYQ_COMPILATIONS

  Works on:
    - Plain text
    - Bold / italic text
    - @username mentions
    - https://t.me/... links
    - Hidden hyperlinks (entities)
    - ANY case (UPPER, lower, MiXeD)
========================================================
"""

import os
import re
import asyncio
import logging
from aiohttp import web
import aiohttp

BOT_TOKEN     = "8569663296:AAEHgKpJF7p9tcW_QlLW8oQ9K9ipKUixZF8"
ADMIN_USER_ID = 6880375007
OLD_USERNAME  = "SSC_COMPILATIONS_2025"
NEW_USERNAME  = "SSC_PYQ_COMPILATIONS"
PORT          = int(os.environ.get("PORT", "8080"))

# ── Logging setup ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

# ── Global job state ────────────────────────────────────────
job_state = {
    "running": False,
    "task": None,
}

# ── Telegram API base ────────────────────────────────────────
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ============================================================
#   TELEGRAM API HELPERS
# ============================================================

async def tg(method: str, **kwargs) -> dict:
    """Call any Telegram Bot API method."""
    url = f"{TG_API}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=kwargs) as resp:
            return await resp.json()


async def send(chat_id: int, text: str, **kwargs):
    """Send a message to admin DM."""
    return await tg(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        **kwargs
    )


async def delete_msg(chat_id: int, message_id: int):
    """Delete a message silently."""
    await tg("deleteMessage", chat_id=chat_id, message_id=message_id)


# ============================================================
#   REPLACEMENT LOGIC
# ============================================================

def apply_replacements(text: str) -> tuple[str, bool]:
    """
    Replace old username with new in text.
    Case-insensitive. Returns (new_text, was_changed).
    """
    if not text:
        return text, False

    original = text

    # Replace full links:  https://t.me/OLD/123  →  https://t.me/NEW/123
    text = re.sub(
        re.escape(f"https://t.me/{OLD_USERNAME}"),
        f"https://t.me/{NEW_USERNAME}",
        text,
        flags=re.IGNORECASE
    )

    # Replace @username mention
    text = re.sub(
        re.escape(f"@{OLD_USERNAME}"),
        f"@{NEW_USERNAME}",
        text,
        flags=re.IGNORECASE
    )

    # Replace bare username (no @ or link prefix)
    text = re.sub(
        r'(?<![/@])' + re.escape(OLD_USERNAME),
        NEW_USERNAME,
        text,
        flags=re.IGNORECASE
    )

    return text, text != original


def fix_entities(entities: list, text: str) -> tuple[list, bool]:
    """
    Fix hyperlink URLs inside Telegram message entities.
    Handles hidden hyperlinks like [JOIN](https://t.me/OLD/726)
    """
    if not entities:
        return entities, False

    changed = False
    new_entities = []

    for ent in entities:
        if ent.get("type") == "text_link" and ent.get("url"):
            new_url, c = apply_replacements(ent["url"])
            if c:
                changed = True
                ent = {**ent, "url": new_url}
        new_entities.append(ent)

    return new_entities, changed


def needs_replacement(text: str, entities: list) -> bool:
    """Quick check — does this message even contain the old username?"""
    if text and re.search(re.escape(OLD_USERNAME), text, re.IGNORECASE):
        return True
    if entities:
        for ent in entities:
            url = ent.get("url", "")
            if url and re.search(re.escape(OLD_USERNAME), url, re.IGNORECASE):
                return True
    return False


# ============================================================
#   EDIT MESSAGE IN CHANNEL
# ============================================================

async def edit_channel_message(
    channel_id: str,
    msg_id: int,
    text: str,
    entities: list,
    is_caption: bool = False
) -> dict:
    """Edit a channel message or caption with replaced content."""

    new_text, text_changed     = apply_replacements(text)
    new_entities, ent_changed  = fix_entities(entities or [], text)

    if not text_changed and not ent_changed:
        return {"skipped": True}

    if is_caption:
        method = "editMessageCaption"
        payload = dict(
            chat_id=channel_id,
            message_id=msg_id,
            caption=new_text,
        )
    else:
        method = "editMessageText"
        payload = dict(
            chat_id=channel_id,
            message_id=msg_id,
            text=new_text,
        )

    # Prefer sending entities over parse_mode to preserve formatting
    if new_entities:
        key = "caption_entities" if is_caption else "entities"
        payload[key] = new_entities
    else:
        payload["parse_mode"] = "HTML"

    return await tg(method, **payload)


# ============================================================
#   MAIN REPLACEMENT JOB
# ============================================================

async def run_replacement(admin_chat: int, channel_id: str, from_id: int, to_id: int):
    """
    Scan every message ID from from_id to to_id.
    Replace old username with new. Report progress to admin.
    """
    global job_state

    EDIT_DELAY   = 3.2   # seconds after a successful edit (Telegram limit: 20/min)
    SKIP_DELAY   = 0.15  # seconds for deleted/no-match messages (fast)
    REPORT_EVERY = 50    # send progress update every N message IDs

    replaced = 0
    skipped  = 0
    errors   = 0
    total    = to_id - from_id + 1

    log.info(f"Job started: {channel_id} | {from_id} → {to_id}")

    await send(
        admin_chat,
        f"🚀 <b>Job Started!</b>\n\n"
        f"📢 Channel: <code>{channel_id}</code>\n"
        f"📌 Range: <b>{from_id}</b> → <b>{to_id}</b> ({total} IDs)\n"
        f"🔴 Old: <code>@{OLD_USERNAME}</code>\n"
        f"🟢 New: <code>@{NEW_USERNAME}</code>\n\n"
        f"⏳ I'll update you every {REPORT_EVERY} IDs...\n"
        f"<i>Zero messages will be posted to your channel.</i>"
    )

    for msg_id in range(from_id, to_id + 1):

        # Check if job was stopped
        if not job_state["running"]:
            await send(
                admin_chat,
                f"🛑 <b>Job Stopped!</b>\n\n"
                f"✅ Replaced: <b>{replaced}</b>\n"
                f"⏭ Skipped: <b>{skipped}</b>\n"
                f"❌ Errors: <b>{errors}</b>\n"
                f"📌 Stopped at ID: <b>{msg_id}</b>"
            )
            return

        # ── Progress report ──────────────────────────────────
        done = msg_id - from_id
        if done > 0 and done % REPORT_EVERY == 0:
            pct = round((done / total) * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await send(
                admin_chat,
                f"📊 <b>Progress: {pct}%</b>\n"
                f"[{bar}]\n\n"
                f"✅ Replaced: <b>{replaced}</b>\n"
                f"⏭ Skipped: <b>{skipped}</b>\n"
                f"❌ Errors: <b>{errors}</b>\n"
                f"📌 Current ID: <b>{msg_id}</b> / {to_id}"
            )

        try:
            # ── Step 1: Forward message to admin DM to read it ──
            fwd = await tg(
                "forwardMessage",
                chat_id=admin_chat,
                from_chat_id=channel_id,
                message_id=msg_id
            )

            # Message deleted or doesn't exist
            if not fwd.get("ok"):
                skipped += 1
                await asyncio.sleep(SKIP_DELAY)
                continue

            fwd_msg    = fwd["result"]
            fwd_msg_id = fwd_msg["message_id"]

            # Get text (normal message or caption for media)
            text       = fwd_msg.get("text") or fwd_msg.get("caption") or ""
            entities   = fwd_msg.get("entities") or fwd_msg.get("caption_entities") or []
            is_caption = "caption" in fwd_msg

            # ── Step 2: Delete forwarded copy from admin DM ──────
            await delete_msg(admin_chat, fwd_msg_id)

            # ── Step 3: Check if replacement needed ──────────────
            if not needs_replacement(text, entities):
                skipped += 1
                await asyncio.sleep(SKIP_DELAY)
                continue

            # ── Step 4: Edit original channel message ────────────
            result = await edit_channel_message(
                channel_id, msg_id, text, entities, is_caption
            )

            if result.get("skipped"):
                skipped += 1
                await asyncio.sleep(SKIP_DELAY)

            elif result.get("ok"):
                replaced += 1
                log.info(f"✅ Replaced msg {msg_id}")
                await asyncio.sleep(EDIT_DELAY)  # rate limit safe delay

            else:
                err_code = result.get("error_code", 0)
                err_desc = result.get("description", "unknown")

                if err_code == 400:
                    # Message too old, or media without caption — can't edit
                    skipped += 1
                    log.info(f"⏭ Can't edit {msg_id}: {err_desc}")
                    await asyncio.sleep(SKIP_DELAY)
                elif err_code == 429:
                    # Rate limited — wait and retry once
                    retry_after = result.get("parameters", {}).get("retry_after", 10)
                    log.warning(f"⚠️ Rate limited. Waiting {retry_after}s...")
                    await asyncio.sleep(retry_after + 1)
                    retry = await edit_channel_message(
                        channel_id, msg_id, text, entities, is_caption
                    )
                    if retry.get("ok"):
                        replaced += 1
                    else:
                        errors += 1
                else:
                    errors += 1
                    log.error(f"❌ Error on {msg_id}: {err_code} {err_desc}")
                    await asyncio.sleep(SKIP_DELAY)

        except asyncio.CancelledError:
            break
        except Exception as e:
            errors += 1
            log.error(f"❌ Exception on {msg_id}: {e}")
            await asyncio.sleep(SKIP_DELAY)

    # ── Job complete ─────────────────────────────────────────
    job_state["running"] = False

    await send(
        admin_chat,
        f"🎉 <b>Job Complete!</b>\n\n"
        f"✅ Replaced: <b>{replaced}</b>\n"
        f"⏭ Skipped: <b>{skipped}</b>\n"
        f"❌ Errors: <b>{errors}</b>\n\n"
        f"🔴 <s>@{OLD_USERNAME}</s>\n"
        f"🟢 @{NEW_USERNAME}\n\n"
        f"<i>No messages were posted to your channel.</i> ✅"
    )
    log.info(f"Job done. Replaced={replaced} Skipped={skipped} Errors={errors}")


# ============================================================
#   COMMAND HANDLERS
# ============================================================

async def handle_update(update: dict):
    """Process incoming Telegram update."""
    global job_state

    msg = update.get("message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text    = msg.get("text", "").strip()

    # Security — only admin allowed
    if user_id != ADMIN_USER_ID:
        await send(chat_id, "⛔ Unauthorized.")
        return

    # ── /start ───────────────────────────────────────────────
    if text == "/start":
        await send(
            chat_id,
            f"👋 <b>Channel Replacer Bot</b>\n\n"
            f"🔴 Old: <code>@{OLD_USERNAME}</code>\n"
            f"🟢 New: <code>@{NEW_USERNAME}</code>\n\n"
            f"<b>Commands:</b>\n\n"
            f"<code>/replace @channel from_id to_id</code>\n"
            f"Start replacement job\n\n"
            f"<code>/status</code>\n"
            f"Check running job\n\n"
            f"<code>/stop</code>\n"
            f"Stop current job\n\n"
            f"<b>Example:</b>\n"
            f"<code>/replace @SSC_PYQ_COMPILATIONS 1 1500</code>"
        )

    # ── /status ──────────────────────────────────────────────
    elif text == "/status":
        if job_state["running"]:
            await send(chat_id, "⚙️ <b>Job is currently running.</b>\nSend /stop to stop it.")
        else:
            await send(chat_id, "✅ No job running.")

    # ── /stop ────────────────────────────────────────────────
    elif text == "/stop":
        if job_state["running"]:
            job_state["running"] = False
            if job_state["task"]:
                job_state["task"].cancel()
            await send(chat_id, "🛑 Stop signal sent. Will stop after current message.")
        else:
            await send(chat_id, "ℹ️ No job is running.")

    # ── /replace ─────────────────────────────────────────────
    elif text.startswith("/replace"):
        parts = text.split()

        if len(parts) < 4:
            await send(
                chat_id,
                "❌ <b>Wrong format!</b>\n\n"
                "Usage:\n<code>/replace @channel from_id to_id</code>\n\n"
                "Example:\n<code>/replace @SSC_PYQ_COMPILATIONS 1 1500</code>"
            )
            return

        channel_id = parts[1]
        try:
            from_id = int(parts[2])
            to_id   = int(parts[3])
        except ValueError:
            await send(chat_id, "❌ from_id and to_id must be numbers.")
            return

        if from_id > to_id:
            await send(chat_id, "❌ from_id must be less than to_id.")
            return

        if job_state["running"]:
            await send(chat_id, "⚠️ A job is already running! Send /stop first.")
            return

        # Start job
        job_state["running"] = True
        task = asyncio.create_task(
            run_replacement(chat_id, channel_id, from_id, to_id)
        )
        job_state["task"] = task

    else:
        await send(chat_id, "❓ Unknown command. Send /start to see commands.")


# ============================================================
#   WEBHOOK SERVER  (aiohttp)
# ============================================================

async def webhook_handler(request: web.Request) -> web.Response:
    """Receive updates from Telegram via webhook."""
    try:
        update = await request.json()
        asyncio.create_task(handle_update(update))
    except Exception as e:
        log.error(f"Webhook error: {e}")
    return web.Response(text="OK")


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint for Railway."""
    return web.Response(text="Bot is running ✅")


async def main():
    log.info("Starting bot...")

    # Set webhook
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    if webhook_url:
        res = await tg("setWebhook", url=f"{webhook_url}/webhook")
        log.info(f"Webhook set: {res}")
    else:
        log.warning("WEBHOOK_URL not set — webhook not registered")

    # Start web server
    app = web.Application()
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_get("/", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info(f"Bot running on port {PORT}")
    await asyncio.Event().wait()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
