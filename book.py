#!/usr/bin/env python3
# book_store_bot_v2.py
# Telegram Book Store Bot v2
# Features: admin add book+file, set UPI, set QR, buy->screenshot->admin approve->send file, logs

import sqlite3
import datetime
import nest_asyncio
import asyncio

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

nest_asyncio.apply()

# ==========================
# CONFIG - EDIT THESE
# ==========================
TOKEN = "8094733589:AAGYPT_O8oE0eBGPt-LvaHfpQQNE5xyB-lE"
ADMIN_ID = 6944519938               # replace with your Telegram ID (int)
LOG_GROUP_ID = -1002760355837      # replace with your logs group ID (int, negative for supergroups)
DB_FILE = "books.db"

# ==========================
# TEMP STATES (in-memory)
# ==========================
admin_waiting_for_bookfile = {}   # admin_id -> (name, price_inr, price_usd)
admin_waiting_for_qr = set()      # admin ids waiting to upload QR
waiting_for_payment_ss = {}       # user_id -> order_id (while waiting for screenshot)
# For creating orders: we will create order after user sends screenshot, so waiting_for_payment_ss not needed beyond mapping

# ==========================
# DATABASE
# ==========================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price_inr TEXT,
            price_usd TEXT,
            file_id TEXT,
            file_kind TEXT  -- e.g. document, photo, video, audio
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            book_id INTEGER,
            price TEXT,
            currency TEXT,
            status TEXT, -- pending / approved / rejected
            created_at TEXT,
            screenshot_file_id TEXT,
            screenshot_kind TEXT
        )
    ''')
    # Key-value store for settings
    cur.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_setting(key, value):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def get_setting(key):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def add_book_db(name, inr, usd, file_id=None, file_kind=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO books (name, price_inr, price_usd, file_id, file_kind) VALUES (?, ?, ?, ?, ?)",
                (name, inr, usd, file_id, file_kind))
    conn.commit()
    book_id = cur.lastrowid
    conn.close()
    return book_id

def update_book_file(book_id, file_id, file_kind):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE books SET file_id=?, file_kind=? WHERE id=?", (file_id, file_kind, book_id))
    conn.commit()
    conn.close()

def list_books_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, name, price_inr, price_usd FROM books")
    rows = cur.fetchall()
    conn.close()
    return rows

def get_book(book_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT id, name, price_inr, price_usd, file_id, file_kind FROM books WHERE id=?", (book_id,))
    row = cur.fetchone()
    conn.close()
    return row

def create_order(user_id, username, full_name, book_id, price, currency, screenshot_file_id=None, screenshot_kind=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    cur.execute('''INSERT INTO orders
        (user_id, username, full_name, book_id, price, currency, status, created_at, screenshot_file_id, screenshot_kind)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)''',
        (user_id, username, full_name, book_id, price, currency, now, screenshot_file_id, screenshot_kind))
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid

def update_order_status(order_id, status):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute('''SELECT id, user_id, username, full_name, book_id, price, currency, status, created_at,
                   screenshot_file_id, screenshot_kind FROM orders WHERE id=?''', (order_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ==========================
# HELPERS
# ==========================
def file_info_from_message(msg):
    """
    Returns tuple (file_id, kind) depending what kind of file the message contains.
    kind: 'document','photo','video','audio','voice','other'
    """
    if msg.document:
        return msg.document.file_id, "document"
    if msg.photo:
        # photo is a list of sizes; take highest
        return msg.photo[-1].file_id, "photo"
    if msg.video:
        return msg.video.file_id, "video"
    if msg.audio:
        return msg.audio.file_id, "audio"
    if msg.voice:
        return msg.voice.file_id, "voice"
    return None, None

# ==========================
# HANDLERS
# ==========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = f"📚 *Welcome to Dev's Book Store!*\n\nHi {user.first_name} — choose a book below or contact admin."
    # Build inline keyboard with all books (each as a button) + contact admin
    buttons = []
    books = list_books_db()
    for bid, name, inr, usd in books:
        # label small: name — price
        label = f"{name} — ₹{inr}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"buy_{bid}")])
    # contact admin
    buttons.append([InlineKeyboardButton("📩 Contact Admin", url=f"tg://user?id={ADMIN_ID}")])
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

# Admin: set UPI
async def setupi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    data = " ".join(context.args)
    if not data:
        await update.message.reply_text("Usage: /setupi your_upi_id (e.g. dev@ybl)")
        return
    save_setting("upi_id", data.strip())
    await update.message.reply_text(f"✅ UPI set to: `{data.strip()}`", parse_mode="Markdown")

# Admin: set QR - start flow
async def setqr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return
    admin_waiting_for_qr.add(update.effective_user.id)
    await update.message.reply_text("Please send the QR image now (as photo or image file).")

# Admin: addbook command - start flow (admin must then upload file)
# Format: /addbook Name | INR_price | USD_price
async def addbook_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to add books.")
        return
    data = " ".join(context.args)
    try:
        name, inr, usd = [x.strip() for x in data.split("|")]
    except Exception:
        await update.message.reply_text("Usage: /addbook Book Name | INR_price | USD_price\nExample: /addbook Python Basics | 199 | 2.5")
        return
    # create a db entry without file yet; store book id and ask admin to upload file
    book_id = add_book_db(name, inr, usd, file_id=None, file_kind=None)
    admin_waiting_for_bookfile[update.effective_user.id] = book_id
    await update.message.reply_text(f"Book record created (ID {book_id}). Now please upload the book file (PDF, epub, docx, audio, video, etc).")

# Show books: textual listing with inline buy buttons (same as /start)
async def books_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = list_books_db()
    if not books:
        await update.message.reply_text("No books available yet.")
        return
    for bid, name, inr, usd in books:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Buy", callback_data=f"buy_{bid}"),
                                    InlineKeyboardButton("📩 Contact Admin", url=f"tg://user?id={ADMIN_ID}")]])
        await update.message.reply_text(f"📖 *{name}*\n💵 INR: ₹{inr}\n💵 USD: {usd}", parse_mode="Markdown", reply_markup=kb)

# Callback: buy button pressed
async def callback_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("buy_"):
        return
    book_id = int(data.split("_", 1)[1])
    book = get_book(book_id)
    if not book:
        await query.message.reply_text("❌ Book not found.")
        return
    _id, name, inr, usd, file_id, file_kind = book
    # Show UPI and QR if set, then ask to send screenshot
    upi = get_setting("upi_id")
    text = f"📖 *{name}*\nPrice: ₹{inr} (or {usd} USD)\n\nPlease pay using the UPI below and then send the *payment screenshot* here so admin can verify.\n"
    if upi:
        text += f"\nUPI: `{upi}`\n"
    else:
        text += "\nUPI not set by admin yet.\n"
    # Build reply markup: Send buttons to open QR (if present) or contact admin
    buttons = []
    qr_file_id = get_setting("qr_file_id")
    if qr_file_id:
        buttons.append([InlineKeyboardButton("📷 View QR", callback_data=f"viewqr")])
    buttons.append([InlineKeyboardButton("📩 Contact Admin", url=f"tg://user?id={ADMIN_ID}")])
    buttons.append([InlineKeyboardButton("✅ I have paid (send screenshot below)", callback_data=f"confirm_pay_{book_id}")])
    kb = InlineKeyboardMarkup(buttons)
    await query.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

# Callback: view qr or confirm pay
async def callback_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "viewqr":
        qr_file_id = get_setting("qr_file_id")
        if not qr_file_id:
            await query.message.reply_text("QR not set by admin.")
            return
        # send QR image (it's stored as photo file_id)
        try:
            await context.bot.send_photo(chat_id=query.from_user.id, photo=qr_file_id, caption="Scan this QR to pay.")
        except Exception:
            # fallback: try send document
            await context.bot.send_message(chat_id=query.from_user.id, text="Unable to show QR image.")
        return

    if data.startswith("confirm_pay_"):
        book_id = int(data.split("_", 2)[2])
        # Ask user to send screenshot image as a message (we will accept images or documents)
        await query.message.reply_text("📸 Please send the payment screenshot here (as photo or image file).")
        # Save state: map user to a pending book id in waiting_for_payment_ss as temporary marker
        waiting_for_payment_ss[query.from_user.id] = book_id
        return

    # Approve button format: approve_{order_id}
    if data.startswith("approve_"):
        order_id = int(data.split("_", 1)[1])
        order = get_order(order_id)
        if not order:
            await query.message.reply_text("Order not found.")
            return
        # only admin can approve (we'll still check)
        if query.from_user.id != ADMIN_ID:
            await query.message.reply_text("❌ Only admin can approve.")
            return

        # Approve: send file to user
        _oid, user_id, username, full_name, book_id, price, currency, status, created_at, screenshot_file_id, screenshot_kind = order
        book = get_book(book_id)
        if not book:
            await query.message.reply_text("Book not found.")
            return
        _, book_name, _, _, book_file_id, book_file_kind = book

        # send the file to user depending on kind
        try:
            if book_file_kind == "document":
                await context.bot.send_document(chat_id=user_id, document=book_file_id, caption=f"✅ Your purchase: {book_name}")
            elif book_file_kind == "photo":
                await context.bot.send_photo(chat_id=user_id, photo=book_file_id, caption=f"✅ Your purchase: {book_name}")
            elif book_file_kind == "video":
                await context.bot.send_video(chat_id=user_id, video=book_file_id, caption=f"✅ Your purchase: {book_name}")
            elif book_file_kind == "audio":
                await context.bot.send_audio(chat_id=user_id, audio=book_file_id, caption=f"✅ Your purchase: {book_name}")
            else:
                # fallback to document
                await context.bot.send_document(chat_id=user_id, document=book_file_id, caption=f"✅ Your purchase: {book_name}")
        except Exception as e:
            await query.message.reply_text(f"Failed to send file to user: {e}")

        # update order status
        update_order_status(order_id, "approved")

        # notify admin and logs
        await query.message.reply_text(f"Order #{order_id} approved and book sent to user.")
        # Log to group
        log_msg = (f"📦 *Order Approved*\nOrder ID: `{order_id}`\nUser: {full_name} (@{username})\n"
                   f"Book: {book_name}\nPrice: {price} {currency}\nTime: {created_at}")
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_msg, parse_mode="Markdown")
        return

# Message handler for uploads (admin uploading book file or QR, user uploading screenshot)
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id

    # 1) Is admin sending QR? (admin used /setqr before)
    if uid in admin_waiting_for_qr:
        file_id, kind = file_info_from_message(msg)
        if not file_id:
            await update.message.reply_text("Please send an image/photo for QR.")
            return
        # store QR file id in settings (we store as value)
        save_setting("qr_file_id", file_id)
        admin_waiting_for_qr.remove(uid)
        await update.message.reply_text("✅ QR saved.")
        return

    # 2) Admin uploading book file after /addbook
    if uid in admin_waiting_for_bookfile:
        book_id = admin_waiting_for_bookfile.pop(uid)
        file_id, kind = file_info_from_message(msg)
        if not file_id:
            await update.message.reply_text("Please upload a file (document/photo/video/audio).")
            return
        update_book_file(book_id, file_id, kind)
        await update.message.reply_text(f"✅ File saved for book ID {book_id}. Book is now ready.")
        # Optionally notify logs
        b = get_book(book_id)
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=f"➕ New book added: {b[1]} (ID {book_id})")
        return

    # 3) User sending payment screenshot (expected)
    if uid in waiting_for_payment_ss:
        # confirm it's a photo or document (screenshot)
        file_id, kind = file_info_from_message(msg)
        if not file_id:
            await update.message.reply_text("Please send an image or screenshot file.")
            return
        book_id = waiting_for_payment_ss.pop(uid)
        # create order entry
        user = update.effective_user
        # We'll record price as the INR price by default; admin can check USD too
        book = get_book(book_id)
        price = book[2] if book else "N/A"
        order_id = create_order(user.id, user.username or "", user.full_name or "", book_id, price, "INR",
                                screenshot_file_id=file_id, screenshot_kind=kind)
        # Forward screenshot to admin with approve button
        caption = (f"📸 *Payment Screenshot*\nOrder ID: `{order_id}`\nUser: {user.full_name} (@{user.username})\n"
                   f"Book: {book[1] if book else book_id}\nPrice: ₹{price}\n\nApprove if payment is valid.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{order_id}")]])
        try:
            # send different based on kind
            if kind == "photo":
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            elif kind == "document":
                await context.bot.send_document(chat_id=ADMIN_ID, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            elif kind == "video":
                await context.bot.send_video(chat_id=ADMIN_ID, video=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
            else:
                # fallback to document
                await context.bot.send_document(chat_id=ADMIN_ID, document=file_id, caption=caption, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            await update.message.reply_text(f"Failed to forward to admin: {e}")
            return

        await update.message.reply_text("✅ Screenshot received. Admin will review and approve soon. Thank you!")
        # Log to group
        await context.bot.send_message(chat_id=LOG_GROUP_ID,
                                       text=(f"🧾 New order (pending) — Order ID `{order_id}`\nUser: {user.full_name} (@{user.username})\nBook: {book[1] if book else book_id}\nTime: {datetime.datetime.utcnow().isoformat()}"),
                                       parse_mode="Markdown")
        return

    # If none of the above, respond help or ignore
    # (e.g., plain text messages)
    await update.message.reply_text("I didn't understand. Use /books to see books or contact admin.")

# Fallback: unknown commands
async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Unknown command. Use /books or /start.")

# ==========================
# MAIN
# ==========================
async def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("setupi", setupi_cmd))
    app.add_handler(CommandHandler("setqr", setqr_cmd))
    app.add_handler(CommandHandler("addbook", addbook_cmd))
    app.add_handler(CommandHandler("books", books_cmd))

    # callback queries
    app.add_handler(CallbackQueryHandler(callback_buy, pattern=r"^buy_"))
    app.add_handler(CallbackQueryHandler(callback_general, pattern=r"^(viewqr|confirm_pay_|approve_)"))

    # message handler (files, screenshots, admin uploads, general)
    # accept photos, documents, video, audio, voice, text (for fallback)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))

    # unknown commands
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    print("🤖 Book Store Bot v2 running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
