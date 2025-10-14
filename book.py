import logging import sqlite3 from datetime import datetime from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup from telegram.ext import ( ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler, ConversationHandler )

---------- CONFIGURATION ----------

BOT_TOKEN = "8094733589:AAGYPT_O8oE0eBGPt-LvaHfpQQNE5xyB-lE" ADMIN_ID = 6944519938 DB_PATH = "bookstore.db"

Conversation states

ADD_LANG, ADD_NAME, ADD_PRICE, ADD_COVER, ADD_FILE = range(5) SETUPI_WAIT, SETQR_WAIT = range(10, 12)

---------- LOGGING ----------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO) logger = logging.getLogger(name)

---------- DB HELPERS ----------

def init_db(): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute('''CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, lang TEXT NOT NULL, price REAL NOT NULL, file_id TEXT NOT NULL, cover_file_id TEXT)''') cur.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, registered_at TEXT, purchased_count INTEGER DEFAULT 0)''') cur.execute('''CREATE TABLE IF NOT EXISTS purchases (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, book_id INTEGER, status TEXT, created_at TEXT, proof_file_id TEXT)''') cur.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''') conn.commit() conn.close()

def db_add_book(name, lang, price, file_id, cover_file_id=None): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("INSERT INTO books (name, lang, price, file_id, cover_file_id) VALUES (?, ?, ?, ?, ?)", (name, lang, price, file_id, cover_file_id)) conn.commit() bid = cur.lastrowid conn.close() return bid

def db_get_books(lang=None): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() if lang: cur.execute("SELECT id, name, lang, price, cover_file_id FROM books WHERE lang = ?", (lang,)) else: cur.execute("SELECT id, name, lang, price, cover_file_id FROM books") rows = cur.fetchall() conn.close() return rows

def db_get_book_by_id(book_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, name, lang, price, file_id, cover_file_id FROM books WHERE id = ?", (book_id,)) row = cur.fetchone() conn.close() return row

def db_get_book_by_name(name): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, name, lang, price, file_id, cover_file_id FROM books WHERE name = ?", (name,)) row = cur.fetchone() conn.close() return row

def db_register_user(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id FROM users WHERE id = ?", (user_id,)) if not cur.fetchone(): cur.execute("INSERT INTO users (id, registered_at, purchased_count) VALUES (?, ?, 0)", (user_id, datetime.now().isoformat())) conn.commit() conn.close()

def db_inc_purchase_count(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE users SET purchased_count = purchased_count + 1 WHERE id = ?", (user_id,)) conn.commit() conn.close()

def db_get_user(user_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, registered_at, purchased_count FROM users WHERE id = ?", (user_id,)) row = cur.fetchone() conn.close() return row

def db_create_purchase(user_id, book_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("INSERT INTO purchases (user_id, book_id, status, created_at) VALUES (?, ?, ?, ?)" , (user_id, book_id, 'pending', datetime.now().isoformat())) conn.commit() pid = cur.lastrowid conn.close() return pid

def db_set_purchase_proof(purchase_id, file_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE purchases SET proof_file_id = ? WHERE id = ?", (file_id, purchase_id)) conn.commit() conn.close()

def db_set_purchase_status(purchase_id, status): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("UPDATE purchases SET status = ? WHERE id = ?", (status, purchase_id)) conn.commit() conn.close()

def db_get_purchase(purchase_id): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT id, user_id, book_id, status, created_at, proof_file_id FROM purchases WHERE id = ?", (purchase_id,)) row = cur.fetchone() conn.close() return row

def set_setting(key, value): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)) conn.commit() conn.close()

def get_setting(key): conn = sqlite3.connect(DB_PATH) cur = conn.cursor() cur.execute("SELECT value FROM settings WHERE key = ?", (key,)) row = cur.fetchone() conn.close() return row[0] if row else None

---------- DECORATORS ----------

def admin_only(func): @wraps(func) async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE): if update.effective_user.id != ADMIN_ID: await update.effective_message.reply_text("Sirf admin use kar sakta hai.") return return await func(update, context) return wrapper

---------- HANDLERS ----------

Here you would implement start_handler, callback_query_handler, buy_command, incoming_file_handler, setupi_command, setqr_command, add_command and its steps, cancel_handler, setbook_command

For brevity, include the logic as in your previous working code with correct syntax and imports.

---------- MAIN ----------

def main(): init_db() app = ApplicationBuilder().token(BOT_TOKEN).build()

# Command handlers
app.add_handler(CommandHandler("start", start_handler))
app.add_handler(CommandHandler("buy", buy_command))
app.add_handler(CallbackQueryHandler(callback_query_handler))

# Media handler for payment proof
app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, incoming_file_handler))

# Conversation handlers
app.add_handler(ConversationHandler(entry_points=[CommandHandler("setupi", setupi_command)], states={SETUPI_WAIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_setupi)]}, fallbacks=[CommandHandler("cancel", cancel_handler)]))
app.add_handler(ConversationHandler(entry_points=[CommandHandler("setqr", setqr_command)], states={SETQR_WAIT: [MessageHandler(filters.PHOTO, handle_setqr)]}, fallbacks=[CommandHandler("cancel", cancel_handler)]))
app.add_handler(ConversationHandler(entry_points=[CommandHandler("add", add_command)], states={ADD_LANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_lang_handler)], ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name_handler)], ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price_handler)], ADD_COVER: [MessageHandler((filters.PHOTO | (filters.TEXT & ~filters.COMMAND)), add_cover_handler)], ADD_FILE: [MessageHandler(filters.Document.ALL & ~filters.COMMAND, add_file_handler)]}, fallbacks=[CommandHandler("cancel", cancel_handler)]))

# setbook command
app.add_handler(CommandHandler("setbook", setbook_command))

print("Bot started...")
app.run_polling()

if name == "main": main()

