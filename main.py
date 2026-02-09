#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketLink Pro - Final Version (Data Persistent)
Optimized for Pydroid 3 / Termux
"""

import os
import csv
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)
from dotenv import load_dotenv

# ================= CONFIGURATION =================
# 1. Setup Absolute Paths (Prevents Data Loss on Restart)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
DB_PATH = os.path.join(BASE_DIR, "marketlink.db")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")

# 2. Load Environment Variables
load_dotenv(ENV_PATH)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# 3. Settings
FEE = 5000  # Subscription Fee (MMK)
TRIAL_DAYS = 3

# 4. Check Config
if not BOT_TOKEN or not ADMIN_ID:
    print("❌ ERROR: .env file is missing BOT_TOKEN or ADMIN_ID")
    exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    print("❌ ERROR: ADMIN_ID in .env must be a number")
    exit(1)

# ================= LOGGING =================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)

# ================= CONVERSATION STATES =================
# Order Flow
(ORDER_NAME, ORDER_PHONE, ORDER_ADDRESS, ORDER_SHOPPING, ORDER_ITEM_PHOTO, ORDER_PAY_PHOTO) = range(6)
# Edit Flow
(EDIT_LINK_ID, EDIT_LINK_TITLE, EDIT_LINK_URL) = range(6, 9)
(EDIT_PROD_ID, EDIT_PROD_NAME, EDIT_PROD_PRICE) = range(9, 12)
# Subscription Flow
(PAYMENT_WAIT,) = range(12, 13)


# ================= DATABASE MANAGER =================
def init_db():
    """Initialize database and create tables if they don't exist."""
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Table: Shops
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shops (
        owner_id INTEGER PRIMARY KEY,
        shop_name TEXT,
        expire_date TEXT,
        created_at TEXT
    )""")

    # Table: Products
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        name TEXT,
        price INTEGER
    )""")

    # Table: Links
    cur.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        title TEXT,
        url TEXT
    )""")

    # Table: Orders (Updated with item_photo_path)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER,
        user_id INTEGER,
        name TEXT,
        phone TEXT,
        address TEXT,
        items TEXT,
        total INTEGER,
        item_photo_path TEXT,
        pay_photo_path TEXT,
        status TEXT,
        created_at TEXT
    )""")

    # Table: Payments
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER,
        kind TEXT,
        ref_id INTEGER,
        photo_path TEXT,
        status TEXT,
        created_at TEXT
    )""")
    
    con.commit()
    con.close()
    log.info(f"✅ Database connected at: {DB_PATH}")

# --- DB Helpers ---
def get_db_connection():
    return sqlite3.connect(DB_PATH)

def db_get_shop(owner_id):
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM shops WHERE owner_id=?", (owner_id,))
        return cur.fetchone()

def db_set_shop(owner_id, name, expire_date):
    with get_db_connection() as con:
        con.execute("INSERT OR REPLACE INTO shops VALUES (?, ?, ?, ?)", 
                   (owner_id, name, expire_date, datetime.now().strftime("%Y-%m-%d")))

def db_extend_shop(owner_id, days):
    shop = db_get_shop(owner_id)
    if not shop: return None
    current_exp = datetime.strptime(shop[2], "%Y-%m-%d")
    if current_exp < datetime.now():
        current_exp = datetime.now()
    new_exp = (current_exp + timedelta(days=days)).strftime("%Y-%m-%d")
    with get_db_connection() as con:
        con.execute("UPDATE shops SET expire_date=? WHERE owner_id=?", (new_exp, owner_id))
    return new_exp

def db_add_product(owner_id, name, price):
    with get_db_connection() as con:
        con.execute("INSERT INTO products (owner_id, name, price) VALUES (?, ?, ?)", (owner_id, name, price))

def db_list_products(owner_id):
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT id, name, price FROM products WHERE owner_id=?", (owner_id,))
        return cur.fetchall()

def db_delete_product(pid, owner_id):
    with get_db_connection() as con:
        con.execute("DELETE FROM products WHERE id=? AND owner_id=?", (pid, owner_id))

def db_create_order(shop_id, user_id, data):
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO orders 
            (shop_id, user_id, name, phone, address, items, total, item_photo_path, pay_photo_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            shop_id, user_id, 
            data['name'], data['phone'], data['address'], 
            ", ".join(data['cart']), data['total'], 
            data.get('item_photo', 'None'), 
            data.get('pay_photo', 'None'), 
            "Pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        return cur.lastrowid

def db_get_order(oid):
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (oid,))
        return cur.fetchone()

def db_update_order_status(oid, status):
    with get_db_connection() as con:
        con.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))

def db_insert_payment(uid, kind, ref_id, path):
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO payments (uid, kind, ref_id, photo_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (uid, kind, ref_id, path, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return cur.lastrowid

def db_update_payment_status(pid, status):
    with get_db_connection() as con:
        con.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))

def is_shop_active(owner_id):
    if owner_id == ADMIN_ID: return True
    shop = db_get_shop(owner_id)
    if not shop: return False
    try:
        exp_date = datetime.strptime(shop[2], "%Y-%m-%d")
        return datetime.now() <= exp_date + timedelta(days=1) # Allow until end of day
    except:
        return False

# ================= BOT HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = context.args

    # 1. Deep Link (Customer entering a shop)
    if args:
        try:
            shop_id = int(args[0])
            if not is_shop_active(shop_id):
                await update.message.reply_text("⚠️ ဤဆိုင်သည် သက်တမ်းကုန်ဆုံးသွားပါပြီ။")
                return
            
            shop = db_get_shop(shop_id)
            context.user_data["current_shop_id"] = shop_id
            await update.message.reply_text(
                f"🏪 **{shop[1]}** မှ ကြိုဆိုပါသည်။\n\n🛍️ စျေးဝယ်ရန် /order ကိုနှိပ်ပါ။",
                parse_mode="Markdown"
            )
            return
        except ValueError:
            pass

    # 2. Admin Panel
    if uid == ADMIN_ID:
        kb = [["📊 Dashboard", "📥 Pending Payments"], ["🏬 All Shops", "📢 Broadcast"]]
        await update.message.reply_text("👑 **Admin Panel**", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return

    # 3. Shop Owner Panel
    shop = db_get_shop(uid)
    if shop:
        if not is_shop_active(uid):
            await update.message.reply_text(f"⚠️ သင့်ဆိုင်သက်တမ်းကုန်နေပါပြီ။\nသက်တမ်း: {shop[2]}\n\nသက်တမ်းတိုးရန် /pay_subscribe ကိုနှိပ်ပါ။")
            return
            
        kb = [
            ["➕ Add Product", "📦 My Orders"],
            ["🗑️ Del Product", "📋 Product List"],
            ["🔗 My Link", "💳 Subscription"]
        ]
        await update.message.reply_text(f"🏪 **Owner Panel: {shop[1]}**\nExpires: {shop[2]}", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return

    # 4. New User
    kb = [["📝 Create Shop"]]
    await update.message.reply_text("👋 Welcome to MarketLink Pro!\n\nဆိုင်ဖွင့်ရန်အောက်ပါခလုတ်ကိုနှိပ်ပါ။", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /setup_shop Name"""
    uid = update.effective_user.id
    if db_get_shop(uid):
        await update.message.reply_text("✅ You already have a shop.")
        return

    name = " ".join(context.args)
    if not name:
        await update.message.reply_text("အသုံးပြုပုံ: /setup_shop <ဆိုင်နာမည်>\nExample: /setup_shop MyFashionStore")
        return

    exp_date = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
    db_set_shop(uid, name, exp_date)
    await update.message.reply_text(f"✅ Shop '{name}' created!\nTrial expires: {exp_date}\n\nType /start to see your panel.")

