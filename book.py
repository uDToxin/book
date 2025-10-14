# 📚 Telegram Book Store Bot v3 — by Dev + ChatGPT
# Full Featured Book Shop (Admin + User Flow + Payment Approval)
# Requirements:
# pip install python-telegram-bot==20.5 nest_asyncio

import os, sqlite3, asyncio, nest_asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

nest_asyncio.apply()

# =========================
# CONFIGURATION
# =========================
TOKEN = "8094733589:AAGYPT_O8oE0eBGPt-LvaHfpQQNE5xyB-lE"
ADMIN_ID = 6944519938
LOG_GROUP_ID = -1002760355837 # Group for logs
DB_FILE = "bookstore.db"

# =========================
# DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        price_inr TEXT,
        price_usd TEXT,
        cover_path TEXT,
        file_path TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(query, params)
    data = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return data

# =========================
# ADMIN SETTINGS
# =========================
def set_setting(key, value):
    db_execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))

def get_setting(key):
    data = db_execute("SELECT value FROM settings WHERE key=?", (key,), True)
    return data[0][0] if data else None

# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    buttons = [
        [InlineKeyboardButton("📚 Books", callback_data="view_books")],
        [InlineKeyboardButton("👤 User Commands", callback_data="user_cmds")],
    ]
    if user.id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("👮 Admin Commands", callback_data="admin_cmds")])
    buttons.append([InlineKeyboardButton("📩 Contact Admin", url=f"tg://user?id={ADMIN_ID}")])
    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "📚 *Welcome to Dev’s Book Store!*\nChoose an option below 👇",
        parse_mode="Markdown", reply_markup=markup
    )

# --- Admin command panels ---
async def admin_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.message.reply_text("❌ You are not admin.")
        return
    txt = (
        "👮 *Admin Commands:*\n\n"
        "/addbook <name> | <INR> | <$>\n"
        "/setbook <book_id> (then send cover)\n"
        "/uploadbook <book_id> (then send file)\n"
        "/setqr (upload QR)\n"
        "/setupi <upi_id>\n"
        "/books - list all books"
    )
    await q.message.reply_text(txt, parse_mode="Markdown")

async def user_cmds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    txt = (
        "👤 *User Commands:*\n\n"
        "/start - main menu\n"
        "/books - view all books\n"
        "Use buttons to buy books"
    )
    await q.message.reply_text(txt, parse_mode="Markdown")

# --- Add book ---
async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ Only admin can add books.")
    try:
        data = " ".join(context.args)
        name, inr, usd = [x.strip() for x in data.split("|")]
        db_execute("INSERT INTO books (name, price_inr, price_usd) VALUES (?,?,?)", (name, inr, usd))
        await update.message.reply_text(f"✅ Added: {name}\n💰 INR {inr} | ${usd}")
    except:
        await update.message.reply_text("⚠️ Format: /addbook Book Name | 299 | $4")

# --- Set cover ---
async def setbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /setbook <book_id>")
        return
    context.user_data["set_cover_id"] = int(context.args[0])
    await update.message.reply_text("📸 Send the cover image now...")

# --- Upload file ---
async def uploadbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /uploadbook <book_id>")
        return
    context.user_data["upload_file_id"] = int(context.args[0])
    await update.message.reply_text("📁 Send the book file (PDF/ZIP/EPUB etc)...")

# --- Set QR ---
async def setqr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["awaiting_qr"] = True
    await update.message.reply_text("📷 Send the payment QR image...")

# --- Set UPI ---
async def setupi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    upi = " ".join(context.args)
    set_setting("upi_id", upi)
    await update.message.reply_text(f"✅ UPI ID set: {upi}")

# --- Handle media from admin (QR, cover, file) ---
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    if "awaiting_qr" in context.user_data:
        file = await update.message.photo[-1].get_file()
        path = f"qr.png"
        await file.download_to_drive(path)
        set_setting("qr_path", path)
        del context.user_data["awaiting_qr"]
        await update.message.reply_text("✅ QR updated successfully!")
        return

    if "set_cover_id" in context.user_data:
        book_id = context.user_data.pop("set_cover_id")
        file = await update.message.photo[-1].get_file()
        path = f"cover_{book_id}.jpg"
        await file.download_to_drive(path)
        db_execute("UPDATE books SET cover_path=? WHERE id=?", (path, book_id))
        await update.message.reply_text("✅ Cover saved!")
        return

    if "upload_file_id" in context.user_data:
        book_id = context.user_data.pop("upload_file_id")
        file = await update.message.document.get_file()
        path = f"book_{book_id}_{update.message.document.file_name}"
        await file.download_to_drive(path)
        db_execute("UPDATE books SET file_path=? WHERE id=?", (path, book_id))
        await update.message.reply_text("✅ File uploaded successfully!")
        return

