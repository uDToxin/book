import logging
import aiosqlite
from datetime import datetime
from functools import wraps
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)
import nest_asyncio
nest_asyncio.apply()

# ===== CONFIG =====
BOT_TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"
ADMIN_ID = 6944519938  # Replace with your Telegram ID
DB_PATH = "bookstore.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== UTILITIES =====
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.effective_message.reply_text("❌ This command is only for admin.")
            return
        return await func(update, context)
    return wrapper

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS books(
            id TEXT PRIMARY KEY,
            title TEXT,
            lang TEXT,
            price TEXT,
            file_id TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            username TEXT,
            book_id TEXT,
            status TEXT,
            screenshot TEXT,
            created_at TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
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

async def get_book_by_title(title):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,title,price,file_id FROM books WHERE title=?", (title,))
        return await cur.fetchone()

async def get_all_books(lang=None):
    async with aiosqlite.connect(DB_PATH) as db:
        if lang:
            cur = await db.execute("SELECT title,price FROM books WHERE lang=?", (lang,))
        else:
            cur = await db.execute("SELECT title,price FROM books")
        return await cur.fetchall()

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Books", callback_data="books")],
        [InlineKeyboardButton("👤 My Info", callback_data="myinfo")]
    ]
    await update.message.reply_text(
        "📖 Welcome to Toxic BookStore! Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def myinfo(update, context):
    if hasattr(update, "callback_query") and update.callback_query:
        user = update.callback_query.from_user
    else:
        user = update.effective_user
    user_id = user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,))
        total = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='approved'", (user_id,))
        approved = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='pending'", (user_id,))
        pending = (await cur.fetchone())[0]
    text = f"👤 Your Info:\nTotal Orders: {total}\nApproved: {approved}\nPending: {pending}"
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)

@admin_only
async def setup_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /setupi your_upi@okbank")
        return
    upi = context.args[0]
    await set_config("upi_id", upi)
    await update.message.reply_text(f"✅ UPI set to: {upi}")

@admin_only
async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send book info in format:\nLanguage,Hindi/English,Title,Price\nAttach file (optional)")

async def add_book_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split(",")
        lang, title, price = parts[0].strip().lower(), parts[1].strip(), parts[2].strip()
        file_id = update.message.document.file_id if update.message.document else None
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO books(id,title,lang,price,file_id) VALUES(?,?,?,?,?)",
                (str(uuid4()), title, lang, price, file_id)
            )
            await db.commit()
        await update.message.reply_text("✅ Book added successfully")
    except Exception as e:
        await update.message.reply_text("❌ Error adding book. Make sure format is correct.")

# ===== CALLBACKS =====
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "books":
        keyboard = [
            [InlineKeyboardButton("📗 Hindi", callback_data="lang_hindi")],
            [InlineKeyboardButton("📘 English", callback_data="lang_english")]
        ]
        await q.message.reply_text("Select language:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif q.data.startswith("lang_"):
        lang = q.data.split("_")[1]
        books = await get_all_books(lang)
        text = "📚 *Books List:*\n\n"
        for b in books:
            text += f"{b[0]} - {b[1]}\n"
        text += "\nTo buy, use /buy <Book Name>"
        await q.message.reply_text(text, parse_mode="Markdown")
    elif q.data == "myinfo":
        await myinfo(update, context)

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /buy <Book Name>")
        return
    book_name = " ".join(context.args)
    book = await get_book_by_title(book_name)
    if not book:
        await update.message.reply_text("❌ Book not found.")
        return
    upi = await get_config("upi_id")
    if not upi:
        await update.message.reply_text("❌ Admin has not set up payment.")
        return
    order_id = str(uuid4())[:8]
    user = update.effective_user
    await update.message.reply_text(f"💰 Pay to: `{upi}`\nOrder ID: `{order_id}`\nAfter payment, send screenshot here.", parse_mode="Markdown")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,username,book_id,status,created_at,screenshot) VALUES(?,?,?,?,?,?,?)",
            (order_id, user.id, user.username, book[0], "pending", datetime.now().isoformat(), "")
        )
        await db.commit()
    await context.bot.send_message(ADMIN_ID, f"🛒 New order from {user.username}\nBook: {book_name}\nOrder ID: {order_id}")

# ===== SCREENSHOT HANDLER =====
async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("Attach screenshot of payment.")
        return
    # Find latest pending order for user
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,book_id FROM orders WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1", (user.id,))
        row = await cur.fetchone()
        if not row:
            await update.message.reply_text("No pending orders found.")
            return
        order_id, book_id = row
        photo_id = update.message.photo[-1].file_id
        await db.execute("UPDATE orders SET screenshot=? WHERE id=?", (photo_id, order_id))
        await db.commit()
        # Send to admin
        cur = await db.execute("SELECT title FROM books WHERE id=?", (book_id,))
        book = await cur.fetchone()
    keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}")]]
    await context.bot.send_photo(
        ADMIN_ID,
        photo=photo_id,
        caption=f"💳 Payment screenshot from @{user.username}\nBook: {book[0]}\nOrder ID: {order_id}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await update.message.reply_text("✅ Screenshot received. Waiting for admin approval.")

# ===== APPROVE HANDLER =====
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split("_")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id,book_id FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
        if not row:
            await q.message.reply_text("Order not found.")
            return
        user_id, book_id = row
        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        await db.commit()
        cur = await db.execute("SELECT title,file_id FROM books WHERE id=?", (book_id,))
        book = await cur.fetchone()
    await context.bot.send_message(user_id, f"✅ Payment approved! Here’s your book: *{book[0]}*\nAdmin ID: {ADMIN_ID}", parse_mode="Markdown")
    if book[1]:
        await context.bot.send_document(user_id, book[1])
    await q.message.reply_text("✅ Approved!")

# ===== MAIN =====
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myinfo", myinfo))
    app.add_handler(CommandHandler("setupi", setup_upi))
    app.add_handler(CommandHandler("addbook", addbook))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, add_book_manual))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(MessageHandler(filters.PHOTO, screenshot_handler))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(books|myinfo|lang_)"))
    app.add_handler(CallbackQueryHandler(approve_command, pattern="^approve_"))

    print("🚀 Bot running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Future()  # keep running

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