# --- Product Management ---
async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("အသုံးပြုပုံ: /add_product <အမည်> <စျေးနှုန်း>\nExample: /add_product Shirt 15000")
        return
    try:
        price = int(context.args[-1])
        name = " ".join(context.args[:-1])
        db_add_product(uid, name, price)
        await update.message.reply_text(f"✅ Added: {name} - {price} MMK")
    except ValueError:
        await update.message.reply_text("Price must be a number.")

async def cmd_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prods = db_list_products(uid)
    if not prods:
        await update.message.reply_text("ပစ္စည်းမရှိသေးပါ။")
        return
    text = "📦 **Your Products**\n\n"
    for p in prods:
        text += f"🆔 `{p[0]}` : {p[1]} - {p[2]} MMK\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_del_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: /del_product <ID>")
        return
    try:
        pid = int(context.args[0])
        db_delete_product(pid, uid)
        await update.message.reply_text("✅ Product deleted.")
    except:
        await update.message.reply_text("Error deleting product.")

# --- ORDER CONVERSATION ---
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "current_shop_id" not in context.user_data:
        await update.message.reply_text("⚠️ ကျေးဇူးပြု၍ ဆိုင် Link မှတဆင့် ဝင်ရောက်ပါ။")
        return ConversationHandler.END
    
    context.user_data["cart"] = []
    context.user_data["total"] = 0
    await update.message.reply_text("အမည် (Name) ရေးပေးပါ:", reply_markup=ReplyKeyboardRemove())
    return ORDER_NAME