# --- View all books list ---
async def books_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = db_execute("SELECT id,name FROM books", fetch=True)
    if not books:
        await update.message.reply_text("📚 No books available.")
        return
    keyboard = [[InlineKeyboardButton(name, callback_data=f"book_{bid}")] for bid, name in books]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📖 *Select a Book:*", parse_mode="Markdown", reply_markup=markup)

# --- Handle inline clicks ---
async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "view_books":
        books = db_execute("SELECT id,name FROM books", fetch=True)
        if not books:
            await q.message.reply_text("📚 No books yet.")
            return
        keyboard = [[InlineKeyboardButton(name, callback_data=f"book_{bid}")] for bid, name in books]
        await q.message.reply_text("📖 *Select a Book:*", parse_mode="Markdown",
                                   reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_cmds":
        return await admin_cmds(update, context)

    if data == "user_cmds":
        return await user_cmds(update, context)

    if data.startswith("book_"):
        bid = int(data.split("_")[1])
        book = db_execute("SELECT name,price_inr,price_usd,cover_path FROM books WHERE id=?", (bid,), True)[0]
        name, inr, usd, cover = book
        caption = f"📖 *{name}*\n💵 INR {inr} | ${usd}"
        keyboard = [[InlineKeyboardButton("💰 Buy", callback_data=f"buy_{bid}")]]
        if cover and os.path.exists(cover):
            await q.message.reply_photo(InputFile(cover), caption=caption, parse_mode="Markdown",
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown",
                                       reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("buy_"):
        bid = int(data.split("_")[1])
        qr = get_setting("qr_path")
        upi = get_setting("upi_id") or "Not set"
        if qr and os.path.exists(qr):
            await q.message.reply_photo(
                InputFile(qr),
                caption=f"💰 *Payment Info:*\nUPI: `{upi}`\n\nSend payment screenshot here after paying.",
                parse_mode="Markdown"
            )
        else:
            await q.message.reply_text(f"💰 UPI: `{upi}`\n\nSend payment screenshot here after paying.",
                                       parse_mode="Markdown")
        context.user_data["await_ss_book"] = bid
        return

# --- Handle payment screenshot from user ---
async def handle_ss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if "await_ss_book" not in context.user_data:
        return
    bid = context.user_data.pop("await_ss_book")
    book = db_execute("SELECT name FROM books WHERE id=?", (bid,), True)[0][0]

    file = await update.message.photo[-1].get_file()
    ss_path = f"ss_{user.id}_{bid}.jpg"
    await file.download_to_drive(ss_path)

    # Send to admin for approval
    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user.id}_{bid}_{ss_path}")]]
    await update.get_bot().send_photo(
        ADMIN_ID, photo=InputFile(ss_path),
        caption=f"📩 *New Payment*\n👤 {user.full_name}\n📖 {book}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb)
    )
    await update.message.reply_text("✅ Payment screenshot sent! Waiting for approval.")

# --- Approve payment ---
async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("_")
    if q.from_user.id != ADMIN_ID:
        return
    uid, bid = int(data[1]), int(data[2])
    book = db_execute("SELECT name,file_path FROM books WHERE id=?", (bid,), True)[0]
    name, fpath = book
    await context.bot.send_message(uid, f"✅ Payment approved!\n📚 Here’s your book: *{name}*", parse_mode="Markdown")
    if fpath and os.path.exists(fpath):
        await context.bot.send_document(uid, InputFile(fpath))

    # Send logs
    await context.bot.send_message(LOG_GROUP_ID,
        f"🧾 *Order Log*\n👤 User ID: {uid}\n📖 Book: {name}\n✅ Approved by Admin",
        parse_mode="Markdown"
    )

    await q.message.reply_text("✅ Approved and sent!")

# =========================
# MAIN
# =========================
async def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addbook", addbook))
    app.add_handler(CommandHandler("setbook", setbook))
    app.add_handler(CommandHandler("uploadbook", uploadbook))
    app.add_handler(CommandHandler("setqr", setqr))
    app.add_handler(CommandHandler("setupi", setupi))
    app.add_handler(CommandHandler("books", books_list))

    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(CallbackQueryHandler(approve_payment, pattern="approve_"))

    app.add_handler(MessageHandler(filters.PHOTO & filters.User(ADMIN_ID), handle_media))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.User(ADMIN_ID), handle_ss))

    print("🤖 Book Store Bot v3 running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
