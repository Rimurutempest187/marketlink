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
        'welcome': "MarketLink Pro မှ ကြိုဆိုပါတယ်။ 👋",
        'setup_help': "ဆိုင်ဖွင့်ရန်: `/setup_shop ဆိုင်အမည်`",
        'main_menu': "🏠 ပင်မမီနူး",
        'add_prod': "➕ ပစ္စည်းတင်ရန်",
        'my_prods': "📦 ပစ္စည်းစာရင်း",
        'orders': "🛒 အမှာစာများ",
        'stats': "📊 အရောင်းမှတ်တမ်း",
        'broadcast': "📢 ကြော်ငြာပို့ရန်",
        'lang_btn': "🌐 Language (မြန်မာ/EN)",
        'shop_link': "🔗 ဆိုင် Link ယူရန်",
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
        'expired': "❌ ဤဆိုင်သည် သက်တမ်းကုန်ဆုံးနေပါသည်။",
        'confirm_btn': "✅ အတည်ပြု",
        'reject_btn': "❌ ငြင်းပယ်",
        'bc_ask': "ပို့လိုသည့် ကြော်ငြာစာ (သို့မဟုတ်) ပုံကို ပို့ပေးပါ။",
        'bc_done': "✅ Customer {} ဦးထံ ပို့ပြီးပါပြီ။",
        'del_help': "ပစ္စည်းဖျက်ရန်: `/del <ID>`",
    },
    'en': {
        'welcome': "Welcome to MarketLink Pro. 👋",
        'setup_help': "To setup: `/setup_shop ShopName`",
        'main_menu': "🏠 Main Menu",
        'add_prod': "➕ Add Product",
        'my_prods': "📦 My Products",
        'orders': "🛒 Orders",
        'stats': "📊 Sales Stats",
        'broadcast': "📢 Broadcast",
        'lang_btn': "🌐 Language (MM/EN)",
        'shop_link': "🔗 Get Shop Link",
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
        'expired': "❌ This shop has expired.",
        'confirm_btn': "✅ Confirm",
        'reject_btn': "❌ Reject",
        'bc_ask': "Send your broadcast message or photo.",
        'bc_done': "✅ Sent to {} customers.",
        'del_help': "Delete product: `/del <ID>`",
    }
}

# ---------- DATABASE ENGINE ----------
def init_db():
    if not os.path.exists(PHOTOS_DIR): os.makedirs(PHOTOS_DIR)
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS shops (owner_id INTEGER PRIMARY KEY, shop_name TEXT, expire_date TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, name TEXT, price INTEGER, stock INTEGER, photo_id TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER, user_id INTEGER, name TEXT, addr TEXT, items TEXT, total INTEGER, pay_photo TEXT, status TEXT, date TEXT)")
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

# ---------- KEYBOARDS ----------
async def get_main_kb(uid):
    lang = get_u_lang(uid)
    return ReplyKeyboardMarkup([
        [TEXTS[lang]['add_prod'], TEXTS[lang]['my_prods']],
        [TEXTS[lang]['orders'], TEXTS[lang]['stats']],
        [TEXTS[lang]['shop_link'], TEXTS[lang]['broadcast']],
        [TEXTS[lang]['lang_btn']]
    ], resize_keyboard=True)

# ---------- COMMAND HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    init_db()
    
    if context.args: # Deep Link for Customer
        sid = int(context.args[0])
        con = sqlite3.connect(DB_FILE)
        shop = con.execute("SELECT shop_name, expire_date FROM shops WHERE owner_id=?", (sid,)).fetchone()
        con.close()
        
        if not shop: return await update.message.reply_text("Shop not found.")
        if datetime.now() > datetime.strptime(shop[1], "%Y-%m-%d") and sid != ADMIN_ID:
            return await update.message.reply_text(t(uid, 'expired'))

        context.user_data['sid'] = sid
        return await update.message.reply_text(f"🏪 **{shop[0]}**\n\n/shop - View Catalog\n/checkout - Buy Items\n/my_orders - History", parse_mode="Markdown")

    shop = sqlite3.connect(DB_FILE).execute("SELECT shop_name FROM shops WHERE owner_id=?", (uid,)).fetchone()
    if shop:
        await update.message.reply_text(t(uid, 'welcome'), reply_markup=await get_main_kb(uid))
    else:
        await update.message.reply_text(t(uid, 'setup_help'), parse_mode="Markdown")

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("မြန်မာစာ 🇲🇲", callback_data="lang_my"), 
           InlineKeyboardButton("English 🇺🇸", callback_data="lang_en")]]
    await update.message.reply_text("Choose Language:", reply_markup=InlineKeyboardMarkup(kb))

