import os
import sqlite3
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, ConversationHandler, CallbackQueryHandler
)

# ---------- CONFIGURATION & PATHS ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "bot.db")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ---------- TRANSLATIONS (MM/EN) ----------
TEXTS = {
    'my': {
        'welcome': "MarketLink Pro မှ ကြိုဆိုပါတယ်။",
        'setup_help': "ဆိုင်ဖွင့်ရန်: `/setup_shop ဆိုင်အမည်`",
        'main_menu': "🏠 ပင်မမီနူး",
        'add_prod': "➕ ပစ္စည်းတင်ရန်",
        'my_prods': "📦 ပစ္စည်းစာရင်း",
        'orders': "🛒 အမှာစာများ",
        'broadcast': "📢 ကြော်ငြာပို့ရန်",
        'lang_btn': "🌐 Language (မြန်မာ/EN)",
        'ask_pname': "ပစ္စည်းအမည် ပို့ပေးပါ။",
        'ask_price': "ဈေးနှုန်း (ဂဏန်း) ပို့ပေးပါ။",
        'ask_stock': "လက်ကျန်အရေအတွက် (Stock) ပို့ပေးပါ။",
        'ask_photo': "ဓာတ်ပုံပို့ပေးပါ။ (မပို့လိုပါက /skip)",
        'cart_added': "ကို Cart ထဲ ထည့်လိုက်ပါပြီ။",
        'checkout_name': "ဝယ်ယူသူအမည် ပို့ပေးပါ။",
        'checkout_addr': "လိပ်စာနှင့် ဖုန်းနံပါတ် ပို့ပေးပါ။",
        'checkout_pay': "စုစုပေါင်း: {} MMK\nငွေလွှဲဖြတ်ပိုင်း ပို့ပေးပါ။",
        'stock_out': "❌ ပစ္စည်းပြတ်သွားပါပြီ။",
        'order_success': "✅ Order တင်ပြီးပါပြီ။ ဆိုင်ရှင်အတည်ပြုချက်ကို စောင့်ပါ။",
        'confirm_btn': "✅ အတည်ပြု",
        'reject_btn': "❌ ငြင်းပယ်",
        'bc_ask': "ပို့လိုသည့် ကြော်ငြာစာ (သို့မဟုတ်) ပုံကို ပို့ပေးပါ။",
        'bc_done': "✅ Customer {} ဦးထံ ပို့ပြီးပါပြီ။"
    },
    'en': {
        'welcome': "Welcome to MarketLink Pro.",
        'setup_help': "To setup: `/setup_shop ShopName`",
        'main_menu': "🏠 Main Menu",
        'add_prod': "➕ Add Product",
        'my_prods': "📦 My Products",
        'orders': "🛒 Orders",
        'broadcast': "📢 Broadcast",
        'lang_btn': "🌐 Language (MM/EN)",
        'ask_pname': "Enter product name:",
        'ask_price': "Enter price (numbers only):",
        'ask_stock': "Enter stock quantity:",
        'ask_photo': "Send photo (or /skip):",
        'cart_added': "added to cart.",
        'checkout_name': "Enter your name:",
        'checkout_addr': "Enter address and phone:",
        'checkout_pay': "Total: {} MMK\nPlease send payment screenshot.",
        'stock_out': "❌ Out of stock.",
        'order_success': "✅ Order placed! Please wait for confirmation.",
        'confirm_btn': "✅ Confirm",
        'reject_btn': "❌ Reject",
        'bc_ask': "Send your broadcast message or photo.",
        'bc_done': "✅ Sent to {} customers."
    }
}

# ---------- DATABASE ENGINE ----------
def init_db():
    if not os.path.exists(PHOTOS_DIR): os.makedirs(PHOTOS_DIR)
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS shops (owner_id INTEGER PRIMARY KEY, shop_name TEXT, expire_date TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, price INTEGER, stock INTEGER, photo_id TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER, user_id INTEGER, name TEXT, addr TEXT, items TEXT, total INTEGER, pay_photo TEXT, status TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS settings (user_id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'my')")
    con.commit(); con.close()

def get_u_lang(uid):
    con = sqlite3.connect(DB_FILE)
    res = con.execute("SELECT lang FROM settings WHERE user_id=?", (uid,)).fetchone()
    con.close()
    return res[0] if res else 'my'

def t(uid, key): return TEXTS[get_u_lang(uid)].get(key, key)

# ---------- STATES ----------
(P_NAME, P_PRICE, P_STOCK, P_PHOTO) = range(4)
(O_NAME, O_ADDR, O_PAY) = range(4, 7)
(BC_MSG,) = range(7, 8)

# ---------- CORE FUNCTIONS ----------

async def get_main_kb(uid):
    lang = get_u_lang(uid)
    return ReplyKeyboardMarkup([
        [TEXTS[lang]['add_prod'], TEXTS[lang]['my_prods']],
        [TEXTS[lang]['orders'], TEXTS[lang]['broadcast']],
        [TEXTS[lang]['lang_btn']]
    ], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_db()
    
    # Deep Link for Customers
    if context.args:
        sid = int(context.args[0])
        context.user_data['sid'] = sid
        return await update.message.reply_text(f"{t(uid, 'welcome')}\n\n/shop - View Products\n/checkout - Order")

    shop = sqlite3.connect(DB_FILE).execute("SELECT shop_name FROM shops WHERE owner_id=?", (uid,)).fetchone()
    if shop:
        await update.message.reply_text(f"🏪 {shop[0]}", reply_markup=await get_main_kb(uid))
    else:
        await update.message.reply_text(t(uid, 'setup_help'), parse_mode="Markdown")

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("မြန်မာစာ 🇲🇲", callback_data="lang_my"), 
           InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")]]
    await update.message.reply_text("Choose Language:", reply_markup=InlineKeyboardMarkup(kb))

# --- Add Product Flow ---
async def add_p_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, 'ask_pname'), reply_markup=ReplyKeyboardRemove())
    return P_NAME

