import logging
import aiosqlite
import asyncio
from datetime import datetime
from functools import wraps
from uuid import uuid4
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
BOT_TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"  # <-- yahan apna bot token daalna
ADMIN_ID = 6944519938               # <-- apna Telegram user id daalna (int)
DB_PATH = "bookstore.db"
# ==========================================

# Conversation states
(ADD_LANG, ADD_TITLE, ADD_PRICE, ADD_FILE) = range(4)

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ================= UTILITIES =================
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **kw):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Ye command sirf admin ke liye hai.")
            return
        return await func(update, context, *a, **kw)
    return wrapped


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS books(
                id TEXT PRIMARY KEY,
                title TEXT,
                lang TEXT,
                price TEXT,
                file_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                user_name TEXT,
                book_id TEXT,
                status TEXT,
                created_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config(
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()


async def set_config(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_config(key):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM config WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


# ================= COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Books", callback_data="books")],
        [InlineKeyboardButton("👤 My Info", callback_data="myinfo")],
    ]
    await update.message.reply_text(
        "📖 *Welcome to Toxic BookStore!*\nSelect an option:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def setup_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Use: /setupi your_upi@okbank")
        return
    upi = context.args[0]
    await set_config("upi_id", upi)
    await update.message.reply_text(f"✅ UPI ID set to: `{upi}`", parse_mode="Markdown")


@admin_only
async def add_book_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Add to which language? (Hindi / English)")
    return ADD_LANG


async def add_book_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = update.message.text.strip().lower()
    if lang not in ["hindi", "english"]:
        await update.message.reply_text("❌ Invalid. Please type 'Hindi' or 'English'.")
        return ADD_LANG
    context.user_data["lang"] = lang
    await update.message.reply_text("Enter book title:")
    return ADD_TITLE


async def add_book_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Enter book price (₹ or $):")
    return ADD_PRICE


async def add_book_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["price"] = update.message.text
    await update.message.reply_text("Now send the book file (PDF, etc.) or type 'skip':")
    return ADD_FILE


async def add_book_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.document.file_id if update.message.document else None
    data = context.user_data
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO books VALUES (?,?,?,?,?)",
            (str(uuid4()), data["title"], data["lang"], data["price"], file_id),
        )
        await db.commit()
    await update.message.reply_text("✅ Book added successfully!")
    return ConversationHandler.END


async def skip_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await add_book_file(update, context)


# ================= CALLBACKS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "books":
        langs = [
            [InlineKeyboardButton("📗 Hindi", callback_data="lang_hindi"),
             InlineKeyboardButton("📘 English", callback_data="lang_english")]
        ]
        await q.message.reply_text("Choose language:", reply_markup=InlineKeyboardMarkup(langs))
        return

    if q.data.startswith("lang_"):
        lang = q.data.split("_")[1]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT id,title,price FROM books WHERE lang=?", (lang,))
            rows = await cur.fetchall()
        if not rows:
            await q.message.reply_text("❌ No books found.")
            return
        for bid, title, price in rows:
            kb = [[InlineKeyboardButton("💰 Buy", callback_data=f"buy_{bid}")]]
            await q.message.reply_text(
                f"📖 *{title}*\n💵 Price: {price}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        return

    if q.data.startswith("buy_"):
        bid = q.data.split("_")[1]
        upi = await get_config("upi_id")
        if not upi:
            await q.message.reply_text("❌ Admin has not set up payment yet.")
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT title,price FROM books WHERE id=?", (bid,))
            book = await cur.fetchone()

        order_id = str(uuid4())[:8]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?)",
                (order_id, q.from_user.id, q.from_user.first_name, bid, "pending", datetime.now().isoformat()),
            )
            await db.commit()

        await q.message.reply_text(
            f"🧾 *Order ID:* `{order_id}`\n💰 *Pay to:* `{upi}`\nAfter payment, wait for admin approval.",
            parse_mode="Markdown",
        )

        await context.bot.send_message(
            ADMIN_ID,
            f"💸 *New Order Received*\n👤 {q.from_user.first_name}\n📘 {book[0]}\n💵 {book[1]}\n🆔 {order_id}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}")]]
            ),
        )


async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split("_")[1]

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id,book_id FROM orders WHERE id=?", (order_id,))
        order = await cur.fetchone()
        if not order:
            await q.message.reply_text("❌ Order not found.")
            return

        user_id, book_id = order
        cur = await db.execute("SELECT title,file_id FROM books WHERE id=?", (book_id,))
        book = await cur.fetchone()
        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        await db.commit()

    await context.bot.send_message(user_id, f"✅ Payment approved!\nHere’s your file: *{book[0]}*", parse_mode="Markdown")
    if book[1]:
        await context.bot.send_document(user_id, document=book[1])
    await q.message.reply_text("✅ Approved and file sent!")


# ================= MAIN =================
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("addbook", add_book_start)],
        states={
            ADD_LANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_book_lang)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_book_title)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_book_price)],
            ADD_FILE: [
                MessageHandler(filters.Document.ALL, add_book_file),
                MessageHandler(filters.Regex("^(skip|Skip)$"), skip_file),
            ],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setupi", setup_upi))
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CallbackQueryHandler(approve_payment, pattern="^approve_"))

    logger.info("🚀 Bot running...")
    await app.run_polling()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
