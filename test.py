import logging
import aiosqlite
from datetime import datetime
from functools import wraps
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
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
        await db.commit()

async def get_book_by_title(title):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id,title,file_id FROM books WHERE title=?", (title,))
        return await cur.fetchone()

async def get_all_books(lang=None):
    async with aiosqlite.connect(DB_PATH) as db:
        if lang:
            cur = await db.execute("SELECT title FROM books WHERE lang=?", (lang,))
        else:
            cur = await db.execute("SELECT title FROM books")
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
    await update.message.reply_text(text)

@admin_only
async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send book info in format:\nLanguage,Hindi/English,Title\nAttach file (optional)")

async def add_book_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.split(",")
        lang, title = parts[0].strip().lower(), parts[1].strip()
        file_id = update.message.document.file_id if update.message.document else None
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO books(id,title,lang,file_id) VALUES(?,?,?,?)",
                (str(uuid4()), title, lang, file_id)
            )
            await db.commit()
        await update.message.reply_text("✅ Book added successfully")
    except Exception as e:
        await update.message.reply_text("❌ Error adding book. Make sure format is correct.")

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
            text += f"{b[0]}\n"
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
    user = update.effective_user
    order_id = str(uuid4())[:8]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,username,book_id,status,created_at,screenshot) VALUES(?,?,?,?,?,?,?)",
            (order_id, user.id, user.username, book[0], "pending", datetime.now().isoformat(), "")
        )
        await db.commit()
    keyboard = [[InlineKeyboardButton("💰 I Paid", callback_data=f"paid_{order_id}")]]
    await update.message.reply_text("Click when you have paid:", reply_markup=InlineKeyboardMarkup(keyboard))

# ===== PAYMENT & SCREENSHOT =====
async def paid_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split("_")[1]
    await q.message.reply_text("✅ Please send screenshot of your payment now.")

async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("Attach screenshot of payment.")
        return
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

# ===== APPROVE =====
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
    keyboard = [[InlineKeyboardButton("📖 Here is your book", url=f"tg://user?id={ADMIN_ID}")]]
    await context.bot.send_message(user_id, f"✅ Thanks for your purchase!\nBook: *{book[0]}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    if book[1]:
        await context.bot.send_document(user_id, book[1])
    await q.message.reply_text("✅ Approved!")

# ===== MAIN =====
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myinfo", myinfo))
    app.add_handler(CommandHandler("addbook", addbook))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, add_book_manual))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(MessageHandler(filters.PHOTO, screenshot_handler))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(books|myinfo|lang_)"))
    app.add_handler(CallbackQueryHandler(paid_button, pattern="^paid_"))
    app.add_handler(CallbackQueryHandler(approve_command, pattern="^approve_"))

    print("🚀 Bot running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Future()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