async def add_p_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pn'] = update.message.text
    await update.message.reply_text(t(update.effective_user.id, 'ask_price'))
    return P_PRICE

async def add_p_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pp'] = int(update.message.text)
    await update.message.reply_text(t(update.effective_user.id, 'ask_stock'))
    return P_STOCK

async def add_p_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ps'] = int(update.message.text)
    await update.message.reply_text(t(update.effective_user.id, 'ask_photo'))
    return P_PHOTO

async def add_p_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fid = update.message.photo[-1].file_id if update.message.photo else None
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT INTO products (owner_id, name, price, stock, photo_id) VALUES (?,?,?,?,?)",
                (uid, context.user_data['pn'], context.user_data['pp'], context.user_data['ps'], fid))
    con.commit(); con.close()
    await update.message.reply_text("✅ Success!", reply_markup=await get_main_kb(uid))
    return ConversationHandler.END

# --- Shopping Flow ---
async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get('sid')
    if not sid: return await update.message.reply_text("Please use shop link.")
    
    prods = sqlite3.connect(DB_FILE).execute("SELECT id, name, price, stock, photo_id FROM products WHERE owner_id=?", (sid,)).fetchall()
    for p in prods:
        kb = [[InlineKeyboardButton(f"🛒 Add to Cart ({p[3]} left)", callback_data=f"buy_{p[0]}")]]
        cap = f"📦 {p[1]}\n💰 {p[2]} MMK"
        if p[4]: await update.message.reply_photo(p[4], caption=cap, reply_markup=InlineKeyboardMarkup(kb))
        else: await update.message.reply_text(cap, reply_markup=InlineKeyboardMarkup(kb))

async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid = update.effective_user.id
    pid = int(query.data.split("_")[1])
    
    con = sqlite3.connect(DB_FILE)
    p = con.execute("SELECT name, price, stock FROM products WHERE id=?", (pid,)).fetchone(); con.close()
    
    if p[2] <= 0: return await context.bot.send_message(uid, t(uid, 'stock_out'))
    
    if 'cart' not in context.user_data: context.user_data['cart'] = []
    context.user_data['cart'].append({'id': pid, 'name': p[0], 'price': p[1]})
    await context.bot.send_message(uid, f"✅ {p[0]} {t(uid, 'cart_added')}")

# --- Checkout ---
async def check_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('cart'): return await update.message.reply_text("Cart is empty.")
    await update.message.reply_text(t(update.effective_user.id, 'checkout_name'), reply_markup=ReplyKeyboardRemove())
    return O_NAME

async def check_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['on'] = update.message.text
    await update.message.reply_text(t(update.effective_user.id, 'checkout_addr'))
    return O_ADDR

async def check_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['oa'] = update.message.text
    total = sum(i['price'] for i in context.user_data['cart'])
    context.user_data['ot'] = total
    await update.message.reply_text(t(update.effective_user.id, 'checkout_pay').format(total))
    return O_PAY

async def check_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; sid = context.user_data['sid']
    fid = update.message.photo[-1].file_id
    items = ", ".join([i['name'] for i in context.user_data['cart']])
    
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("INSERT INTO orders (shop_id, user_id, name, addr, items, total, pay_photo, status) VALUES (?,?,?,?,?,?,?,?)",
                (sid, uid, context.user_data['on'], context.user_data['oa'], items, context.user_data['ot'], fid, "Pending"))
    oid = cur.lastrowid; con.commit(); con.close()

    # Notify Shop Owner
    kb = [[InlineKeyboardButton(t(sid, 'confirm_btn'), callback_data=f"conf_{oid}"),
           InlineKeyboardButton(t(sid, 'reject_btn'), callback_data=f"rejc_{oid}")]]
    await context.bot.send_photo(sid, fid, caption=f"🔔 New Order #{oid}\nItems: {items}\nTotal: {context.user_data['ot']} MMK\nUser: {context.user_data['on']}", reply_markup=InlineKeyboardMarkup(kb))
    
    await update.message.reply_text(t(uid, 'order_success'))
    context.user_data['cart'] = []
    return ConversationHandler.END