# --- Product Management ---
async def start_add_p(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, 'ask_pname'), reply_markup=ReplyKeyboardRemove())
    return P_NAME

async def p_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pn'] = update.message.text
    await update.message.reply_text(t(update.effective_user.id, 'ask_price'))
    return P_PRICE

async def p_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pp'] = int(update.message.text)
    await update.message.reply_text(t(update.effective_user.id, 'ask_stock'))
    return P_STOCK

async def p_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['ps'] = int(update.message.text)
    await update.message.reply_text(t(update.effective_user.id, 'ask_photo'))
    return P_PHOTO

async def p_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    fid = update.message.photo[-1].file_id if update.message.photo else None
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT INTO products (owner_id, name, price, stock, photo_id) VALUES (?,?,?,?,?)",
                (uid, context.user_data['pn'], context.user_data['pp'], context.user_data['ps'], fid))
    con.commit(); con.close()
    await update.message.reply_text("✅ Success!", reply_markup=await get_main_kb(uid))
    return ConversationHandler.END

async def list_prods(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    prods = sqlite3.connect(DB_FILE).execute("SELECT id, name, price, stock FROM products WHERE owner_id=?", (uid,)).fetchall()
    if not prods: return await update.message.reply_text("No products.")
    msg = "📦 **Product List:**\n\n" + "\n".join([f"ID: `{p[0]}` | {p[1]} | {p[2]} MMK ({p[3]})" for p in prods])
    msg += f"\n\n{t(uid, 'del_help')}"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def del_prod(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("Usage: `/del ID`")
    con = sqlite3.connect(DB_FILE); con.execute("DELETE FROM products WHERE id=? AND owner_id=?", (context.args[0], update.effective_user.id))
    con.commit(); con.close(); await update.message.reply_text("✅ Deleted.")

# --- Shop Catalog & Order ---
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
    q = update.callback_query; await q.answer(); uid = update.effective_user.id
    pid = int(q.data.split("_")[1])
    p = sqlite3.connect(DB_FILE).execute("SELECT name, price, stock FROM products WHERE id=?", (pid,)).fetchone()
    if p[2] <= 0: return await context.bot.send_message(uid, t(uid, 'stock_out'))
    if 'cart' not in context.user_data: context.user_data['cart'] = []
    context.user_data['cart'].append({'id': pid, 'name': p[0], 'price': p[1]})
    await context.bot.send_message(uid, f"✅ {p[0]} {t(uid, 'cart_added')}")

# --- Checkout Conversation ---
async def checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('cart'): return await update.message.reply_text("Cart empty.")
    await update.message.reply_text(t(update.effective_user.id, 'checkout_name'), reply_markup=ReplyKeyboardRemove())
    return O_NAME

async def o_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['on'] = update.message.text
    await update.message.reply_text(t(update.effective_user.id, 'checkout_addr'))
    return O_ADDR

async def o_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['oa'] = update.message.text
    total = sum(i['price'] for i in context.user_data['cart'])
    context.user_data['ot'] = total
    await update.message.reply_text(t(update.effective_user.id, 'checkout_pay').format(total))
    return O_PAY

async def o_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; sid = context.user_data['sid']
    fid = update.message.photo[-1].file_id
    items = ", ".join([i['name'] for i in context.user_data['cart']])
    con = sqlite3.connect(DB_FILE); cur = con.cursor()
    cur.execute("INSERT INTO orders (shop_id, user_id, name, addr, items, total, pay_photo, status, date) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, uid, context.user_data['on'], context.user_data['oa'], items, context.user_data['ot'], fid, "Pending", datetime.now().strftime("%Y-%m-%d")))
    oid = cur.lastrowid; con.commit(); con.close()
    
    kb = [[InlineKeyboardButton(t(sid, 'confirm_btn'), callback_data=f"conf_{oid}"),
           InlineKeyboardButton(t(sid, 'reject_btn'), callback_data=f"rejc_{oid}")]]
    await context.bot.send_photo(sid, fid, caption=f"🔔 Order #{oid}\nItems: {items}\nTotal: {context.user_data['ot']} MMK", reply_markup=InlineKeyboardMarkup(kb))
    await update.message.reply_text(t(uid, 'order_success'))
    context.user_data['cart'] = []
    return ConversationHandler.END

# --- Callback & Broadcast ---
async def btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; await q.answer()
    if data.startswith("lang_"):
        lcode = data.split("_")[1]
        con = sqlite3.connect(DB_FILE); con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (update.effective_user.id, lcode)); con.commit(); con.close()
        await q.edit_message_text("Updated!")
        await context.bot.send_message(update.effective_user.id, t(update.effective_user.id, 'main_menu'), reply_markup=await get_main_kb(update.effective_user.id))
    elif data.startswith("conf_") or data.startswith("rejc_"):
        action, oid = data.split("_")
        con = sqlite3.connect(DB_FILE); cur = con.cursor()
        order = cur.execute("SELECT user_id, items, shop_id FROM orders WHERE id=?", (oid,)).fetchone()
        if action == "conf":
            cur.execute("UPDATE orders SET status='Confirmed' WHERE id=?", (oid,))
            for item in order[1].split(", "):
                cur.execute("UPDATE products SET stock = MAX(0, stock-1) WHERE name=? AND owner_id=?", (item, order[2]))
            await context.bot.send_message(order[0], f"✅ Order #{oid} Confirmed!")
        else: cur.execute("UPDATE orders SET status='Rejected' WHERE id=?", (oid,))
        con.commit(); con.close(); await q.edit_message_caption(f"Status: {action}")

# --- Features (Stats, Broadcast, Link) ---
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    con = sqlite3.connect(DB_FILE)
    sales = con.execute("SELECT SUM(total) FROM orders WHERE shop_id=? AND status='Confirmed'", (uid,)).fetchone()[0] or 0
    cnt = con.execute("SELECT COUNT(*) FROM orders WHERE shop_id=?", (uid,)).fetchone()[0]
    con.close(); await update.message.reply_text(f"📊 **Total Sales:** {sales:,} MMK\n📦 **Orders:** {cnt}")

async def bc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_user.id, 'bc_ask'), reply_markup=ReplyKeyboardRemove())
    return BC_MSG