async def order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_name"] = update.message.text
    await update.message.reply_text("ဖုန်းနံပါတ် (Phone):")
    return ORDER_PHONE

async def order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_phone"] = update.message.text
    await update.message.reply_text("ပို့ဆောင်ရမည့် လိပ်စာ (Address):")
    return ORDER_ADDRESS

async def order_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_address"] = update.message.text
    
    # Show Shop Menu
    sid = context.user_data["current_shop_id"]
    prods = db_list_products(sid)
    
    kb = []
    for p in prods:
        kb.append([f"{p[1]} : {p[2]}"]) # Button format "Name : Price"
    kb.append(["✅ Done / Checkout"])
    
    await update.message.reply_text(
        "မိမိလိုချင်သော ပစ္စည်းများကို နှိပ်၍ ရွေးချယ်ပါ။ (ပြီးလျှင် Done ကိုနှိပ်ပါ)",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=False)
    )
    return ORDER_SHOPPING

async def order_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "✅ Done / Checkout":
        if not context.user_data["cart"]:
            await update.message.reply_text("စျေးခြင်းတောင်းထဲတွင် ဘာမှမရှိသေးပါ။")
            return ORDER_SHOPPING
        
        cart_summary = "\n".join(context.user_data["cart"])
        total = context.user_data["total"]
        msg = f"📋 **Order Summary**\n{cart_summary}\n\n💰 Total: {total} MMK"
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        
        # Ask for Item Photo (Feature Request)
        await update.message.reply_text(
            "📸 **ပစ္စည်းပုံ ပြခြင်း**\n\nမိမိမှာယူသည့်ပစ္စည်းသေချာစေရန် ပုံရှိပါက ပို့ပေးပါ။\n(ပုံမရှိလျှင် /skip ဟု ရိုက်ထည့်နိုင်ပါသည်။)"
        )
        return ORDER_ITEM_PHOTO

    # Parse product selection
    if ":" in text:
        try:
            name_part, price_part = text.rsplit(":", 1)
            name = name_part.strip()
            price = int(price_part.strip())
            
            context.user_data["cart"].append(f"{name} - {price}")
            context.user_data["total"] += price
            await update.message.reply_text(f"➕ Added {name}. Total: {context.user_data['total']}")
        except:
            await update.message.reply_text("Error selecting item.")
            
    return ORDER_SHOPPING

async def order_item_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle Photo or Skip
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        filename = f"item_ref_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
        path = os.path.join(PHOTOS_DIR, filename)
        await file.download_to_drive(path)
        context.user_data["item_photo"] = path
        await update.message.reply_text("✅ ပစ္စည်းပုံ ရရှိပါသည်။")
    else:
        context.user_data["item_photo"] = None
        if update.message.text != "/skip":
            await update.message.reply_text("Skipped item photo.")

    await update.message.reply_text("💸 **ငွေပေးချေခြင်း**\n\nငွေလွှဲပြေစာ (Screenshot) ပို့ပေးပါ။ (Kpay/Wave)")
    return ORDER_PAY_PHOTO

