# book.py
import logging
import sqlite3
import aiosqlite
import asyncio
from uuid import uuid4
from functools import wraps
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)

# ---------------- CONFIG ----------------
TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"  # <--- apna bot token yahan daalo
ADMIN_ID = 0  # Pehli baar /setadmin chalane wala admin banega
DB_PATH = "books_bot.db"
# ----------------------------------------

# Conversation states for adding book
(ADD_LANG, ADD_TITLE, ADD_PRICE_USD, ADD_PRICE_INR, ADD_FILE) = range(5)

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        global ADMIN_ID
        uid = update.effective_user.id
        if ADMIN_ID == 0:
            await update.effective_message.reply_text(
                "Admin not set. Use /setadmin to claim admin (first user becomes admin)."
            )
            return
        if uid != ADMIN_ID:
            await update.effective_message.reply_text("❌ Ye command sirf admin ke liye hai.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


# ---------------- DB INIT ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                title TEXT,
                lang TEXT,
                price_usd REAL,
                price_inr REAL,
                file_id TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                user_name TEXT,
                book_id TEXT,
                status TEXT,
                note TEXT,
                created_at TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                purchases INTEGER DEFAULT 0
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS config (
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
        row = await cur.fetchone()
        return row[0] if row else None


# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📚 *Welcome to Book Store Bot!*\n\nChoose an option below:"
    keyboard = [
        [InlineKeyboardButton("📖 Books", callback_data="books")],
        [InlineKeyboardButton("👤 My Info", callback_data="myinfo")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "books":
        kb = [
            [
                InlineKeyboardButton("📕 Hindi Books", callback_data="lang_hindi"),
                InlineKeyboardButton("📘 English Books", callback_data="lang_english"),
            ]
        ]
        await query.message.reply_text("Choose language:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "myinfo":
        uid = query.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT purchases FROM users WHERE user_id=?", (uid,))
            row = await cur.fetchone()
            purchases = row[0] if row else 0
            cur2 = await db.execute(
                "SELECT title FROM orders JOIN books ON orders.book_id=books.id WHERE orders.user_id=? AND orders.status='approved'",
                (uid,),
            )
            bought = await cur2.fetchall()
            bought_names = ", ".join(r[0] for r in bought) if bought else "None"
        await query.message.reply_text(f"🧾 *Your Stats:*\n\nTotal purchases: {purchases}\nBooks: {bought_names}", parse_mode="Markdown")
        return

    if data.startswith("lang_"):
        lang = data.split("_", 1)[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, title, price_usd, price_inr FROM books WHERE lang=?", (lang,))
            rows = await cur.fetchall()
        if not rows:
            await query.message.reply_text(f"No {lang} books available yet.")
            return
        for r in rows:
            book_id, title, usd, inr = r
            kb = [
                [InlineKeyboardButton(f"💰 Buy — ₹{inr} / ${usd}", callback_data=f"buy_{book_id}")],
            ]
            await query.message.reply_photo(
                photo="https://via.placeholder.com/300x400.png?text=Book+Cover",
                caption=f"*{title}*\nPrice: ₹{inr} / ${usd}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode="Markdown"
            )
        return

    if data.startswith("buy_"):
        book_id = data.split("_", 1)[1]
        await start_buy_flow(update, context, book_id)
        return

    if data.startswith("approve_"):
        order_id = data.split("_", 1)[1]
        await handle_admin_approve(update, context, order_id)
        return


async def start_buy_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, book_id: str = None):
    is_callback = update.callback_query is not None
    user = update.effective_user

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, price_usd, price_inr, file_id FROM books WHERE id=?", (book_id,))
        row = await cur.fetchone()
    if not row:
        msg = "Book not found."
        if is_callback:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    bid, title, usd, inr, file_id = row
    upi = await get_config("upi")
    if not upi:
        msg = "Payment UPI not set by admin yet."
        if is_callback:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    order_id = str(uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,user_name,book_id,status,note,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            (order_id, user.id, user.full_name, bid, "pending", "",),
        )
        await db.commit()

    pay_text = (
        f"🧾 *Payment Instructions*\n\n"
        f"Book: *{title}*\nOrder ID: `{order_id}`\n\n"
        f"UPI ID: `{upi}`\n"
        f"Amount: ₹{inr} / ${usd}\n\n"
        "After payment, admin will approve and your book will be sent. ✅"
    )

    if is_callback:
        await update.callback_query.message.reply_text(pay_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(pay_text, parse_mode="Markdown")

    global ADMIN_ID
    if ADMIN_ID:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Approve Payment", callback_data=f"approve_{order_id}")]]
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 New Order: `{order_id}`\nUser: {user.full_name} ({user.id})\nBook: {title}\nStatus: Pending",
            parse_mode="Markdown",
            reply_markup=kb,
        )


async def handle_admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        await query.message.reply_text("❌ Only admin can approve.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, book_id, status FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await query.message.reply_text("Order not found.")
            return
        user_id, book_id, status = row
        if status == "approved":
            await query.message.reply_text("Already approved.")
            return

        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        await db.execute(
            "INSERT INTO users(user_id, username, purchases) VALUES(?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET purchases = users.purchases + 1",
            (user_id, str(user_id)),
        )
        await db.commit()
        cur2 = await db.execute("SELECT file_id, title FROM books WHERE id=?", (book_id,))
        br = await cur2.fetchone()
    if not br:
        await query.message.reply_text("Book missing in DB.")
        return
    file_id, title = br

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Payment approved!\nHere is your book: *{title}*\nOrder ID: `{order_id}`",
            parse_mode="Markdown",
        )
        await context.bot.send_document(chat_id=user_id, document=file_id)
    except Exception as e:
        logger.error(e)
        await query.message.reply_text("❗ Could not deliver file (user might have blocked bot).")
    await query.message.reply_text(f"Approved & delivered to user ({user_id}).")


# ---------------- COMMANDS ----------------
async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /buy <book name>")
        return
    name = " ".join(context.args).strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM books WHERE lower(title)=?", (name,))
        row = await cur.fetchone()
    if not row:
        await update.message.reply_text("Book not found.")
        return
    await start_buy_flow(update, context, row[0])


@admin_only
async def setupi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setupi <upi-id>")
        return
    upi = context.args[0]
    await set_config("upi", upi)
    await update.message.reply_text(f"✅ UPI set to: `{upi}`", parse_mode="Markdown")


async def setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_ID
    uid = update.effective_user.id
    if ADMIN_ID == 0:
        ADMIN_ID = uid
        await update.message.reply_text(f"✅ You are now admin. ID: {ADMIN_ID}")
    elif uid == ADMIN_ID and context.args:
        try:
            new_id = int(context.args[0])
            ADMIN_ID = new_id
            await update.message.reply_text(f"✅ Admin changed to {new_id}")
        except:
            await update.message.reply_text("Provide numeric ID.")
    else:
        await update.message.reply_text(f"Current admin ID: {ADMIN_ID}")


# --- Addbook Conversation ---
async def addbook_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ADMIN_ID != 0 and uid != ADMIN_ID:
        await update.message.reply_text("Only admin can add books.")
        return ConversationHandler.END
    kb = [[InlineKeyboardButton("Hindi", callback_data="addlang_hindi"),
           InlineKeyboardButton("English", callback_data="addlang_english")]]
    await update.message.reply_text("Select language:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LANG


async def addbook_lang_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["lang"] = q.data.split("_", 1)[1]
    await q.message.reply_text("Send book title:")
    return ADD_TITLE


async def addbook_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text("Price in USD:")
    return ADD_PRICE_USD


async def addbook_price_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["usd"] = float(update.message.text.strip())
        await update.message.reply_text("Price in INR:")
        return ADD_PRICE_INR
    except:
        await update.message.reply_text("Send a number (e.g. 2.99)")
        return ADD_PRICE_USD


async def addbook_price_inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["inr"] = float(update.message.text.strip())
        await update.message.reply_text("Now send the book file (PDF/doc) or a public link:")
        return ADD_FILE
    except:
        await update.message.reply_text("Send a number (e.g. 199)")
        return ADD_PRICE_INR


async def addbook_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.text and update.message.text.startswith(("http://", "https://")):
        file_id = update.message.text.strip()
    else:
        await update.message.reply_text("Send a document or valid URL.")
        return ADD_FILE

    bid = str(uuid4())[:8]
    lang = context.user_data["lang"]
    title = context.user_data["title"]
    usd = context.user_data["usd"]
    inr = context.user_data["inr"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO books(id,title,lang,price_usd,price_inr,file_id) VALUES(?,?,?,?,?,?)",
            (bid, title, lang, usd, inr, file_id),
        )
        await db.commit()

    await update.message.reply_text(f"✅ Added: {title} ({lang})\nID: {bid}")
    return ConversationHandler.END


async def cancel_addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ---------------- MAIN ----------------
async def main():
    await init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_router))
    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("setupi", setupi))
    app.add_handler(CommandHandler("setadmin", setadmin))

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("addbook", addbook_start)],
        states={
            ADD_LANG: [CallbackQueryHandler(addbook_lang_choice, pattern=r"^addlang_", block=False)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_title)],
            ADD_PRICE_USD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_usd)],
            ADD_PRICE_INR: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_inr)],
            ADD_FILE: [MessageHandler((filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, addbook_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel_addbook)],
        per_message=True,
    )
    app.add_handler(add_conv)

    logger.info("🚀 Bot starting...")
    await app.run_polling()


# Safe run fix for asyncio
if __name__ == "__main__":
    import asyncio
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
