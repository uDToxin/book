import logging, json, os, html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
ADMIN_ID = 6944519938  # Replace with your numeric Telegram ID
TOKEN = "8094733589:AAGYPT_O8oE0eBGPt-LvaHfpQQNE5xyB-lE"
DATA_FILE = "books_data.json"

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- DATA ----------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        DATA = json.load(f)
else:
    DATA = {"books": {}, "upi": "", "qr": "", "pending": {}, "upload_step": {}}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(DATA, f, indent=2)

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📚 Books", callback_data="show_books")]]
    await update.message.reply_text(
        "👋 Welcome to *Book Store Bot!*\nBrowse and buy books! 📖",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def addbook(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Not authorized")
        return
    if len(context.args)<3:
        await update.message.reply_text("Usage: /addbook <Name> <Price> <Cover_URL or none>", parse_mode="Markdown")
        return
    name = context.args[0]
    price = context.args[1]
    cover = " ".join(context.args[2:])
    if cover.lower()=="none":
        cover=None
    DATA["books"][name]={"price":price,"cover":cover,"file":None}
    DATA["upload_step"][str(update.effective_user.id)] = name
    save_data()
    await update.message.reply_text(f"✅ Book *{name}* added! Now send the book file (PDF/EPUB/MOBI) for this book.", parse_mode="Markdown")

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in DATA["upload_step"]:
        return
    book_name = DATA["upload_step"][user_id]
    if update.message.document:
        DATA["books"][book_name]["file"] = update.message.document.file_id
        DATA["upload_step"].pop(user_id)
        save_data()
        await update.message.reply_text(f"✅ File for *{book_name}* uploaded successfully!", parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ Send a valid file.")

async def setupi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Not authorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setupi <UPI_ID>", parse_mode="Markdown")
        return
    DATA["upi"]=context.args[0]
    save_data()
    await update.message.reply_text(f"✅ UPI set to `{DATA['upi']}`", parse_mode="Markdown")

async def setqr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Not authorized")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setqr <QR_URL>", parse_mode="Markdown")
        return
    DATA["qr"]=" ".join(context.args)
    save_data()
    await update.message.reply_text("✅ QR set successfully")

async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not DATA["books"]:
        await update.message.reply_text("❌ No books yet")
        return
    text="*📚 Book List:*\n"
    for name,info in DATA["books"].items():
        text+=f"• *{html.escape(name)}* — ₹{info['price']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def show_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    books=DATA["books"]
    if not books:
        await query.edit_message_text("❌ No books yet")
        return
    buttons=[[InlineKeyboardButton(name, callback_data=f"book_{name}")] for name in books.keys()]
    await query.edit_message_text("📚 *Available Books:*",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(buttons))

async def book_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    book_name=query.data.split("_",1)[1]
    book=DATA["books"].get(book_name)
    if not book:
        await query.message.reply_text("❌ Book not found!")
        return
    caption=f"*{html.escape(book_name)}*\n💰 Price: ₹{book['price']}\n\nClick below to buy 👇"
    buttons=[[InlineKeyboardButton("🛒 Buy Now",callback_data=f"buy_{book_name}")]]
    if book.get("cover"):
        await query.message.reply_photo(photo=book["cover"],caption=caption,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await query.message.reply_text(caption,parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(buttons))

async def buy_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    book_name=query.data.split("_",1)[1]
    book=DATA["books"].get(book_name)
    if not book:
        await query.message.reply_text("❌ Book not found!")
        return
    upi=DATA.get("upi","")
    qr=DATA.get("qr","")
    msg=f"📖 *{html.escape(book_name)}*\n💰 Price: ₹{book['price']}\n\n"
    if upi:
        msg+=f"📤 Pay via UPI:\n`{upi}`\n\n"
    else:
        msg+="⚠️ UPI not set yet\n"
    msg+="After payment, send payment screenshot here. Admin will approve and deliver book ✅"
    DATA["pending"][str(query.from_user.id)]=book_name
    save_data()
    if qr:
        await query.message.reply_photo(photo=qr,caption=msg,parse_mode="Markdown")
    else:
        await query.message.reply_text(msg,parse_mode="Markdown")

async def payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id=str(update.effective_user.id)
    if user_id in DATA["pending"]:
        book_name=DATA["pending"][user_id]
        if update.message.photo:
            file_id=update.message.photo[-1].file_id
        elif update.message.document:
            file_id=update.message.document.file_id
        else:
            await update.message.reply_text("⚠️ Send photo or document as proof")
            return
        await context.bot.send_message(chat_id=ADMIN_ID,text=f"💳 Payment proof from {update.effective_user.first_name} for *{book_name}*",parse_mode="Markdown")
        if update.message.photo or update.message.document:
            await context.bot.send_photo(chat_id=ADMIN_ID,photo=file_id)
        await update.message.reply_text("✅ Payment proof sent to admin. Wait for approval.")
    else:
        await update.message.reply_text("⚠️ No pending book purchase found.")

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text("🚫 Not authorized")
        return
    if len(context.args)<1:
        await update.message.reply_text("Usage: /approve <user_id>")
        return
    user_id=context.args[0]
    if user_id in DATA["pending"]:
        book_name=DATA["pending"][user_id]
        book=DATA["books"].get(book_name)
        if book and book.get("file"):
            await context.bot.send_document(chat_id=int(user_id),document=book["file"],caption=f"📖 Here is your book *{book_name}*",parse_mode="Markdown")
            DATA["pending"].pop(user_id)
            save_data()
            await update.message.reply_text(f"✅ Delivered *{book_name}* to user {user_id}",parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Book file not uploaded yet")
    else:
        await update.message.reply_text("❌ No pending request for this user")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query=update.callback_query
    await query.answer()
    data=query.data
    if data=="show_books":
        await show_books(update,context)
    elif data.startswith("book_"):
        await book_details(update,context)
    elif data.startswith("buy_"):
        await buy_book(update,context)

def main():
    print("🤖 Full Book Store Bot running...")
    app=ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("addbook",addbook))
    app.add_handler(MessageHandler(filters.Document.ALL,receive_file))
    app.add_handler(CommandHandler("setupi",setupi))
    app.add_handler(CommandHandler("setqr",setqr))
    app.add_handler(CommandHandler("books",list_books))
    app.add_handler(CommandHandler("approve",approve))
    app.add_handler(MessageHandler(filters.PHOTO, payment_proof))
    app.add_handler(CallbackQueryHandler(button_click))
    app.run_polling()

if __name__=="__main__":
    main()
