""" Telegram Bookstore Bot Features:

/start shows Inline buttons: Books, My Info

Books -> choose Hindi / English -> list books with Buy buttons

/buy <book name or id> command supported

/setupi and /setqr (admin only) to set UPI id and QR image

/add (admin only) to add a book: asks language, name, price, cover (optional), and book file (document)

Purchase flow: user clicks Buy or uses /buy; bot shows payment details (UPI + QR if set) and asks user to send payment screenshot. Screenshot is forwarded to admin DM with Approve button. On approve the bot sends the book file to user.

/setbook (admin) can set/update a book cover for existing book


Requirements:

python 3.10+

python-telegram-bot v20+


Run:

1. pip install python-telegram-bot==20.6


2. Edit ADMIN_ID and BOT_TOKEN in the code below.


3. python telegram_bookstore_bot.py



Note: This is a single-file example and uses sqlite3 for persistence.

"""

import logging import sqlite3 import os from datetime import datetime from functools import wraps

from telegram import ( Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ChatAction, ) from telegram.ext import ( ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, )

---------- CONFIGURATION ----------

BOT_TOKEN = "REPLACE_WITH_YOUR_BOT_TOKEN" ADMIN_ID = 123456789  # REPLACE_WITH_ADMIN_TELEGRAM_ID (int) DB_PATH = "bookstore.db"

Conversation states for /add, /setupi, /setqr

ADD_LANG, ADD_NAME, ADD_PRICE, ADD_COVER, ADD_FILE = range(5) SETUPI_WAIT = 10 SETQR_WAIT = 11

---------- LOGGING ----------

logging.basicConfig( format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, ) logger = logging.getLogger(name)

---------- DB HELPERS ----------

def init_db(): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute( """ CREATE TABLE IF NOT EXISTS books ( id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, lang TEXT NOT NULL, price REAL NOT NULL, file_id TEXT NOT NULL, cover_file_id TEXT ) """ )

cur.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        registered_at TEXT,
        purchased_count INTEGER DEFAULT 0
    )
    """
)

cur.execute(
    """
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        book_id INTEGER,
        status TEXT,
        created_at TEXT,
        proof_file_id TEXT
    )
    """
)

cur.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """
)

conn.commit()
conn.close()

def db_add_book(name, lang, price, file_id, cover_file_id=None): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute( "INSERT INTO books (name, lang, price, file_id, cover_file_id) VALUES (?, ?, ?, ?, ?)", (name, lang, price, file_id, cover_file_id), ) conn.commit() book_id = cur.lastrowid conn.close() return book_id

def db_get_books(lang=None): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() if lang: cur.execute("SELECT id, name, lang, price, cover_file_id FROM books WHERE lang = ?", (lang,)) else: cur.execute("SELECT id, name, lang, price, cover_file_id FROM books") rows = cur.fetchall() conn.close() return rows