async def order_pay_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ ကျေးဇူးပြု၍ ဓာတ်ပုံ ပို့ပေးပါ။")
        return ORDER_PAY_PHOTO

    # Save Payment Photo
    file = await update.message.photo[-1].get_file()
    filename = f"pay_order_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
    path = os.path.join(PHOTOS_DIR, filename)
    await file.download_to_drive(path)
    context.user_data["pay_photo"] = path

    # Create Order in DB
    sid = context.user_data["current_shop_id"]
    uid = update.effective_user.id
    
    order_data = {
        "name": context.user_data["order_name"],
        "phone": context.user_data["order_phone"],
        "address": context.user_data["order_address"],
        "cart": context.user_data["cart"],
        "total": context.user_data["total"],
        "item_photo": context.user_data["item_photo"],
        "pay_photo": context.user_data["pay_photo"]
    }
    
    oid = db_create_order(sid, uid, order_data)
    pid = db_insert_payment(uid, "order", oid, path)

    await update.message.reply_text(f"✅ Order #{oid} Submitted! ဆိုင်ရှင်အတည်ပြုမှုကို စောင့်ဆိုင်းပေးပါ။")

    # Notify Shop Owner
    try:
        msg_text = (f"🔔 **New Order #{oid}**\n\n"
                    f"👤 {order_data['name']} ({order_data['phone']})\n"
                    f"📍 {order_data['address']}\n"
                    f"🛒 Items: {len(order_data['cart'])}\n"
                    f"💰 Total: {order_data['total']} MMK")
        
        kb = [[
            InlineKeyboardButton("✅ Confirm", callback_data=f"ord_ok_{oid}_{pid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"ord_no_{oid}_{pid}")
        ]]
        
        # Send Payment Photo first
        with open(path, "rb") as f:
            await context.bot.send_photo(chat_id=sid, photo=f, caption=msg_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        
        # If Item Reference Photo exists, send it too
        if order_data['item_photo']:
            with open(order_data['item_photo'], "rb") as f:
                await context.bot.send_photo(chat_id=sid, photo=f, caption=f"📸 Item Reference for Order #{oid}")
                
    except Exception as e:
        log.error(f"Failed to notify owner: {e}")

    return ConversationHandler.END

# --- Subscription Payment ---
async def sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📅 Subscription Fee: {FEE} MMK / Month\n\nငွေလွှဲ Screenshot ပို့ပေးပါ။")
    return PAYMENT_WAIT

async def sub_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Please send a photo.")
        return PAYMENT_WAIT
    
    file = await update.message.photo[-1].get_file()
    filename = f"sub_pay_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
    path = os.path.join(PHOTOS_DIR, filename)
    await file.download_to_drive(path)
    
    pid = db_insert_payment(update.effective_user.id, "subscription", 0, path)
    
    # Notify Admin
    kb = [[
        InlineKeyboardButton("✅ Approve", callback_data=f"sub_ok_{pid}_{update.effective_user.id}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"sub_no_{pid}_{update.effective_user.id}")
    ]]
    with open(path, "rb") as f:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=f"💸 Subscription Request\nUID: {update.effective_user.id}", reply_markup=InlineKeyboardMarkup(kb))
    
    await update.message.reply_text("✅ Payment sent to Admin. Please wait.")
    return ConversationHandler.END

# --- Callback Handler (Admin/Owner Actions) ---
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split("_")
    action = data[0] + "_" + data[1] # ord_ok, sub_no etc.
    
    if action == "sub_ok":
        pid, uid = int(data[2]), int(data[3])
        new_date = db_extend_shop(uid, 30)
        db_update_payment_status(pid, "approved")
        await context.bot.send_message(uid, f"✅ Subscription Approved!\nNew Expiry: {new_date}")
        await q.edit_message_caption(f"✅ Approved Sub for {uid}")

    elif action == "sub_no":
        pid, uid = int(data[2]), int(data[3])
        db_update_payment_status(pid, "rejected")
        await context.bot.send_message(uid, "❌ Subscription Rejected.")
        await q.edit_message_caption(f"❌ Rejected Sub for {uid}")

    elif action == "ord_ok":
        oid, pid = int(data[2]), int(data[3])
        db_update_order_status(oid, "Confirmed")
        db_update_payment_status(pid, "approved")
        order = db_get_order(oid)
        await context.bot.send_message(order[2], f"✅ Your Order #{oid} has been confirmed!") # order[2] is user_id
        await q.edit_message_caption(f"✅ Order #{oid} Confirmed")

    elif action == "ord_no":
        oid, pid = int(data[2]), int(data[3])
        db_update_order_status(oid, "Rejected")
        db_update_payment_status(pid, "rejected")
        order = db_get_order(oid)
        await context.bot.send_message(order[2], f"❌ Your Order #{oid} was rejected.")
        await q.edit_message_caption(f"❌ Order #{oid} Rejected")

# --- Export to Excel (CSV) ---
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # Get orders for this shop
    with get_db_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM orders WHERE shop_id=?", (uid,))
        rows = cur.fetchall()
    
    if not rows:
        await update.message.reply_text("No orders found.")
        return

    # Write CSV
    filename = f"orders_{uid}_{int(datetime.now().timestamp())}.csv"
    filepath = os.path.join(BASE_DIR, filename)
    
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "ShopID", "UserID", "Name", "Phone", "Address", "Items", "Total", "ItemPhoto", "PayPhoto", "Status", "Date"])
        writer.writerows(rows)
    
    await update.message.reply_document(document=open(filepath, "rb"), filename=filename)
    os.remove(filepath)