# --- Callbacks (Lang, Confirm) ---
async def btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; data = query.data; await query.answer()
    
    if data.startswith("lang_"):
        lcode = data.split("_")[1]
        con = sqlite3.connect(DB_FILE); con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (update.effective_user.id, lcode)); con.commit(); con.close()
        await query.edit_message_text("Done!")
        await context.bot.send_message(update.effective_user.id, t(update.effective_user.id, 'main_menu'), reply_markup=await get_main_kb(update.effective_user.id))

    elif data.startswith("conf_") or data.startswith("rejc_"):
        action, oid = data.split("_")
        con = sqlite3.connect(DB_FILE); cur = con.cursor()
        order = cur.execute("SELECT user_id, items, shop_id FROM orders WHERE id=?", (oid,)).fetchone()
        
        if action == "conf":
            cur.execute("UPDATE orders SET status='Confirmed' WHERE id=?", (oid,))
            # Stock Deduction
            for item_name in order[1].split(", "):
                cur.execute("UPDATE products SET stock = MAX(0, stock - 1) WHERE name=? AND owner_id=?", (item_name, order[2]))
            await context.bot.send_message(order[0], f"✅ Your Order #{oid} is Confirmed!")
            await query.edit_message_caption("Status: Confirmed & Stock Deducted")
        else:
            cur.execute("UPDATE orders SET status='Rejected' WHERE id=?", (oid,))
            await context.bot.send_message(order[0], f"❌ Your Order #{oid} was Rejected.")
            await query.edit_message_caption("Status: Rejected")
        con.commit(); con.close()

# --- Broadcast ---
async def bc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, 'bc_ask'), reply_markup=ReplyKeyboardRemove())
    return BC_MSG

async def bc_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.effective_user.id
    con = sqlite3.connect(DB_FILE)
    customers = con.execute("SELECT DISTINCT user_id FROM orders WHERE shop_id=?", (sid,)).fetchall()
    con.close()
    
    count = 0
    for c in customers:
        try:
            if update.message.photo: await context.bot.send_photo(c[0], update.message.photo[-1].file_id, caption=update.message.caption)
            else: await context.bot.send_message(c[0], update.message.text)
            count += 1
        except: continue
        
    await update.message.reply_text(t(sid, 'bc_done').format(count), reply_markup=await get_main_kb(sid))
    return ConversationHandler.END

# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add Product Conv
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([TEXTS['my']['add_prod'], TEXTS['en']['add_prod']]), add_p_start)],
        states={ P_NAME: [MessageHandler(filters.TEXT, add_p_name)], P_PRICE: [MessageHandler(filters.TEXT, add_p_price)], 
                 P_STOCK: [MessageHandler(filters.TEXT, add_p_stock)], P_PHOTO: [MessageHandler(filters.PHOTO | filters.COMMAND, add_p_done)] },
        fallbacks=[]
    ))
    
    # Checkout Conv
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("checkout", check_start)],
        states={ O_NAME: [MessageHandler(filters.TEXT, check_name)], O_ADDR: [MessageHandler(filters.TEXT, check_addr)], O_PAY: [MessageHandler(filters.PHOTO, check_done)] },
        fallbacks=[]
    ))

    # Broadcast Conv
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([TEXTS['my']['broadcast'], TEXTS['en']['broadcast']]), bc_start)],
        states={ BC_MSG: [MessageHandler(filters.TEXT | filters.PHOTO, bc_send)] },
        fallbacks=[]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("shop", show_shop))
    app.add_handler(MessageHandler(filters.Text([TEXTS['my']['lang_btn'], TEXTS['en']['lang_btn']]), set_lang))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(btn_callback))
    
    # Setup Shop
    async def setup_s(u, c):
        name = " ".join(c.args)
        if not name: return await u.message.reply_text("/setup_shop <Name>")
        con = sqlite3.connect(DB_FILE); con.execute("INSERT OR REPLACE INTO shops VALUES (?,?,?)", (u.effective_user.id, name, "2026-12-31")); con.commit(); con.close()
        await u.message.reply_text("✅ Shop Created!", reply_markup=await get_main_kb(u.effective_user.id))
    app.add_handler(CommandHandler("setup_shop", setup_s))

    print("MarketLink Pro is LIVE..."); app.run_polling()

if __name__ == "__main__": main()