def db_get_book_by_id(book_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, name, lang, price, file_id, cover_file_id FROM books WHERE id = ?", (book_id,)) row = cur.fetchone() conn.close() return row

def db_get_book_by_name(name): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, name, lang, price, file_id, cover_file_id FROM books WHERE name = ?", (name,)) row = cur.fetchone() conn.close() return row

def db_register_user(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id FROM users WHERE id = ?", (user_id,)) if not cur.fetchone(): cur.execute( "INSERT INTO users (id, registered_at, purchased_count) VALUES (?, ?, 0)", (user_id, datetime.now().isoformat()), ) conn.commit() conn.close()

def db_inc_purchase_count(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE users SET purchased_count = purchased_count + 1 WHERE id = ?", (user_id,)) conn.commit() conn.close()

def db_get_user(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, registered_at, purchased_count FROM users WHERE id = ?", (user_id,)) row = cur.fetchone() conn.close() return row

def db_create_purchase(user_id, book_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute( "INSERT INTO purchases (user_id, book_id, status, created_at) VALUES (?, ?, ?, ?)", (user_id, book_id, 'pending', datetime.now().isoformat()), ) conn.commit() pid = cur.lastrowid conn.close() return pid

def db_set_purchase_proof(purchase_id, file_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE purchases SET proof_file_id = ? WHERE id = ?", (file_id, purchase_id)) conn.commit() conn.close()

def db_set_purchase_status(purchase_id, status): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE purchases SET status = ? WHERE id = ?", (status, purchase_id)) conn.commit() conn.close()

def db_get_purchase(purchase_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, user_id, book_id, status, created_at, proof_file_id FROM purchases WHERE id = ?", (purchase_id,)) row = cur.fetchone() conn.close() return row

def set_setting(key, value): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)) conn.commit() conn.close()

def get_setting(key): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT value FROM settings WHERE key = ?", (key,)) row = cur.fetchone() conn.close() return row[0] if row else None

---------- UTIL DECORATORS ----------

def admin_only(func): @wraps(func) async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE): user_id = update.effective_user.id if user_id != ADMIN_ID: await update.effective_message.reply_text("Sirf admin use kar sakta hai.") return return await func(update, context)

return wrapper

---------- HANDLERS ----------

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): user = update.effective_user db_register_user(user.id) # Build profile button keyboard = [ [InlineKeyboardButton("📚 Books", callback_data="books")], [InlineKeyboardButton("👤 My Info", callback_data="myinfo")], ] text = "Welcome to BookStore Bot! Choose an option:" await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): q = update.callback_query await q.answer() data = q.data

if data == "books":
    kb = [
        [InlineKeyboardButton("Hindi 🇮🇳", callback_data="lang:HINDI")],
        [InlineKeyboardButton("English 🏴", callback_data="lang:ENGLISH")],
    ]
    await q.edit_message_text("Choose language:", reply_markup=InlineKeyboardMarkup(kb))
    return

if data == "myinfo":
    user = q.from_user
    info = db_get_user(user.id)
    if info:
        reg = info[1][:19].replace("T", " ")
        purchased = info[2]
    else:
        reg = "-"
        purchased = 0
    text = (
        "👤 Your profile\n━━━━━━━━━━━━━━━\n"
        f"🆔 ID: {user.id}\n\n"
        f"💰 Balance: 0.0$\n\n"
        f"🎁 Purchased products: {purchased} pcs\n\n"
        f"🕒 Registration: {reg}"
    )
    await q.edit_message_text(text)
    return

if data.startswith("lang:"):
    lang = data.split(":", 1)[1]
    rows = db_get_books(lang=lang)
    if not rows:
        await q.edit_message_text("Koi books nahi mile is language me.")
        return
    # Build keyboard with buy buttons
    blocks = []
    for r in rows:
        bid, name, lang, price, cover = r
        button = InlineKeyboardButton(f"Buy - {name} — ₹{price}", callback_data=f"buy:{bid}")
        blocks.append([button])
    await q.edit_message_text("Available books:", reply_markup=InlineKeyboardMarkup(blocks))
    return

if data.startswith("buy:"):
    bid = int(data.split(":", 1)[1])
    book = db_get_book_by_id(bid)
    if not book:
        await q.edit_message_text("Book nahi mila.")
        return
    book_id, name, lang, price, file_id, cover = book
    # create pending purchase
    pid = db_create_purchase(q.from_user.id, book_id)
    # show payment info and ask to send screenshot
    upi = get_setting("upi") or "UPI not set by admin yet"
    qr_file_id = get_setting("qr_file_id")
    text = f"You chose: {name}\nPrice: ₹{price}\n\nPay using UPI: {upi}\n\nAfter payment, please send screenshot of payment here."
    buttons = []
    if qr_file_id:
        buttons.append([InlineKeyboardButton("Show QR", callback_data=f"showqr:{pid}")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
    return

if data.startswith("showqr:"):
    pid = int(data.split(":", 1)[1])
    qr_file_id = get_setting("qr_file_id")
    if not qr_file_id:
        await q.edit_message_text("QR not set.")
        return
    # Show QR image and instruction
    await q.message.reply_photo(qr_file_id, caption="Scan QR to pay. After payment send screenshot here.")
    return

# Admin approve/reject callbacks
if data.startswith("approve:") or data.startswith("reject:"):
    if q.from_user.id != ADMIN_ID:
        await q.edit_message_text("Sirf admin kar sakta hai yeh.")
        return
    parts = data.split(":")
    action = parts[0]
    pid = int(parts[1])
    purchase = db_get_purchase(pid)
    if not purchase:
        await q.edit_message_text("Purchase record nahi mila.")
        return
    purchase_id, user_id, book_id, status, created_at, proof = purchase
    if action == "approve":
        # mark approved and send book to user
        db_set_purchase_status(pid, "approved")
        book = db_get_book_by_id(book_id)
        if not book:
            await q.edit_message_text("Book file missing.")
            return
        book_file_id = book[4]
        try:
            await context.bot.send_message(user_id, "Your payment has been approved. Sending your book now...")
            await context.bot.send_document(user_id, book_file_id, caption=f"Here is your book: {book[1]}")
            db_inc_purchase_count(user_id)
            await q.edit_message_text("Approved and book sent to user.")
        except Exception as e:
            logger.exception(e)
            await q.edit_message_text("Error sending book to user - maybe user blocked the bot.")
    else:
        db_set_purchase_status(pid, "rejected")
        try:
            await context.bot.send_message(user_id, "Your payment proof was rejected by admin. Please contact support.")
            await q.edit_message_text("Rejected.")
        except Exception:
            await q.edit_message_text("Rejected but couldn't notify user.")
    return

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE): # /buy <book name or id> if not context.args: await update.message.reply_text("Usage: /buy <book id or exact book name>") return query = " ".join(context.args).strip() # try id book = None if query.isdigit(): book = db_get_book_by_id(int(query)) if not book: book = db_get_book_by_name(query) if not book: await update.message.reply_text("Book not found. Use books menu to see available books.") return book_id, name, lang, price, file_id, cover = book pid = db_create_purchase(update.effective_user.id, book_id) upi = get_setting("upi") or "UPI not set by admin yet" qr_file_id = get_setting("qr_file_id") text = f"You chose: {name}\nPrice: ₹{price}\n\nPay using UPI: {upi}\n\nAfter payment, please send screenshot of payment here." if qr_file_id: await update.message.reply_photo(qr_file_id, caption=text) else: await update.message.reply_text(text)

Handle images/documents from users (receive payment screenshot)

async def incoming_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): # If user sends a photo or document and they have a pending purchase in 'pending' status, treat it as proof user_id = update.effective_user.id conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute( "SELECT id FROM purchases WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1", (user_id,), ) row = cur.fetchone() conn.close() if not row: await update.message.reply_text("Koi pending purchase nahi mila. Agar aapne pay kiya hai to /buy se try karein.") return pid = row[0] # get file_id of the incoming media (photo or document) file_id = None if update.message.photo: file_id = update.message.photo[-1].file_id elif update.message.document: file_id = update.message.document.file_id else: await update.message.reply_text("Please send a photo or document as proof.") return db_set_purchase_proof(pid, file_id) # forward proof to admin with approve/reject buttons and link to purchase kb = [ [ InlineKeyboardButton("Approve", callback_data=f"approve:{pid}"), InlineKeyboardButton("Reject", callback_data=f"reject:{pid}"), ] ] await context.bot.send_message(ADMIN_ID, f"New payment proof for purchase #{pid} from user {user_id}") await context.bot.send_photo(ADMIN_ID, file_id, caption=f"Purchase #{pid}\nUser: {user_id}", reply_markup=InlineKeyboardMarkup(kb)) await update.message.reply_text("Proof received. Admin will verify and you'll receive the book after approval.")

Admin commands: /setupi, /setqr, /add

@admin_only async def setupi_command(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Send the UPI ID now (like name@bank) — it will be saved.") return SETUPI_WAIT

async def handle_setupi(update: Update, context: ContextTypes.DEFAULT_TYPE): upi = update.message.text.strip() set_setting("upi", upi) await update.message.reply_text(f"UPI saved: {upi}") return ConversationHandler.END

@admin_only async def setqr_command(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Send the QR image now as a photo.") return SETQR_WAIT

async def handle_setqr(update: Update, context: ContextTypes.DEFAULT_TYPE): if not update.message.photo: await update.message.reply_text("Please send a photo.") return SETQR_WAIT file_id = update.message.photo[-1].file_id set_setting("qr_file_id", file_id) await update.message.reply_text("QR image saved.") return ConversationHandler.END

Add book flow

@admin_only async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Adding a book. Which language? (send HINDI or ENGLISH)") return ADD_LANG

async def add_lang_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): lang = update.message.text.strip().upper() if lang not in ("HINDI", "ENGLISH"): await update.message.reply_text("Please send HINDI or ENGLISH") return ADD_LANG context.user_data["add_lang"] = lang await update.message.reply_text("Send book name now:") return ADD_NAME

async def add_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): context.user_data["add_name"] = update.message.text.strip() await update.message.reply_text("Send book price (numbers only):") return ADD_PRICE

async def add_price_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): txt = update.message.text.strip() try: price = float(txt) except ValueError: await update.message.reply_text("Price invalid. Send numeric value.") return ADD_PRICE context.user_data["add_price"] = price await update.message.reply_text("Send cover image (photo) or type SKIP to skip:") return ADD_COVER

async def add_cover_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): if update.message.text and update.message.text.strip().upper() == "SKIP": context.user_data["add_cover"] = None elif update.message.photo: context.user_data["add_cover"] = update.message.photo[-1].file_id else: await update.message.reply_text("Send a photo or SKIP") return ADD_COVER await update.message.reply_text("Now send the book file as a document (PDF/EPUB):") return ADD_FILE

async def add_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): if not update.message.document: await update.message.reply_text("Please send a document file (not photo).") return ADD_FILE file_id = update.message.document.file_id name = context.user_data.get("add_name") lang = context.user_data.get("add_lang") price = context.user_data.get("add_price") cover = context.user_data.get("add_cover") bid = db_add_book(name, lang, price, file_id, cover) await update.message.reply_text(f"Book added with id {bid} — {name}") context.user_data.clear() return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Cancelled.") context.user_data.clear() return ConversationHandler.END

