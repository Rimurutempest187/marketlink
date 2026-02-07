#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MarketLink Pro - Full Bot (single file)
Upgraded to use aiosqlite (async sqlite) to reduce blocking.
Includes previous fixes: ADMIN_ID syntax, price-check, product name parsing,
admin callback authorization, cancel cleanup, list_links, etc.
"""
import os
import logging
import traceback
import asyncio
from datetime import datetime, timedelta

import aiosqlite
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
load_dotenv()
# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except Exception:
    ADMIN_ID = 0

DB_FILE = "bot.db"
PHOTOS_DIR = "photos"
FEE = 5000  # subscription fee in MMK
TRIAL_DAYS = 3

# Conversation states
(ORDER_NAME, ORDER_PHONE, ORDER_ADDRESS, ORDER_SHOPPING, ORDER_PHOTO) = range(5)
(EDIT_LINK_ID, EDIT_LINK_TITLE, EDIT_LINK_URL) = range(5, 8)
(EDIT_PROD_ID, EDIT_PROD_NAME, EDIT_PROD_PRICE) = range(8, 11)
(PAYMENT_WAIT,) = range(11, 12)

# ---------- LOG ----------
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
log = logging.getLogger(__name__)

# ---------- Async DB UTILITIES ----------
async def init_db():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.executescript(
            """
    CREATE TABLE IF NOT EXISTS shops (
        owner_id INTEGER PRIMARY KEY,
        shop_name TEXT,
        expire_date TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        name TEXT,
        price INTEGER
    );

    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        title TEXT,
        url TEXT
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shop_id INTEGER,
        user_id INTEGER,
        name TEXT,
        phone TEXT,
        address TEXT,
        items TEXT,
        total INTEGER,
        photo_path TEXT,
        status TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER,
        kind TEXT,
        ref_id INTEGER,
        photo_path TEXT,
        status TEXT,
        created_at TEXT
    );
    """
        )
        await db.commit()


async def db_get_shop(owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT owner_id, shop_name, expire_date, created_at FROM shops WHERE owner_id=?", (owner_id,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_set_shop(owner_id, shop_name, expire_date):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO shops(owner_id, shop_name, expire_date, created_at) VALUES(?,?,?,?)",
            (owner_id, shop_name, expire_date, datetime.now().strftime("%Y-%m-%d")),
        )
        await db.commit()


async def db_extend_shop(owner_id, days):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT expire_date FROM shops WHERE owner_id=?", (owner_id,))
        row = await cur.fetchone()
        await cur.close()
        if row and row[0]:
            try:
                cur_exp = datetime.strptime(row[0], "%Y-%m-%d")
            except Exception:
                cur_exp = datetime.now()
        else:
            cur_exp = datetime.now()
        new_exp = (cur_exp + timedelta(days=days)).strftime("%Y-%m-%d")
        await db.execute("UPDATE shops SET expire_date=? WHERE owner_id=?", (new_exp, owner_id))
        await db.commit()
        return new_exp


async def db_add_product(owner_id, name, price):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO products(owner_id, name, price) VALUES(?,?,?)", (owner_id, name, price))
        await db.commit()


async def db_list_products(owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, name, price FROM products WHERE owner_id=?", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_get_product(pid, owner_id=None):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        if owner_id:
            cur = await db.execute("SELECT id, name, price FROM products WHERE id=? AND owner_id=?", (pid, owner_id))
        else:
            cur = await db.execute("SELECT id, name, price FROM products WHERE id=?", (pid,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_product(pid, owner_id, name, price):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE products SET name=?, price=? WHERE id=? AND owner_id=?", (name, price, pid, owner_id))
        await db.commit()


async def db_delete_product(pid, owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM products WHERE id=? AND owner_id=?", (pid, owner_id))
        await db.commit()


async def db_add_link(owner_id, title, url):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT INTO links(owner_id, title, url) VALUES(?,?,?)", (owner_id, title, url))
        await db.commit()


async def db_list_links(owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, title, url FROM links WHERE owner_id=?", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_get_link(lid, owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, title, url FROM links WHERE id=? AND owner_id=?", (lid, owner_id))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_link(lid, owner_id, title, url):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE links SET title=?, url=? WHERE id=? AND owner_id=?", (title, url, lid, owner_id))
        await db.commit()


async def db_create_order(shop_id, user_id, name, phone, address, items, total, photo_path):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "INSERT INTO orders(shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (shop_id, user_id, name, phone, address, items, total, photo_path, "Pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await db.commit()
        oid = cur.lastrowid
        await cur.close()
        return oid


async def db_get_order(oid):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, shop_id, user_id, name, phone, address, items, total, photo_path, status, created_at FROM orders WHERE id=?", (oid,))
        row = await cur.fetchone()
        await cur.close()
        return row


async def db_update_order_status(oid, status):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        await db.commit()


async def db_insert_payment(uid, kind, ref_id, photo_path):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("INSERT INTO payments(uid, kind, ref_id, photo_path, status, created_at) VALUES(?,?,?,?,?,?)", (uid, kind, ref_id, photo_path, "pending", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        await db.commit()
        pid = cur.lastrowid
        await cur.close()
        return pid


async def db_get_pending_payments():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, uid, kind, ref_id, photo_path, status, created_at FROM payments WHERE status='pending' ORDER BY id ASC")
        rows = await cur.fetchall()
        await cur.close()
        return rows


async def db_update_payment_status(pid, status):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE payments SET status=? WHERE id=?", (status, pid))
        await db.commit()


async def db_list_orders_by_shop(owner_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, user_id, name, phone, address, items, total, status, created_at FROM orders WHERE shop_id=? ORDER BY id DESC", (owner_id,))
        rows = await cur.fetchall()
        await cur.close()
        return rows


# ---------- HELPERS ----------
async def is_shop_active(owner_id):
    if owner_id == ADMIN_ID:
        return True
    shop = await db_get_shop(owner_id)
    if not shop:
        return False
    exp = shop["expire_date"] if "expire_date" in shop.keys() else shop[2]
    if not exp:
        return False
    try:
        return datetime.now().date() <= datetime.strptime(exp, "%Y-%m-%d").date()
    except Exception:
        return False


async def extend_by_days(owner_id, days):
    return await db_extend_shop(owner_id, days)


def fmt_date(d):
    return d if d else "-"


# ---------- BOT HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        args = context.args or []

        # deep link to shop: /start <shop_id>
        if args:
            try:
                shop_id = int(args[0])
            except:
                await update.message.reply_text("Invalid link.")
                return
            if not await is_shop_active(shop_id):
                await update.message.reply_text("❌ ဆိုင်သည် သက်တမ်းကုန်ဆုံးနေပါသည်။")
                return
            context.user_data["current_shop_id"] = shop_id
            shop = await db_get_shop(shop_id)
            if shop:
                await update.message.reply_text(f"🏪 **{shop['shop_name']}** မှ ကြိုဆိုပါသည်။\n/Order ဖြင့်မှာယူပါ။", reply_markup=ReplyKeyboardRemove())
            else:
                await update.message.reply_text("Shop not found.")
            return

        # Admin
        if uid == ADMIN_ID:
            kb = [["📊 Platform Stats", "📥 Pending Payments"], ["🏬 All Shops", "📤 Broadcast"]]
            await update.message.reply_text("👑 Admin Panel", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        # Owner
        shop = await db_get_shop(uid)
        if shop:
            if not await is_shop_active(uid):
                await update.message.reply_text("❌ Your shop subscription has expired. Please renew using /pay_subscribe.")
                return
            kb = [["➕ Add Product", "🛒 My Orders"], ["🔗 My Link", "💳 Subscription"]]
            await update.message.reply_text(f"🏪 Owner Panel: {shop['shop_name']}", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
            return

        # New user
        kb = [["📝 Create Shop (/setup_shop MyShopName)", "ℹ️ Help"]]
        await update.message.reply_text("Welcome to MarketLink Pro!\nTo create a shop: /setup_shop <ShopName>", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    except Exception:
        log.exception("start error")


# ----- Shop setup -----
async def setup_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        name = " ".join(context.args or [])
        if not name:
            await update.message.reply_text("Usage: /setup_shop <Shop Name>")
            return
        exp = (datetime.now() + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d")
        await db_set_shop(uid, name, exp)
        await update.message.reply_text(f"✅ Shop created: {name}\nTrial until {exp}\nGo to /start to open your panel.")
    except Exception:
        log.exception("setup_shop")


# ----- Product commands -----
async def cmd_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        shop = await db_get_shop(uid)
        if not shop:
            await update.message.reply_text("You are not a shop owner. Create shop with /setup_shop")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /add_product <name> <price>\nExample: /add_product \"Red Scarf\" 15000")
            return
        try:
            price = int(context.args[-1])
        except:
            await update.message.reply_text("Price must be a number.")
            return
        name = " ".join(context.args[:-1])
        await db_add_product(uid, name, price)
        await update.message.reply_text("✅ Product added.")
    except Exception:
        log.exception("add_product")


async def cmd_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        rows = await db_list_products(uid)
        if not rows:
            await update.message.reply_text("No products yet. Add with /add_product")
            return
        text = "📦 Your Products:\n\n"
        for r in rows:
            text += f"ID:{r['id']} • {r['name']} • {r['price']} MMK\n"
        await update.message.reply_text(text)
    except Exception:
        log.exception("list_products")


# Edit product (conversation)
async def edit_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_products(uid)
    if not rows:
        await update.message.reply_text("No products to edit.")
        return ConversationHandler.END
    msg = "Send Product ID to edit:\n\n"
    for r in rows:
        msg += f"ID:{r['id']} • {r['name']} • {r['price']} MMK\n"
    await update.message.reply_text(msg)
    return EDIT_PROD_ID


async def edit_product_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.strip())
    except:
        await update.message.reply_text("Send a valid numeric ID.")
        return EDIT_PROD_ID
    uid = update.effective_user.id
    prod = await db_get_product(pid, uid)
    if not prod:
        await update.message.reply_text("Product not found or not yours.")
        return EDIT_PROD_ID
    context.user_data["edit_product_id"] = pid
    await update.message.reply_text(f"Old name: {prod['name']}\nSend new name:")
    return EDIT_PROD_NAME


async def edit_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_product_name"] = update.message.text.strip()
    await update.message.reply_text("Send new price:")
    return EDIT_PROD_PRICE


async def edit_product_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip())
    except:
        await update.message.reply_text("Price must be numeric. Send price again.")
        return EDIT_PROD_PRICE
    pid = context.user_data.pop("edit_product_id")
    name = context.user_data.pop("edit_product_name")
    uid = update.effective_user.id
    await db_update_product(pid, uid, name, price)
    await update.message.reply_text("✅ Product updated.")
    return ConversationHandler.END


# delete product
async def cmd_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if len(context.args) < 1:
            await update.message.reply_text("Usage: /del_product <product_id>")
            return
        pid = int(context.args[0])
        await db_delete_product(pid, uid)
        await update.message.reply_text("✅ Product deleted (if it belonged to you).")
    except Exception:
        log.exception("del_product")


# ----- Links -----
async def cmd_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /add_link <title> <url>")
            return
        title = context.args[0]
        url = context.args[1]
        await db_add_link(uid, title, url)
        await update.message.reply_text("✅ Link added.")
    except Exception:
        log.exception("add_link")


async def cmd_list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.effective_user.id
        rows = await db_list_links(uid)
        if not rows:
            await update.message.reply_text("No links.")
            return
        txt = "🔗 Your Links:\n\n"
        for r in rows:
            txt += f"ID:{r['id']} • {r['title']} • {r['url']}\n"
        await update.message.reply_text(txt)
    except Exception:
        log.exception("list_links")


# Edit link conversation
async def edit_link_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = await db_list_links(uid)
    if not rows:
        await update.message.reply_text("No links to edit.")
        return ConversationHandler.END
    msg = "✏️ Your Links (ID)\n\n"
    for r in rows:
        msg += f"ID:{r['id']} • {r['title']} • {r['url']}\n"
    msg += "\nSend Link ID to edit:"
    await update.message.reply_text(msg)
    return EDIT_LINK_ID


async def edit_link_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lid = int(update.message.text.strip())
    except:
        await update.message.reply_text("Send valid numeric ID.")
        return EDIT_LINK_ID
    uid = update.effective_user.id
    link = await db_get_link(lid, uid)
    if not link:
        await update.message.reply_text("Not found / not your link.")
        return EDIT_LINK_ID
    context.user_data["edit_link_id"] = lid
    await update.message.reply_text(f"Old title: {link['title']}\nSend new title:")
    return EDIT_LINK_TITLE


async def edit_link_get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_link_title"] = update.message.text.strip()
    await update.message.reply_text("Send new URL:")
    return EDIT_LINK_URL


async def edit_link_get_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lid = context.user_data.pop("edit_link_id")
    title = context.user_data.pop("edit_link_title")
    url = update.message.text.strip()
    uid = update.effective_user.id
    await db_update_link(lid, uid, title, url)
    await update.message.reply_text("✅ Link updated.")
    return ConversationHandler.END


# ----- Order Flow -----
async def order_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "current_shop_id" not in context.user_data:
        await update.message.reply_text("Please open shop link first (/start <shop_id>)")
        return ConversationHandler.END
    context.user_data["cart"] = []
    context.user_data["total"] = 0
    await update.message.reply_text("Name:", reply_markup=ReplyKeyboardRemove())
    return ORDER_NAME


async def order_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_name"] = update.message.text.strip()
    await update.message.reply_text("Phone:")
    return ORDER_PHONE


async def order_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_phone"] = update.message.text.strip()
    await update.message.reply_text("Address:")
    return ORDER_ADDRESS


async def order_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["cust_address"] = update.message.text.strip()
    # show products
    sid = context.user_data["current_shop_id"]
    prods = await db_list_products(sid)
    if not prods:
        await update.message.reply_text("This shop has no products.")
        return ConversationHandler.END
    kb = []
    for p in prods:
        kb.append([f"{p['name']}:{p['price']}"])
    kb.append(["🛒 View Cart", "✅ Checkout"])
    await update.message.reply_text("Select product (name:price) to add to cart:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ORDER_SHOPPING


async def order_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "🛒 View Cart":
        cart = context.user_data.get("cart", [])
        total = context.user_data.get("total", 0)
        await update.message.reply_text(f"Cart: {', '.join(cart) if cart else 'Empty'}\nTotal: {total} MMK")
        return ORDER_SHOPPING
    if text == "✅ Checkout":
        await update.message.reply_text("Send payment screenshot (WavePay / KBZPay) or type /cancel to abort", reply_markup=ReplyKeyboardRemove())
        return ORDER_PHOTO
    # expect name:price but verify price from DB
    if ":" in text:
        try:
            name = text.split(":", 1)[0].strip()
            sid = context.user_data["current_shop_id"]
            prods = await db_list_products(sid)
            prod = next((p for p in prods if p["name"] == name), None)
            if not prod:
                await update.message.reply_text("Invalid product. Use the shown buttons or type the correct product name.")
                return ORDER_SHOPPING
            price = int(prod["price"])
            context.user_data.setdefault("cart", []).append(f"{name}:{price}")
            context.user_data["total"] = context.user_data.get("total", 0) + price
            await update.message.reply_text(f"Added {name} - {price} MMK. Total: {context.user_data['total']}")
        except Exception:
            log.exception("order_shopping parsing")
            await update.message.reply_text("Format must be name:price (use provided buttons).")
    return ORDER_SHOPPING


async def order_photo_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            await update.message.reply_text("Send a photo (screenshot) of payment.")
            return ORDER_PHOTO
        photo_file = await update.message.photo[-1].get_file()
        filename = f"{PHOTOS_DIR}/pay_order_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
        await photo_file.download_to_drive(filename)
        sid = context.user_data["current_shop_id"]
        uid = update.effective_user.id
        oid = await db_create_order(sid, uid, context.user_data.get("cust_name"), context.user_data.get("cust_phone"), context.user_data.get("cust_address"), ",".join(context.user_data.get("cart", [])), context.user_data.get("total", 0), filename)
        pid = await db_insert_payment(uid, "order", oid, filename)
        shop = await db_get_shop(sid)
        owner_id = shop["owner_id"] if shop and "owner_id" in shop.keys() else (shop[0] if shop else None)
        kb = [
            [InlineKeyboardButton("Confirm Order ✅", callback_data=f"order_conf_{oid}_{pid}"),
             InlineKeyboardButton("Reject ❌", callback_data=f"order_rej_{oid}_{pid}")]
        ]
        target = owner_id or ADMIN_ID
        try:
            with open(filename, "rb") as f:
                await context.bot.send_photo(chat_id=target, photo=f, caption=f"New order #{oid}\nFrom: {uid}\nTotal: {context.user_data.get('total')} MMK", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            log.exception("notify owner failed")
        await update.message.reply_text("✅ Order received and pending owner confirmation. You'll be notified.")
    except Exception:
        log.exception("order_photo_receive")
        await update.message.reply_text("Failed to process order. Please try again.")
    finally:
        context.user_data.pop("cart", None)
        context.user_data.pop("total", None)
        context.user_data.pop("cust_name", None)
        context.user_data.pop("cust_phone", None)
        context.user_data.pop("cust_address", None)
    return ConversationHandler.END


# ----- Subscription Payment (user pays to extend) -----
async def pay_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Subscription fee: {FEE} MMK\nSend screenshot after payment.")
    return PAYMENT_WAIT


async def pay_subscription_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.photo:
            await update.message.reply_text("Send a photo (screenshot) of payment.")
            return PAYMENT_WAIT
        photo_file = await update.message.photo[-1].get_file()
        filename = f"{PHOTOS_DIR}/pay_sub_{update.effective_user.id}_{int(datetime.now().timestamp())}.jpg"
        await photo_file.download_to_drive(filename)
        pid = await db_insert_payment(update.effective_user.id, "subscription", None, filename)
        kb = [
            [InlineKeyboardButton("Approve ✅", callback_data=f"sub_ok_{pid}_{update.effective_user.id}"),
             InlineKeyboardButton("Reject ❌", callback_data=f"sub_no_{pid}_{update.effective_user.id}")]
        ]
        try:
            with open(filename, "rb") as f:
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=f"Subscription payment (uid={update.effective_user.id})", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            log.exception("notify admin subscription failed")
        await update.message.reply_text("✅ Payment submitted. Waiting admin approval.")
    except Exception:
        log.exception("pay_subscription_receive")
        await update.message.reply_text("Failed to send payment. Try again.")
    return ConversationHandler.END


# ----- Admin / Owner callbacks (approve subscription / orders) -----
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    caller = q.from_user.id if q.from_user else None
    try:
        if data.startswith("sub_ok_") or data.startswith("sub_no_"):
            if caller != ADMIN_ID:
                await q.answer("Not authorized", show_alert=True)
                return
            parts = data.split("_")
            action = parts[1]
            pid = int(parts[2])
            uid = int(parts[3])
            if action == "ok":
                new_exp = await extend_by_days(uid, 30)
                await db_update_payment_status(pid, "approved")
                try:
                    await context.bot.send_message(uid, f"✅ Subscription approved. New expiry: {new_exp}")
                except Exception:
                    log.exception("notify user subscription approved failed")
                await q.edit_message_caption(caption=f"Subscription processed. Approved -> UID {uid}")
            else:
                await db_update_payment_status(pid, "rejected")
                try:
                    await context.bot.send_message(uid, "❌ Subscription payment rejected by admin.")
                except Exception:
                    log.exception("notify user subscription rejected failed")
                await q.edit_message_caption(caption=f"Subscription processed. Rejected -> UID {uid}")

        elif data.startswith("order_conf_") or data.startswith("order_rej_"):
            parts = data.split("_")
            action = parts[1]
            oid = int(parts[2])
            pid = int(parts[3])
            order = await db_get_order(oid)
            if not order:
                await q.edit_message_text("Order not found.")
                return
            user_id = order["user_id"] if "user_id" in order.keys() else order[2]
            shop_id = order["shop_id"] if "shop_id" in order.keys() else order[1]
            shop = await db_get_shop(shop_id)
            owner_id = shop["owner_id"] if shop and "owner_id" in shop.keys() else (shop[0] if shop else None)
            if caller != ADMIN_ID and caller != owner_id:
                await q.answer("Not authorized", show_alert=True)
                return
            if action == "conf":
                await db_update_order_status(oid, "Confirmed")
                await db_update_payment_status(pid, "approved")
                try:
                    await context.bot.send_message(user_id, f"🔔 Your order #{oid} has been confirmed by the shop.")
                except Exception:
                    log.exception("notify user order confirmed failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Confirmed")
            else:
                await db_update_order_status(oid, "Rejected")
                await db_update_payment_status(pid, "rejected")
                try:
                    await context.bot.send_message(user_id, f"🔔 Your order #{oid} was rejected by the shop. Contact the shop for details.")
                except Exception:
                    log.exception("notify user order rejected failed")
                await q.edit_message_caption(caption=f"Order #{oid} - Rejected")
    except Exception:
        log.exception("admin_callback error")
        try:
            await q.edit_message_text("Processing failed. See logs.")
        except Exception:
            pass


# ----- Admin command: list pending payments -----
async def cmd_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin.")
        return
    rows = await db_get_pending_payments()
    if not rows:
        await update.message.reply_text("No pending payments.")
        return
    for p in rows:
        pid = p["id"]
        uid = p["uid"]
        kind = p["kind"]
        ref_id = p["ref_id"]
        path = p["photo_path"]
        status = p["status"]
        created = p["created_at"]
        text = f"PID:{pid} UID:{uid} Kind:{kind} Ref:{ref_id} Status:{status} Created:{created}"
        try:
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    kb = []
                    if kind == "subscription":
                        kb = [[InlineKeyboardButton("Approve", callback_data=f"sub_ok_{pid}_{uid}"), InlineKeyboardButton("Reject", callback_data=f"sub_no_{pid}_{uid}")]]
                    elif kind == "order":
                        kb = [[InlineKeyboardButton("Approve Order", callback_data=f"order_conf_{ref_id}_{pid}"), InlineKeyboardButton("Reject Order", callback_data=f"order_rej_{ref_id}_{pid}")]]
                    await context.bot.send_photo(chat_id=ADMIN_ID, photo=f, caption=text, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_text(text + "\n(photo missing)")
        except Exception:
            log.exception("cmd_pending_payments send failed")
            await update.message.reply_text(text + "\n(send failed)")


# ----- Owner export orders to excel -----
async def cmd_export_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import pandas as pd  # local import
    except Exception:
        await update.message.reply_text("Pandas not installed. Install pandas + openpyxl to export.")
        return
    uid = update.effective_user.id
    rows = await db_list_orders_by_shop(uid)
    if not rows:
        await update.message.reply_text("No orders.")
        return
    df = []
    for r in rows:
        df.append({
            "order_id": r["id"],
            "user_id": r["user_id"],
            "name": r["name"],
            "phone": r["phone"],
            "address": r["address"],
            "items": r["items"],
            "total": r["total"],
            "status": r["status"],
            "created_at": r["created_at"],
        })
    df = __import__("pandas").DataFrame(df)
    path = f"orders_{uid}_{int(datetime.now().timestamp())}.xlsx"
    try:
        df.to_excel(path, index=False)
        with open(path, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(path))
    except Exception:
        log.exception("export orders failed")
        await update.message.reply_text("Failed to export orders.")
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


# ----- Utility: show shop link -----
async def cmd_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        bot_username = (await context.bot.get_me()).username
    except Exception:
        bot_username = None
    if not bot_username:
        await update.message.reply_text("Bot username not available.")
        return
    await update.message.reply_text(f"https://t.me/{bot_username}?start={uid}")


# ----- Menu message handler -----
async def text_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    uid = update.effective_user.id

    # block expired owner quickly
    shop = await db_get_shop(uid)
    if shop and not await is_shop_active(uid):
        await update.message.reply_text("❌ Your subscription expired. Please renew with /pay_subscribe.")
        return

    # Owner options
    if t == "➕ Add Product" or t == "/add_product":
        await update.message.reply_text("Use /add_product <name> <price> or /list_products to manage.")
        return
    if t == "🛒 My Orders":
        rows = await db_list_orders_by_shop(uid)
        if not rows:
            await update.message.reply_text("No orders.")
            return
        msg = "📦 Your Orders:\n\n"
        for r in rows:
            msg += f"#{r['id']} | {r['name']} | {r['total']} MMK | {r['status']}\n"
        await update.message.reply_text(msg)
        return
    if t == "🔗 My Link":
        await cmd_my_link(update, context); return
    if t == "💳 Subscription":
        await update.message.reply_text(f"Subscription is {FEE} MMK per month.\nUse /pay_subscribe to pay.", reply_markup=ReplyKeyboardRemove()); return

    # Admin menu
    if uid == ADMIN_ID:
        if t == "📊 Platform Stats":
            async with aiosqlite.connect(DB_FILE) as con:
                cur = await con.execute("SELECT COUNT(*) FROM shops"); shops = (await cur.fetchone())[0]; await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM orders"); orders = (await cur.fetchone())[0]; await cur.close()
                cur = await con.execute("SELECT COUNT(*) FROM payments WHERE status='pending'"); pend = (await cur.fetchone())[0]; await cur.close()
            await update.message.reply_text(f"Shops:{shops}\nOrders:{orders}\nPending payments:{pend}")
            return
        if t == "📥 Pending Payments":
            await cmd_pending_payments(update, context); return
        if t == "🏬 All Shops":
            async with aiosqlite.connect(DB_FILE) as con:
                con.row_factory = aiosqlite.Row
                cur = await con.execute("SELECT owner_id, shop_name, expire_date FROM shops"); rows = await cur.fetchall(); await cur.close()
            txt = "All Shops:\n"
            for r in rows:
                txt += f"ID:{r['owner_id']} • {r['shop_name']} • Exp:{r['expire_date']}\n"
            await update.message.reply_text(txt)
            return

    # general fallback
    if t == "ℹ️ Help" or t == "/help":
        await update.message.reply_text("/setup_shop, /add_product, /list_products, /edit_product, /add_link, /edit_link, /order (open shop link first) /pay_subscribe")
        return

    # default echo
    await update.message.reply_text("Command not recognized. Use /help")

# ----- Fall back / cancel -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------- MAIN ----------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please export BOT_TOKEN environment variable.")
    if ADMIN_ID == 0:
        log.warning("ADMIN_ID is 0 or not set. Set ADMIN_ID environment variable to your Telegram id for admin actions.")

    # FIX 1: Use 'await' instead of asyncio.run() because we are already inside an async function
    log.info("Initializing Database...")
    await init_db()

    # FIX 2: Build the application
    app = Application.builder().token(BOT_TOKEN).build()

    # --- Conversation Handlers ---
    
    # Order conversation
    order_conv = ConversationHandler(
        entry_points=[CommandHandler("order", order_start)],
        states={
            ORDER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_name)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_phone)],
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_address)],
            ORDER_SHOPPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_shopping)],
            ORDER_PHOTO: [
                MessageHandler(filters.PHOTO, order_photo_receive),
                MessageHandler(filters.TEXT & ~filters.COMMAND, order_photo_receive),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Edit link conversation
    edit_link_conv = ConversationHandler(
        entry_points=[CommandHandler("edit_link", edit_link_start)],
        states={
            EDIT_LINK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_id)],
            EDIT_LINK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_title)],
            EDIT_LINK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_link_get_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Edit product conversation
    edit_prod_conv = ConversationHandler(
        entry_points=[CommandHandler("edit_product", edit_product_start)],
        states={
            EDIT_PROD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_get_id)],
            EDIT_PROD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_name)],
            EDIT_PROD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_product_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Subscription payment conversation
    pay_conv = ConversationHandler(
        entry_points=[CommandHandler("pay_subscribe", pay_subscription_start)],
        states={
            PAYMENT_WAIT: [
                MessageHandler(filters.PHOTO, pay_subscription_receive),
                MessageHandler(filters.TEXT & ~filters.COMMAND, pay_subscription_receive),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
    )

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setup_shop", setup_shop))
    app.add_handler(CommandHandler("add_product", cmd_add_product))
    app.add_handler(CommandHandler("list_products", cmd_list_products))
    app.add_handler(CommandHandler("del_product", cmd_delete_product))
    app.add_handler(edit_prod_conv)
    app.add_handler(CommandHandler("add_link", cmd_add_link))
    app.add_handler(CommandHandler("list_links", cmd_list_links))
    app.add_handler(edit_link_conv)
    app.add_handler(order_conv)
    app.add_handler(pay_conv)
    app.add_handler(CommandHandler("pending_payments", cmd_pending_payments))
    app.add_handler(CommandHandler("export_orders", cmd_export_orders))
    app.add_handler(CommandHandler("my_link", cmd_my_link))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_menu_handler))
    app.add_handler(CommandHandler("cancel", cancel))

# FIX 3: Start the bot properly using a context manager
    # This is the most stable way to run v20+ bots inside an async main
    log.info("Bot starting...")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # Keep the bot running until interrupted
        await asyncio.Event().wait()

if __name__ ==__ "main"__:
    try:
        # FIX 4: Correct the entry point name and run the async main
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")
