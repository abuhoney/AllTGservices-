import os
import json
import random
import string
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict

# مكتبات تليجرام
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, PreCheckoutQueryHandler

# مكتبات الويب (Flask)
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ================= إعدادات المشروع =================
BOT_TOKEN = "7717910691:AAEeQ0364UADOtvHcCdjQ6cdu89DfqtP6XA"
PAYMENT_PROVIDER_TOKEN = "6073714100:TEST:TG_9Q2JIpZZk6ASC41JYsPcQJwA"
ADMIN_IDS = [5652667245]

# رابط الموقع الجديد الخاص بك
SITE_URL = "https://alltgservices-08m9.onrender.com/index.html?startapp="

# القناة الإلزامية (اتركها فارغة "" إذا كنت لا تريد فرض الاشتراك)
REQUIRED_CHANNEL = ""  

# تكاليف النظام
COST_PER_ITEM = 1
SHARE_POINTS = 600
DAILY_POINTS = 100

# منفذ السيرفر (يتم تحديده تلقائياً في Render)
PORT = int(os.environ.get("PORT", 5000))

# إعدادات اللوج
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)
logger = logging.getLogger(__name__)

# ================= قاعدة البيانات (Database) =================
class Database:
    def __init__(self, filename: str = "users.json"):
        self.filename = filename
        self.referrals_file = "referrals.json"
        self._ensure_files_exist()
    
    def _ensure_files_exist(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', encoding='utf-8') as f:
                json.dump({}, f)
        if not os.path.exists(self.referrals_file):
            with open(self.referrals_file, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def _read_data(self, filepath) -> Dict:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_data(self, filepath, data: Dict):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def get_user(self, user_id: int) -> Dict:
        data = self._read_data(self.filename)
        user_str = str(user_id)
        if user_str not in data:
            # إنشاء مستخدم جديد برصيد افتراضي
            data[user_str] = {
                'points': 200, 
                'created_at': str(datetime.now().date()), 
                'last_daily': None, 
                'invited_by': None
            }
            self._write_data(self.filename, data)
        return data[user_str]

    def set_user_points(self, user_id: int, points: int):
        data = self._read_data(self.filename)
        user_str = str(user_id)
        if user_str not in data: data[user_str] = {}
        data[user_str]['points'] = points
        self._write_data(self.filename, data)

    def deduct_points(self, user_id: int, points: int) -> bool:
        user = self.get_user(user_id)
        current = user.get('points', 0)
        if current < points: return False
        self.set_user_points(user_id, current - points)
        return True

    def can_claim_daily(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        last = user.get('last_daily')
        if not last: return True
        try:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
            return last_date < datetime.now().date()
        except: return True

    def claim_daily(self, user_id: int) -> bool:
        if not self.can_claim_daily(user_id): return False
        user = self.get_user(user_id)
        self.set_user_points(user_id, user['points'] + DAILY_POINTS)
        
        data = self._read_data(self.filename)
        data[str(user_id)]['last_daily'] = str(datetime.now().date())
        self._write_data(self.filename, data)
        return True

    def process_referral(self, referrer_id: int, new_user_id: int) -> bool:
        ref_data = self._read_data(self.referrals_file)
        referrer_str = str(referrer_id)
        
        if referrer_str not in ref_data:
            ref_data[referrer_str] = []
        
        if new_user_id not in ref_data[referrer_str]:
            ref_data[referrer_str].append(new_user_id)
            self._write_data(self.referrals_file, ref_data)
            
            user_data = self._read_data(self.filename)
            if str(new_user_id) not in user_data: user_data[str(new_user_id)] = {}
            user_data[str(new_user_id)]['invited_by'] = referrer_id
            self._write_data(self.filename, user_data)
            
            self.set_user_points(referrer_id, self.get_user(referrer_id)['points'] + SHARE_POINTS)
            self.set_user_points(new_user_id, self.get_user(new_user_id)['points'] + SHARE_POINTS)
            return True
        return False

# تهيئة قاعدة البيانات
db = Database()

# ================= المولدات (Generators) =================
COUNTRIES_DATA = {
    "967": {"name": "Yemen", "prefixes": ["77", "78", "71", "73", "70"], "length": 9},
    "966": {"name": "Saudi Arabia", "prefixes": ["50", "51", "52", "53", "54", "55", "56", "57", "58", "59"], "length": 9},
    "971": {"name": "UAE", "prefixes": ["50", "51", "52", "53", "54", "55", "56", "57", "58", "59"], "length": 9},
    "20": {"name": "Egypt", "prefixes": ["10", "11", "12", "15"], "length": 10},
    "962": {"name": "Jordan", "prefixes": ["77", "78", "79"], "length": 9},
    "973": {"name": "Bahrain", "prefixes": ["3", "6", "7"], "length": 8},
    "974": {"name": "Qatar", "prefixes": ["3", "5", "6", "7"], "length": 8},
    "965": {"name": "Kuwait", "prefixes": ["5", "6", "9"], "length": 8},
    "968": {"name": "Oman", "prefixes": ["7", "9"], "length": 8}
}

class Generators:
    @staticmethod
    def generate_phones(country_code: str, count: int) -> list:
        if country_code == "random":
            codes = list(COUNTRIES_DATA.keys())
            results = []
            for _ in range(count):
                code = random.choice(codes)
                country = COUNTRIES_DATA[code]
                prefix = random.choice(country["prefixes"])
                remaining = country["length"] - len(prefix)
                number = prefix + ''.join([str(random.randint(0, 9)) for _ in range(remaining)])
                results.append(f"+{code}{number}")
            return results
            
        if country_code not in COUNTRIES_DATA: return []
        country = COUNTRIES_DATA[country_code]
        phones = []
        for _ in range(count):
            prefix = random.choice(country["prefixes"])
            remaining = country["length"] - len(prefix)
            number = prefix + ''.join([str(random.randint(0, 9)) for _ in range(remaining)])
            phones.append(f"+{country_code}{number}")
        return phones

    @staticmethod
    def generate_emails(domain: str, count: int) -> list:
        emails = []
        names = ["ahmed", "mohamed", "ali", "sara", "fatima", "user", "test", "admin"]
        for i in range(count):
            name = random.choice(names)
            rand_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(3, 6)))
            emails.append(f"{name}{rand_str}@{domain}")
        return emails

    @staticmethod
    def generate_usernames(pattern: str, count: int) -> list:
        usernames = set()
        attempts = 0
        max_attempts = count * 10
        
        while len(usernames) < count and attempts < max_attempts:
            attempts += 1
            username = ''
            for char in pattern:
                if char == '$':
                    if len(username) == 0:
                        username += random.choice(string.ascii_lowercase)
                    else:
                        username += random.choice(string.ascii_lowercase + string.digits + '_')
                else:
                    username += char
            
            if len(username) >= 5 and username[0] not in '_0123456789' and '__' not in username and username[-1] != '_':
                usernames.add(username)
        
        return list(usernames)

# ================= إعداد Flask API (للموقع) =================
app = Flask(__name__)
CORS(app) # للسماح للموقع بالتحدث مع البوت

@app.route('/')
def index():
    return "Bot Server is Running!"

# API للحصول على بيانات المستخدم والنقاط
@app.route('/api/user', methods=['POST'])
def get_user_api():
    data = request.json
    user_id = data.get('user_id')
    if not user_id: return jsonify({'error': 'No user_id'}), 400
    
    user_data = db.get_user(user_id)
    return jsonify({
        'points': user_data.get('points', 0),
        'can_claim_daily': db.can_claim_daily(user_id)
    })

# API لتوليد الأرقام
@app.route('/api/generate/phones', methods=['POST'])
def api_phones():
    data = request.json
    user_id = data.get('user_id')
    count = data.get('count', 10)
    mode = data.get('mode', 'specific')
    code = data.get('code', '966')

    cost = count * COST_PER_ITEM
    
    # فحص النقاط
    if not db.deduct_points(user_id, cost):
        return jsonify({'error': 'Insufficient points', 'current': db.get_user(user_id)['points']}), 400

    # التوليد
    results = Generators.generate_phones(code if mode == 'specific' else 'random', count)
    return jsonify({'status': 'success', 'data': results})

# API لتوليد الإيميلات
@app.route('/api/generate/emails', methods=['POST'])
def api_emails():
    data = request.json
    user_id = data.get('user_id')
    count = data.get('count', 10)
    domain = data.get('domain', 'gmail.com')

    cost = count * COST_PER_ITEM

    if not db.deduct_points(user_id, cost):
        return jsonify({'error': 'Insufficient points'}), 400

    results = Generators.generate_emails(domain, count)
    return jsonify({'status': 'success', 'data': results})

# API لتوليد اليوزرات
@app.route('/api/generate/usernames', methods=['POST'])
def api_usernames():
    data = request.json
    user_id = data.get('user_id')
    count = data.get('count', 10)
    pattern = data.get('pattern', 'user$')

    cost = count * COST_PER_ITEM

    if not db.deduct_points(user_id, cost):
        return jsonify({'error': 'Insufficient points'}), 400

    results = Generators.generate_usernames(pattern, count)
    
    # حساب الاسترجاع (Refund) إذا كانت النتائج أقل من المطلوب
    refund = (count - len(results)) * COST_PER_ITEM
    if refund > 0:
        db.set_user_points(user_id, db.get_user(user_id)['points'] + refund)

    return jsonify({'status': 'success', 'data': results, 'refunded': refund})

# API لاستلام الهدية اليومية
@app.route('/api/claim_daily', methods=['POST'])
def api_claim_daily():
    data = request.json
    user_id = data.get('user_id')
    
    if db.claim_daily(user_id):
        return jsonify({'status': 'success', 'points': DAILY_POINTS})
    else:
        return jsonify({'error': 'Already claimed today'}), 400

# دالة لتشغيل السيرفر في خيط منفصل (Thread)
def run_flask():
    # تشغيل على المنفذ المحدد وعلى جميع الشبكات (0.0.0.0)
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ================= منطق البوت (Telegram Logic) =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # معالجة الإحالة
    args = context.args
    if args and str(args[0]).isdigit():
        referrer = int(args[0])
        if referrer != user_id:
            is_new = db.process_referral(referrer, user_id)
            if is_new:
                try:
                    await context.bot.send_message(referrer, f"🎉 تم دعوة مستخدم جديد!\nحصلت على {SHARE_POINTS} نقطة.")
                except: pass

    # التحقق من الاشتراك (فقط إذا تم تحديد قناة)
    if REQUIRED_CHANNEL:
        try:
            member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
            if member.status in ['left', 'kicked']:
                await update.message.reply_text(f"⛔ يجب الاشتراك في القناة أولاً:\n{REQUIRED_CHANNEL}")
                return
        except Exception as e:
            logger.error(f"Sub check error: {e}")
            # في حالة الخطأ (مثل أن البوت ليس مشرفاً)، نسمح بالدخول لتجنب تعطل البوت
            pass

    # إرسال رسالة ترحيبية تتضمن زر Web App
    keyboard = [[InlineKeyboardButton("🚀 فتح التطبيق", web_app={'url': f'{SITE_URL}{user_id}'})]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "مرحباً بك في بوت التوليد الشامل 🤖\n\nاختر من القائمة للبدء:",
        reply_markup=reply_markup
    )

def main():
    # 1. تشغيل سيرفر Flask في خيط منفصل
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print(f"🚀 Flask Server started on port {PORT}")

    # 2. تشغيل بوت تليجرام
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    
    print("🤖 Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()