setbook to update cover for existing book

@admin_only async def setbook_command(update: Update, context: ContextTypes.DEFAULT_TYPE): await update.message.reply_text("Usage: /setbook <book id> — then send cover image as photo.")

async def setbook_flow(update: Update, context: ContextTypes.DEFAULT_TYPE): # Expect: reply to the prompt with photo, but first get args if not context.args: await update.message.reply_text("Please pass book id: /setbook <book id>") return if not update.message.reply_to_message: await update.message.reply_text("After issuing /setbook <id>, reply to this message with the cover photo.") return

---------- MAIN ----------

def main(): init_db() app = ApplicationBuilder().token(BOT_TOKEN).build()

# Basic handlers
app.add_handler(CommandHandler("start", start_handler))
app.add_handler(CommandHandler("buy", buy_command))

# Callback queries
app.add_handler(CallbackQueryHandler(callback_query_handler))

# Incoming images/documents as payment proof
media_filter = filters.PHOTO | filters.Document.ALL
app.add_handler(MessageHandler(media_filter, incoming_file_handler))

# /setupi conversation
setupi_conv = ConversationHandler(
    entry_points=[CommandHandler("setupi", setupi_command)],
    states={SETUPI_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_setupi)]},
    fallbacks=[CommandHandler("cancel", cancel_handler)],
)
app.add_handler(setupi_conv)

# /setqr conversation
setqr_conv = ConversationHandler(
    entry_points=[CommandHandler("setqr", setqr_command)],
    states={SETQR_WAIT: [MessageHandler(filters.PHOTO, handle_setqr)]},
    fallbacks=[CommandHandler("cancel", cancel_handler)],
)
app.add_handler(setqr_conv)

# /add conversation for admin
add_conv = ConversationHandler(
    entry_points=[CommandHandler("add", add_command)],
    states={
        ADD_LANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_lang_handler)],
        ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name_handler)],
        ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price_handler)],
        ADD_COVER: [MessageHandler((filters.PHOTO | (filters.TEXT & ~filters.COMMAND)), add_cover_handler)],
        ADD_FILE: [MessageHandler(filters.Document.ALL & ~filters.COMMAND, add_file_handler)],
    },
    fallbacks=[CommandHandler("cancel", cancel_handler)],
)
app.add_handler(add_conv)

# setbook simple command placeholder
app.add_handler(CommandHandler("setbook", setbook_command))

print("Bot started...")
app.run_polling()

if name == "main": main()