# --- Menu Handler ---
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📝 Create Shop":
        await update.message.reply_text("Use: /setup_shop <Name>")
    elif text == "➕ Add Product":
        await update.message.reply_text("Use: /add_product <Name> <Price>")
    elif text == "🗑️ Del Product":
        await update.message.reply_text("Use: /del_product <ID>")
    elif text == "📋 Product List":
        await cmd_list_products(update, context)
    elif text == "🔗 My Link":
        bot_user = await context.bot.get_me()
        await update.message.reply_text(f"Your Shop Link:\nhttps://t.me/{bot_user.username}?start={update.effective_user.id}")
    elif text == "📦 My Orders":
        await cmd_export(update, context)
    elif text == "💳 Subscription":
        await update.message.reply_text("Use /pay_subscribe to extend.")
    elif text == "📊 Dashboard" and update.effective_user.id == ADMIN_ID:
        # Simple stats
        with get_db_connection() as con:
            cur = con.cursor()
            shops = cur.execute("SELECT count(*) FROM shops").fetchone()[0]
            orders = cur.execute("SELECT count(*) FROM orders").fetchone()[0]
        await update.message.reply_text(f"Shops: {shops}\nOrders: {orders}")
    elif text == "📥 Pending Payments" and update.effective_user.id == ADMIN_ID:
        with get_db_connection() as con:
            cur = con.cursor()
            rows = cur.execute("SELECT * FROM payments WHERE status='pending'").fetchall()
        if not rows: await update.message.reply_text("No pending payments.")
        for p in rows:
            kb = [[
                InlineKeyboardButton("Approve", callback_data=f"sub_ok_{p[0]}_{p[1]}"),
                InlineKeyboardButton("Reject", callback_data=f"sub_no_{p[0]}_{p[1]}")
            ]]
            # Note: For simplicity only showing sub payments here, extending for orders requires more logic
            if p[2] == "subscription":
                 with open(p[4], "rb") as f:
                    await update.message.reply_photo(photo=f, caption=f"Sub Request UID:{p[1]}", reply_markup=InlineKeyboardMarkup(kb))

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ================= MAIN =================
def main():
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation: Order
    order_conv = ConversationHandler(
        entry_points=[CommandHandler("order", order_start)],
        states={
            ORDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_name)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_phone)],
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_address)],
            ORDER_SHOPPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_shopping)],
            ORDER_ITEM_PHOTO: [MessageHandler((filters.PHOTO | filters.Regex("^/skip$")), order_item_photo)],
            ORDER_PAY_PHOTO: [MessageHandler(filters.PHOTO, order_pay_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Conversation: Subscription
    sub_conv = ConversationHandler(
        entry_points=[CommandHandler("pay_subscribe", sub_start)],
        states={PAYMENT_WAIT: [MessageHandler(filters.PHOTO, sub_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Handlers
    app.add_handler(order_conv)
    app.add_handler(sub_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup_shop", setup_shop))
    app.add_handler(CommandHandler("add_product", cmd_add_product))
    app.add_handler(CommandHandler("del_product", cmd_del_product))
    app.add_handler(CommandHandler("list_products", cmd_list_products))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
