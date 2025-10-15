# book.py
# Telegram Bookstore Bot - full features as requested
# Requirements: python-telegram-bot==20.6, aiosqlite

import logging
from uuid import uuid4
from functools import wraps
from datetime import datetime
import aiosqlite
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import asyncio

# ---------------- CONFIG ----------------
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"   # <<--- REPLACE with your token (do NOT share token publicly)
ADMIN_ID = 0                        # <<--- put your Telegram user id here (or run /setadmin to claim)
DB_PATH = "books_bot.db"
# ----------------------------------------

# Conversation states for /addbook
(ADD_LANG, ADD_TITLE, ADD_USD, ADD_INR, ADD_FILE) = range(5)

# Logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ----------------- HELPERS -----------------
def admin_only(func):
    @wraps(func)
    async def inner(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        global ADMIN_ID
        uid = update.effective_user.id
        if ADMIN_ID == 0:
            await update.effective_message.reply_text("Admin not set. Use /setadmin to claim admin (first user becomes admin).")
            return
        if uid != ADMIN_ID:
            await update.effective_message.reply_text("❌ Ye command sirf admin ke liye hai.")
            return
        return await func(update, context, *a, **kw)
    return inner


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS books(
                id TEXT PRIMARY KEY,
                title TEXT,
                lang TEXT,
                price_usd REAL,
                price_inr REAL,
                file_id TEXT,
                cover_id TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                user_name TEXT,
                book_id TEXT,
                status TEXT,
                evidence TEXT,
                created_at TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                purchases INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS config(
                k TEXT PRIMARY KEY,
                v TEXT
            )"""
        )
        await db.commit()


async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO config(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        await db.commit()


async def get_config(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT v FROM config WHERE k=?", (key,))
        r = await cur.fetchone()
        return r[0] if r else None


# ----------------- HANDLERS -----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📚 Welcome! Choose an option:"
    kb = [
        [InlineKeyboardButton("📖 Books", callback_data="books")],
        [InlineKeyboardButton("👤 My Info", callback_data="myinfo")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "books":
        kb = [
            [InlineKeyboardButton("Hindi", callback_data="lang_hindi"),
             InlineKeyboardButton("English", callback_data="lang_english")]
        ]
        await q.message.reply_text("Choose language:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "myinfo" or data == "myinfo_btn":
        uid = q.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT purchases FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            purchases = row[0] if row else 0
            cur2 = await db.execute(
                "SELECT books.title FROM orders JOIN books ON orders.book_id=books.id WHERE orders.user_id=? AND orders.status='approved'",
                (uid,),
            )
            bought = await cur2.fetchall()
            names = ", ".join(r[0] for r in bought) if bought else "None"
        await q.message.reply_text(f"🧾 Purchases: {purchases}\nBooks: {names}")
        return

    if data.startswith("lang_"):
        lang = data.split("_", 1)[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, title, price_usd, price_inr, cover_id FROM books WHERE lang=?", (lang,))
            rows = await cur.fetchall()
        if not rows:
            await q.message.reply_text(f"No {lang} books available yet.")
            return
        # send each book as a message with buy button
        for r in rows:
            bid, title, usd, inr, cover = r
            kb = [[InlineKeyboardButton(f"Buy — ₹{inr} / ${usd}", callback_data=f"buy_{bid}")]]
            caption = f"*{title}*\nPrice: ₹{inr} / ${usd}"
            if cover:
                try:
                    await q.message.reply_photo(photo=cover, caption=caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
                except:
                    await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            else:
                # placeholder cover if none
                await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("buy_"):
        book_id = data.split("_", 1)[1]
        await start_buy_flow(update, context, book_id)
        return

    if data.startswith("approve_"):
        order_id = data.split("_", 1)[1]
        await admin_approve_order(update, context, order_id)
        return


# ---- Buy flow (button or /buy) ----
async def start_buy_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, book_id: str):
    # works for callback_query (preferred) or message
    user = update.effective_user
    is_cb = update.callback_query is not None

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT title, price_usd, price_inr, file_id FROM books WHERE id=?", (book_id,))
        row = await cur.fetchone()
    if not row:
        msg = "Book not found."
        if is_cb:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    title, price_usd, price_inr, file_id = row
    upi = await get_config("upi")
    if not upi:
        msg = "Payment UPI not set by admin yet. Please try later."
        if is_cb:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    order_id = str(uuid4())[:8]
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,user_name,book_id,status,evidence,created_at) VALUES(?,?,?,?,?,?,?)",
            (order_id, user.id, user.full_name, book_id, "pending", "", now),
        )
        await db.commit()

    pay_text = (
        f"🧾 *Payment Instructions*\n\n"
        f"Book: *{title}*\nOrder ID: `{order_id}`\n"
        f"Amount: ₹{price_inr} / ${price_usd}\n\n"
        f"UPI ID: `{upi}`\n\n"
        "1) Send payment using the UPI ID above.\n"
        "2) After payment, use `/evidence {order_id}` and reply with payment screenshot or txn id so admin can verify.\n\n"
        "Admin will approve and the book will be delivered."
    )

    if is_cb:
        await update.callback_query.message.reply_text(pay_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(pay_text, parse_mode="Markdown")

    # notify admin with Approve button (admin can approve manually later)
    if ADMIN_ID:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve (Payment received)", callback_data=f"approve_{order_id}")]])
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🆕 New Order `{order_id}`\nUser: {user.full_name} ({user.id})\nBook: {title}\nStatus: pending",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Failed to notify admin: %s", e)


# ---- /buy <book name> command ----
async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /buy <book name>")
        return
    name = " ".join(context.args).strip()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title FROM books WHERE lower(title)=?", (name.lower(),))
        row = await cur.fetchone()
        if not row:
            cur = await db.execute("SELECT id, title FROM books WHERE title LIKE ?", (f"%{name}%",))
            row = await cur.fetchone()
    if not row:
        await update.message.reply_text("Book not found.")
        return
    book_id = row[0]
    await start_buy_flow(update, context, book_id)


# ---- Evidence command: user forwards payment screenshot/text to admin ----
async def evidence_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /evidence <order_id> (then reply to this command with the screenshot or send txn id).")
        return
    order_id = context.args[0].strip()
    # Expect user to reply to command with a media or text; if they send media separately, they can do "/evidence <id>" then attach: we support both by forwarding message if reply_to_message exists
    # We will forward any attached photo/document/text (replying or message after) - for simplicity, if user invoked /evidence with a reply, we forward the replied message; else we record the text after the command.
    if update.message.reply_to_message:
        target = update.message.reply_to_message
        # forward the replied message to admin with context
        if ADMIN_ID:
            await context.bot.send_message(ADMIN_ID, f"📨 Payment evidence for order `{order_id}` from {update.effective_user.full_name} ({update.effective_user.id})", parse_mode="Markdown")
            try:
                await context.bot.forward_message(chat_id=ADMIN_ID, from_chat_id=update.effective_chat.id, message_id=target.message_id)
                await update.message.reply_text("Evidence forwarded to admin.")
            except Exception as e:
                logger.exception(e)
                await update.message.reply_text("Could not forward evidence. Send evidence as file or text.")
        else:
            await update.message.reply_text("Admin not set. Use /setadmin first.")
        # update DB evidence link/text
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE orders SET evidence=? WHERE id=?", ("forwarded_by_user", order_id))
            await db.commit()
    else:
        # no reply; maybe user put txn id after command: join remaining text
        txt = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        # if user put only order_id and no evidence, instruct them
        if not txt:
            await update.message.reply_text("Please reply to this command with payment screenshot or append txn id after order id.")
            return
        # send text to admin
        if ADMIN_ID:
            await context.bot.send_message(ADMIN_ID, f"📨 Payment evidence (text) for order `{order_id}` from {update.effective_user.full_name} ({update.effective_user.id}):\n\n{txt}")
            await update.message.reply_text("Evidence sent to admin.")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE orders SET evidence=? WHERE id=?", (txt, order_id))
                await db.commit()
        else:
            await update.message.reply_text("Admin not set. Use /setadmin first.")


# ---- Admin approves an order (callback) ----
async def admin_approve_order(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id != ADMIN_ID:
        await q.message.reply_text("Only admin can approve.")
        return
    # fetch order
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, book_id, status FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await q.message.reply_text("Order not found.")
            return
        user_id, book_id, status = row
        if status == "approved":
            await q.message.reply_text("Order already approved.")
            return
        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        # increment user's purchases
        await db.execute(
            "INSERT INTO users(user_id, username, purchases) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET purchases = users.purchases + 1",
            (user_id, str(user_id)),
        )
        # fetch book file and title
        cur2 = await db.execute("SELECT file_id, title FROM books WHERE id=?", (book_id,))
        br = await cur2.fetchone()
        await db.commit()

    if not br:
        await q.message.reply_text("Book not found in DB.")
        return
    file_id, title = br

    # send file to user
    try:
        await context.bot.send_message(chat_id=user_id, text=f"✅ Payment approved!\nHere is your file for *{title}*.\nOrder ID: `{order_id}`", parse_mode="Markdown")
        await context.bot.send_document(chat_id=user_id, document=file_id)
    except Exception as e:
        logger.exception("Failed to send file: %s", e)
        await q.message.reply_text("Could not deliver file to user (maybe user blocked the bot).")

    await q.message.reply_text(f"Order {order_id} approved and delivered.")


# ----------------- ADMIN/UTILITY COMMANDS -----------------
async def setadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_ID
    uid = update.effective_user.id
    if ADMIN_ID == 0:
        ADMIN_ID = uid
        await update.message.reply_text(f"You are now admin. ID: {ADMIN_ID}")
        return
    # if current admin calls and provides new id
    if uid == ADMIN_ID and context.args:
        try:
            new_id = int(context.args[0])
            ADMIN_ID = new_id
            await update.message.reply_text(f"Admin changed to {new_id}")
        except:
            await update.message.reply_text("Provide numeric telegram user id.")
    else:
        await update.message.reply_text(f"Current admin id: {ADMIN_ID}")


@admin_only
async def setupi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setupi <upi-id>")
        return
    upi = context.args[0]
    await set_config("upi", upi)
    await update.message.reply_text(f"UPI set to: `{upi}`", parse_mode="Markdown")


@admin_only
async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin should reply to this command with photo or pass a public url as argument
    if update.message.reply_to_message and update.message.reply_to_message.photo:
        # take the largest photo file_id
        photo = update.message.reply_to_message.photo[-1]
        cover_id = photo.file_id
        await set_config("qr", cover_id)
        await update.message.reply_text("QR image saved (from replied photo).")
        return
    if context.args and (context.args[0].startswith("http://") or context.args[0].startswith("https://")):
        await set_config("qr", context.args[0])
        await update.message.reply_text("QR image saved (public URL).")
        return
    await update.message.reply_text("Reply to this command with a photo of the QR or pass a public URL: /setqr <url>")


# ----------------- ADD BOOK CONVERSATION -----------------
async def addbook_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ADMIN_ID != 0 and uid != ADMIN_ID:
        await update.message.reply_text("Only admin can add books.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton("Hindi", callback_data="addlang_hindi"),
           InlineKeyboardButton("English", callback_data="addlang_english")]]
    await update.message.reply_text("Choose language for the new book:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LANG


async def addbook_lang_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.split("_", 1)[1]
    context.user_data["add_lang"] = lang
    await q.message.reply_text("Send book title (text):")
    return ADD_TITLE


async def addbook_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    context.user_data["add_title"] = title
    await update.message.reply_text("Price in USD (e.g., 2.99):")
    return ADD_USD


async def addbook_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        usd = float(update.message.text.strip())
    except:
        await update.message.reply_text("Invalid number. Send price in USD (e.g., 2.99).")
        return ADD_USD
    context.user_data["add_usd"] = usd
    await update.message.reply_text("Price in INR (e.g., 199):")
    return ADD_INR


async def addbook_inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        inr = float(update.message.text.strip())
    except:
        await update.message.reply_text("Invalid number. Send price in INR (e.g., 199).")
        return ADD_INR
    context.user_data["add_inr"] = inr
    await update.message.reply_text("Now send the book file (as document) or a public file URL:")
    return ADD_FILE


async def addbook_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.text and (update.message.text.startswith("http://") or update.message.text.startswith("https://")):
        file_id = update.message.text.strip()
    else:
        await update.message.reply_text("Send a document or a public file URL.")
        return ADD_FILE

    bid = str(uuid4())[:8]
    lang = context.user_data["add_lang"]
    title = context.user_data["add_title"]
    usd = context.user_data["add_usd"]
    inr = context.user_data["add_inr"]
    # optional: attach cover from config qr (not required)
    cover = await get_config("qr")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO books(id,title,lang,price_usd,price_inr,file_id,cover_id) VALUES(?,?,?,?,?,?,?)",
            (bid, title, lang, usd, inr, file_id, cover),
        )
        await db.commit()

    await update.message.reply_text(f"✅ Book added: {title} (ID: {bid})")
    return ConversationHandler.END


async def addbook_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Add book cancelled.")
    return ConversationHandler.END


# ----------------- MYINFO command -----------------
async def myinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    async with aiosqli
