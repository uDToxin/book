import logging
import asyncio
import aiosqlite
from datetime import datetime
from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)
import uuid
import os

# ================= Configuration =================
BOT_TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"
ADMIN_ID = 6944519938  # Replace with your Telegram ID
DB_PATH = "bookstore.db"
BOOK_FILES_DIR = "./books/"
# =================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ================= Database Initialization =================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS books(
            id TEXT PRIMARY KEY,
            title TEXT,
            lang TEXT,
            price_inr TEXT,
            price_usd TEXT,
            file_path TEXT
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            username TEXT,
            book_id TEXT,
            status TEXT,
            screenshot TEXT,
            created_at TEXT
        )""")
        await db.commit()
# ===========================================================

# =================== Helper Functions =====================
async def get_all_books(lang):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, title, price_inr, price_usd FROM books WHERE lang=?", (lang,)
        )
        res = await cur.fetchall()
    return res

async def get_book(book_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT title, file_path FROM books WHERE id=?", (book_id,)
        )
        res = await cur.fetchone()
    return res

async def add_order(user_id, username, book_id):
    order_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO orders(id,user_id,username,book_id,status,created_at,screenshot) VALUES(?,?,?,?,?,?,?)",
            (order_id, user_id, username or "", book_id, "pending", datetime.now().isoformat(), "")
        )
        await db.commit()
    return order_id
# ===========================================================

# =================== Bot Commands =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Books", callback_data="books")],
        [InlineKeyboardButton("My Info", callback_data="myinfo")]
    ]
    await update.message.reply_text(
        "Welcome to BookStore Bot!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Books callback
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "books":
        keyboard = [
            [InlineKeyboardButton("Hindi", callback_data="lang_Hindi")],
            [InlineKeyboardButton("English", callback_data="lang_English")],
        ]
        await query.message.reply_text(
            "Choose Book Language:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif query.data.startswith("lang_"):
        lang = query.data.split("_")[1]
        books = await get_all_books(lang)
        text = ""
        for b in books:
            text += f"{b[1]} - ₹{b[2]} / ${b[3]}\n"
        text += "\nUse /buy <book title> to purchase."
        await query.message.reply_text(text)
    elif query.data == "myinfo":
        user_id = query.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM orders WHERE user_id=? AND status='approved'", (user_id,)
            )
            res = await cur.fetchone()
        await query.message.reply_text(f"You have bought {res[0]} book(s) till now.")

# /addbook command
async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /addbook")
        return ConversationHandler.END
    await update.message.reply_text("Send language (Hindi/English):")
    return 1

async def addbook_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lang"] = update.message.text
    await update.message.reply_text("Send book title:")
    return 2

async def addbook_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Send price in INR:")
    return 3

async def addbook_price_inr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["price_inr"] = update.message.text
    await update.message.reply_text("Send price in USD:")
    return 4

async def addbook_price_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["price_usd"] = update.message.text
    await update.message.reply_text("Send the book file now:")
    return 5

async def addbook_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()
    book_id = str(uuid.uuid4())
    if not os.path.exists(BOOK_FILES_DIR):
        os.makedirs(BOOK_FILES_DIR)
    file_path = os.path.join(BOOK_FILES_DIR, f"{book_id}.pdf")
    await file.download_to_drive(file_path)
    context.user_data["file_path"] = file_path
    # Save to DB
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO books(id,title,lang,price_inr,price_usd,file_path) VALUES(?,?,?,?,?,?)",
            (
                book_id,
                context.user_data["title"],
                context.user_data["lang"],
                context.user_data["price_inr"],
                context.user_data["price_usd"],
                file_path
            )
        )
        await db.commit()
    await update.message.reply_text("Book added successfully!")
    return ConversationHandler.END

# /buy command
async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /buy <book title>")
        return
    book_title = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM books WHERE title=?", (book_title,))
        book = await cur.fetchone()
    if not book:
        await update.message.reply_text("Book not found!")
        return
    order_id = await add_order(update.message.from_user.id, update.message.from_user.username, book[0])
    keyboard = [[InlineKeyboardButton("I Paid", callback_data=f"paid_{order_id}")]]
    await update.message.reply_text(
        f"Please take a screenshot after payment and click 'I Paid'.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# Paid button callback
async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = query.data.split("_")[1]
    context.user_data["current_order"] = order_id
    await query.message.reply_text("Send screenshot of payment now:")

# Screenshot handler
async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "current_order" not in context.user_data:
        return
    order_id = context.user_data["current_order"]
    screenshot_file = await update.message.photo[-1].get_file()
    screenshot_path = f"screenshots/{order_id}.jpg"
    if not os.path.exists("screenshots"):
        os.makedirs("screenshots")
    await screenshot_file.download_to_drive(screenshot_path)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET screenshot=? WHERE id=?", (screenshot_path, order_id))
        await db.commit()
    # Send to admin
    keyboard = [[InlineKeyboardButton("Approve", callback_data=f"approve_{order_id}")]]
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=InputFile(screenshot_path),
        caption=f"New order from {update.message.from_user.username} ({update.message.from_user.id})",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await update.message.reply_text("Screenshot sent to admin for approval.")

# Approve callback
async def approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = query.data.split("_")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
        await db.commit()
        cur = await db.execute("SELECT user_id, book_id FROM orders WHERE id=?", (order_id,))
        row = await cur.fetchone()
    user_id, book_id = row
    book = await get_book(book_id)
    # Send message to user
    keyboard = [[InlineKeyboardButton("Here is your book", url=f"tg://user?id={ADMIN_ID}")]]
    await context.bot.send_message(
        chat_id=user_id,
        text=f"Thanks for buying {book[0]}!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await query.message.reply_text("Order approved and user notified.")

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END
# ===========================================================

# =================== Main ================================
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(books|myinfo|lang_)"))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CallbackQueryHandler(paid_callback, pattern="^paid_"))
    app.add_handler(CallbackQueryHandler(approve_callback, pattern="^approve_"))
    app.add_handler(MessageHandler(filters.PHOTO, screenshot_handler))

    # Addbook conversation
    addbook_conv = ConversationHandler(
        entry_points=[CommandHandler("addbook", addbook)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_lang)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_title)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_inr)],
            4: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbook_price_usd)],
            5: [MessageHandler(filters.Document.ALL & ~filters.COMMAND, addbook_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(addbook_conv)

    await app.initialize()
    await app.start()
    logging.info("🚀 Bot running without asyncio errors...")
    await app.updater.start_polling()
    await app.updater.idle()
# ===========================================================

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