async def bc_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = update.effective_user.id
    con = sqlite3.connect(DB_FILE); custs = con.execute("SELECT DISTINCT user_id FROM orders WHERE shop_id=?", (sid,)).fetchall(); con.close()
    count = 0
    for c in custs:
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
    
    add_p_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text([TEXTS['my']['add_prod'], TEXTS['en']['add_prod']]), start_add_p)],
        states={P_NAME:[MessageHandler(filters.TEXT, p_name)], P_PRICE:[MessageHandler(filters.TEXT, p_price)],
                P_STOCK:[MessageHandler(filters.TEXT, p_stock)], P_PHOTO:[MessageHandler(filters.PHOTO | filters.COMMAND, p_photo)]},
        fallbacks=[]
    )
    checkout_conv = ConversationHandler(
        entry_points=[CommandHandler("checkout", checkout)],
        states={O_NAME:[MessageHandler(filters.TEXT, o_name)], O_ADDR:[MessageHandler(filters.TEXT, o_addr)], O_PAY:[MessageHandler(filters.PHOTO, o_pay)]},
        fallbacks=[]
    )
    bc_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text([TEXTS['my']['broadcast'], TEXTS['en']['broadcast']]), bc_start)],
        states={BC_MSG:[MessageHandler(filters.TEXT | filters.PHOTO, bc_send)]},
        fallbacks=[]
    )
    
    app.add_handlers([add_p_conv, checkout_conv, bc_conv])
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("shop", show_shop))
    app.add_handler(CommandHandler("del", del_prod))
    app.add_handler(CommandHandler("my_orders", lambda u,c: u.message.reply_text("History: /my_orders is coming soon!")))
    app.add_handler(MessageHandler(filters.Text([TEXTS['my']['stats'], TEXTS['en']['stats']]), show_stats))
    app.add_handler(MessageHandler(filters.Text([TEXTS['my']['my_prods'], TEXTS['en']['my_prods']]), list_prods))
    app.add_handler(MessageHandler(filters.Text([TEXTS['my']['lang_btn'], TEXTS['en']['lang_btn']]), set_lang))
    app.add_handler(MessageHandler(filters.Text([TEXTS['my']['shop_link'], TEXTS['en']['shop_link']]), lambda u,c: u.message.reply_text(f"Link: `https://t.me/{(c.bot.get_me()).username}?start={u.effective_user.id}`", parse_mode="Markdown")))
    app.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(btn_callback))
    
    async def setup_s(u, c):
        name = " ".join(c.args)
        if not name: return await u.message.reply_text("/setup_shop <Name>")
        con = sqlite3.connect(DB_FILE); con.execute("INSERT OR REPLACE INTO shops VALUES (?,?,?)", (u.effective_user.id, name, "2026-12-31")); con.commit(); con.close()
        await u.message.reply_text("✅ Shop Created!", reply_markup=await get_main_kb(u.effective_user.id))
    app.add_handler(CommandHandler("setup_shop", setup_s))

    print("MarketLink Pro is LIVE..."); app.run_polling()

if __name__ == "__main__": main()
