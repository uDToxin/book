import logging
import aiosqlite
from datetime import datetime
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
import nest_asyncio
nest_asyncio.apply()

BOT_TOKEN = "8094733589:AAGg3nkrh8yT6w5C7ySbV7C54bE5n6lyeCg"
ADMIN_ID = 6944519938  # Replace with your Telegram ID
DB_PATH = "bookstore.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== INIT DB =====
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS books(
            id TEXT PRIMARY KEY,
            lang TEXT,
            title TEXT,
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
        await db.commit()

# ===== COMMANDS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📚 Books", callback_data="books")],
        [InlineKeyboardButton("👤 My Info", callback_data="myinfo")]
    ]
    await update.message.reply_text(
        "📖 Welcome! Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ===== ADD BOOK (ADMIN) =====
async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Only admin can use this command.")
        return
    await update.message.reply_text(
        "Send book info step by step:\n1️⃣ Language (hindi/english)\n2️⃣ Title\n3️⃣ Price\n4️⃣ Attach file"
    )
    context.user_data['addbook'] = {}

async def addbook_steps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    data = context.user_data.get('addbook', {})
    if 'lang' not in data:
        data['lang'] = update.message.text.lower()
        context.user_data['addbook'] = data
        await update.message.reply_text("Enter Title of the Book:")
    elif 'title' not in data:
        data['title'] = update.message.text
        context.user_data['addbook'] = data
        await update.message.reply_text("Enter Price (USD or INR):")
    elif 'price' not in data:
        data['price'] = update.message.text
        context.user_data['addbook'] = data
        await update.message.reply_text("Now send the file (document):")
    elif 'file_id' not in data:
        if not update.message.document:
            await update.message.reply_text("Send the file as document.")
            return
        data['file_id'] = update.message.document.file_id
        context.user_data['addbook'] = data
        # Save to DB
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO books(id,lang,title,price,file_id) VALUES(?,?,?,?,?)",
                (str(uuid4()), data['lang'], data['title'], data['price'], data['file_id'])
            )
            await db.commit()
        context.user_data['addbook'] = {}
        await update.message.reply_text(f"✅ Book '{data['title']}' added successfully.")

# ===== BUTTON HANDLER =====
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
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT title,price FROM books WHERE lang=?", (lang,))
            books = await cur.fetchall()
        text = f"📚 *{lang.capitalize()} Books:*\n\n"
        for b in books:
            text += f"{b[0]} - {b[1]}\n"
        text += "\nTo buy, use /buy <Book Name>"
        await q.message.reply_text(text, parse_mode="Markdown")
    elif q.data == "myinfo":
        user_id = q.from_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=?", (user_id,))
            total = (await cur.fetchone())[0]
            cur = await db.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='approved'", (user_id,))
            approved = (await cur.fetchone())[0]
        text = f"👤 Your Info:\nTotal Orders: {total}\nApproved: {approved}"
        await q.message.reply_text(text)

# ===== BUY COMMAND =====
async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /buy <Book Name>")
        return
    book_name = " ".join(context.args)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM books WHERE title=?", (book_name,))
        book = await cur.fetchone()
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

# ===== PAID BUTTON =====
async def paid_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = q.data.split("_")[1]
    await q.message.reply_text("✅ Please send screenshot of your payment now.")

# ===== SCREENSHOT HANDLER =====
async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.photo:
        await update.message.reply_text("Attach screenshot of payment.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id,book_id FROM orders WHERE user_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (user.id,)
        )
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

# ===== APPROVE BUTTON =====
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
    await context.bot.send_message(
        user_id,
        f"✅ Thanks for your purchase!\nBook: *{book[0]}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    if book[1]:
        await context.bot.send_document(user_id, book[1])
    await q.message.reply_text("✅ Approved!")

# ===== MAIN =====
async def main():
    await init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addbook", addbook))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, addbook_steps))
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
