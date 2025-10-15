# bot.py
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
    InputFile,
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
TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"
# Optionally set ADMIN_ID here (int). If 0, first user to run /setadmin becomes admin
ADMIN_ID = 0

DB_PATH = "books_bot.db"
# ----------------------------------------

# Conversation states for adding book
(ADD_LANG, ADD_TITLE, ADD_PRICE_USD, ADD_PRICE_INR, ADD_FILE) = range(5)

# setup logging
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
            await update.effective_message.reply_text("Ye command sirf admin ke liye hai.")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


# --- Database helpers (simple sqlite) ---
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


# --- Command handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Welcome! Choose an option:"
    keyboard = [
        [InlineKeyboardButton("Books", callback_data="books")],
        [InlineKeyboardButton("My Info", callback_data="myinfo")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "books":
        kb = [
            [
                InlineKeyboardButton("Hindi Books", callback_data="lang_hindi"),
                InlineKeyboardButton("English Books", callback_data="lang_english"),
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
        await query.message.reply_text(f"Total purchases: {purchases}\nBooks: {bought_names}")
        return

    if data.startswith("lang_"):
        lang = data.split("_", 1)[1]
        # fetch books of that language
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, title, price_usd, price_inr FROM books WHERE lang=?", (lang,))
            rows = await cur.fetchall()
        if not rows:
            await query.message.reply_text(f"No {lang} books available yet.")
            return
        for r in rows:
            book_id, title, usd, inr = r
            kb = [
                [InlineKeyboardButton(f"Buy — ₹{inr} / ${usd}", callback_data=f"buy_{book_id}")],
            ]
            await query.message.reply_photo(
                photo="https://via.placeholder.com/300x400.png?text=Book+Cover",  # placeholder cover
                caption=f"{title}\nPrice: ₹{inr} / ${usd}",
                reply_markup=InlineKeyboardMarkup(kb),
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
    # can be triggered via callback (update.callback_query) or /buy command (update.message)
    is_callback = update.callback_query is not None
    user = update.effective_user

    # find book
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, title, price_usd, price_inr, file_id FROM books WHERE id=?", (book_id,))
        row = await cur.fetchone()
    if not row:
        if is_callback:
            await update.callback_query.message.reply_text("Book not found.")
        else:
            await update.message.reply_text("Book not found.")
        return

    bid, title, usd, inr, file_id = row
    upi = await get_config("upi")
    if not upi:
        if is_callback:
            await update.callback_query.message.reply_text("Payment UPI not set by admin yet. Try later.")
        else:
            await update.message.reply_text("Payment UPI not set by admin yet. Try later.")
        return

    order_id = str(uuid4())[:8]
    # create pending order
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,user_name,book_id,status,note,created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            (order_id, user.id, user.full_name, bid, "pending", ""),
        )
        await db.commit()

    pay_text = (
        f"Payment instructions for *{title}*:\n\n"
        f"UPI ID: `{upi}`\n\n"
        "1) Send the exact amount.\n"
        f"2) After payment, send the UPI txn id / screenshot to the admin or wait for admin approval.\n\n"
        f"Order ID: `{order_id}`"
    )

    if is_callback:
        await update.callback_query.message.reply_text(pay_text, parse_mode="Markdown")
    else:
        await update.message.reply_text(pay_text, parse_mode="Markdown")

    # notify admin with approve button
    global ADMIN_ID
    if ADMIN_ID == 0:
        # no admin set
        logger.info("No admin to notify about order %s", order_id)
    else:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Approve (Payment received)", callback_data=f"approve_{order_id}")]]
        )
        # fetch user mention and book title
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"New order `{order_id}`\nUser: {user.full_name} ({user.id})\nBook: {title}\nStatus: pending",
            parse_mode="Markdown",
            reply_markup=kb,
        )


async def handle_admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: str = None):
    query = update.callback_query
    await query.answer()
    admin_id = update.effective_user.id
    if admin_id != ADMIN_ID:
        await query.message.reply_text("Sirf admin hi approve kar sakta hai.")
        return

    # fetch order
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id, book_id, status FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await query.message.reply_text("Order not found.")
            return
        user_id, book_id, status = row
        if status == "approved":
            await query.message.reply_text("Order already approved.")
            return
        # mark approved
        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        # increment user's purchases
        await db.execute(
            "INSERT INTO users(user_id, username, purchases) VALUES(?,?,1) ON CONFLICT(user_id) DO UPDATE SET purchases = users.purchases + 1",
            (user_id, str(user_id)),
        )
        await db.commit()

        # fetch book file_id and title
        cur2 = await db.execute("SELECT file_id, title FROM books WHERE id=?", (book_id,))
        br = await cur2.fetchone()
        if not br:
            await query.message.reply_text("Book not found (weird).")
            return
        file_id, title = br

    # send file to user
    try:
        if file_id.startswith("http") or file_id.startswith("https"):
            await context.bot.send_message(chat_id=user_id, text=f"Payment received ✅\nHere is your file for *{title}*.\n\n(Invoice ID: `{order_id}`)", parse_mode="Markdown")
            await context.bot.send_document(chat_id=user_id, document=file_id)
        else:
            # assume it's telegram file_id
            await context.bot.send_message(chat_id=user_id, text=f"Payment received ✅\nHere is your file for *{title}*.\n\n(Invoice ID: `{order_id}`)", parse_mode="Markdown")
            await context.bot.send_document(chat_id=user_id, document=file_id)
    except Exception as e:
        logger.exception("Failed to send file to user: %s", e)
        await query.message.reply_text("Could not deliver file to user (maybe blocked).")

    await query.message.reply_text(f"Order {order_id} approved and file sent.")


# ---- /buy command ----
async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /buy <book name>")
        return
    name = " ".join(context.args).strip()
    # find book by title (case-insensitive)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM books WHERE lower(title)=?", (name.lower(),))
        row = await cur.fetchone()
    if not row:
        # try partial match
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id, title FROM books WHERE title LIKE ?", (f"%{name}%",))
            row = await cur.fetchone()
        if not row:
            await update.message.reply_text("Book not found.")
            return
    book_id = row[0]
    await start_buy_flow(update, context, book_id)


# ---- /setupi admin ----
@admin_only
async def setupi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /setupi <upi-id>  (example: your@upi)")
        return
    upi = context.args[0]
    await set_config("upi", upi)
    await update.message.reply_text(f"UPI set to: `{upi}`", parse_mode="Markdown")


# ---- /setadmin (claim admin if none) ----
async def setadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_ID
    uid = update.effective_user.id
    if ADMIN_ID == 0:
        ADMIN_ID = uid
        await update.message.reply_text(f"You're now admin. Your id: {ADMIN_ID}")
    else:
        if uid != ADMIN_ID:
            await update.message.reply_text("Admin already set. Only current admin can change.")
        else:
            # allow admin to change by providing a new id
            if context.args:
                try:
                    new_id = int(context.args[0])
                    ADMIN_ID = new_id
                    await update.message.reply_text(f"Admin changed to {new_id}")
                except:
                    await update.message.reply_text("Provide numeric telegram user id.")
            else:
                await update.message.reply_text(f"Current admin id: {ADMIN_ID}")


# ---- /addbook conversation (admin only) ----
async def addbook_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if ADMIN_ID != 0 and uid != ADMIN_ID:
        await update.message.reply_text("Only admin can add books.")
        return ConversationHandler.END
    kb = [
        [InlineKeyboardButton("Hindi", callback_data="addlang_hindi"), InlineKeyboardButton("English", callback_data="addlang_english")]
    ]
    await update.message.reply_text("Choose language to add book in:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_LANG


async def addbook_lang_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    lang = data.split("_", 1)[1]
    context.user_data["new_book_lang"] = lang
    await q.message.reply_text("Send book title (text):")
    return ADD_TITLE


async def addbook_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    context.user_data["new_book_title"] = title
    await update.message.reply_text("Price in USD (e.g. 2.99):")
    return ADD_PRICE_USD


async def addbook_price_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        usd = float(txt)
    except:
        await update.message.reply_text("Invalid number. Send price in USD, e.g., 2.99")
        return ADD_PRICE_USD
    context.user_data["new_book_price_usd"] = usd
    await update.message.reply_text("Price in INR (e.g. 199):")
    return ADD_PRICE_INR


async def addbook_price_inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    try:
        inr = float(txt)
    except:
        await update.message.reply_text("Invalid number. Send price in INR, e.g., 199")
        return ADD_PRICE_INR
    context.user_data["new_book_price_inr"] = inr
    await update.message.reply_text("Now send the book file (PDF or any document) or send a public file URL.")
    return ADD_FILE


async def addbook_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    if update.message.document:
        file = update.message.document
        file_id = file.file_id
    elif update.message.text and (update.message.text.startswith("http://") or update.message.text.startswith("https://")):
        file_id = update.message.text.strip()
    else:
        await update.message.reply_text("Send a document or a public file URL.")
        return ADD_FILE

    # store book
    bid = str(uuid4())[:8]
    lang = context.user_data["new_book_lang"]
    title = context.user_data["new_book_title"]
    usd = context.user_data["new_book_price_usd"]
    inr = context.user_data["new_book_price_inr"]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO books(id,title,lang,price_usd,price_inr,file_id) VALUES(?,?,?,?,?,?)",
            (bid, title, lang, usd, inr, file_id),
        )
        await db.commit()

    await update.message.reply_text(f"Book added: {title} ({lang})\nID: {bid}")
    return ConversationHandler.END


async def cancel_addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Add book cancelled.")
    return ConversationHandler.END


# ---- /myinfo command direct ----
async def myinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
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
    await update.message.reply_text(f"Total purchases: {purchases}\nBooks: {bought_names}")


# ---- main ----
async def main():
    await init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_router))

    app.add_handler(CommandHandler("buy", buy_cmd))
    app.add_handler(CommandHandler("setupi", setupi))
    app.add_handler(CommandHandler("setadmin", setadmin))
    app.add_handler(CommandHandler("myinfo", myinfo_cmd))

    addbook_conv = ConversationHandler(
        entry_points=[CommandHandler("addbook", addbook_start)],
        states={
            ADD_LANG: [CallbackQueryHandler(addbook_lang_choice, pattern=r"^addlang_")],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_title)],
            ADD_PRICE_USD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_usd)],
            ADD_PRICE_INR: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_inr)],
            ADD_FILE: [MessageHandler((filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, addbook_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel_addbook)],
    )
    app.add_handler(addbook_conv)

    # start
    logger.info("Bot starting...")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
