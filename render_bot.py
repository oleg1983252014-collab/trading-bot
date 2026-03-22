import requests
import schedule
import time
import threading
from datetime import datetime
from flask import Flask

# =============================
# 🔧 НАЛАШТУВАННЯ — ЗМІН ЦЕ!
# =============================
TELEGRAM_TOKEN = "8298501004:AAFWwY2sikUS87MMZz2kHCOPoKQiVn2X18E"
CHAT_ID = "7553475512"
TWELVE_API_KEY = "99b3ca01dbdf45ccb2f5968b16af1c82"

# =============================
# 📋 ВАЛЮТНІ ПАРИ
# =============================
PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
    "AUD/USD", "USD/CAD", "NZD/USD",
    "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "BTC/USD", "ETH/USD", "BNB/USD",
    "SOL/USD", "XRP/USD", "DOGE/USD",
    "XAU/USD", "XAG/USD",
]

# =============================
# 🌐 FLASK (щоб Render не вимикав)
# =============================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Бот працює!", 200

@app.route("/health")
def health():
    return "OK", 200

# =============================
# 📡 ОТРИМАННЯ ДАНИХ
# =============================
def get_candles(symbol, interval="15min", outputsize=50):
    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": interval,
            "outputsize": outputsize,
            "apikey": TWELVE_API_KEY,
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get("status") == "error":
            return None

        values = data.get("values", [])
        if not values:
            return None

        return [float(v["close"]) for v in reversed(values)]

    except Exception as e:
        print(f"❌ Помилка {symbol}: {e}")
        return None

# =============================
# 📊 ТЕХНІЧНИЙ АНАЛІЗ
# =============================
def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, period + 1):
        change = prices[-i] - prices[-i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def calculate_ma(prices, period):
    if len(prices) < period:
        return prices[-1]
    return sum(prices[-period:]) / period

def get_signal(prices):
    rsi = calculate_rsi(prices)
    ma5 = calculate_ma(prices, 5)
    ma10 = calculate_ma(prices, 10)
    ma20 = calculate_ma(prices, 20)
    price = prices[-1]

    score = 0
    if rsi < 30:
        score += 2
    elif rsi < 40:
        score += 1
    elif rsi > 70:
        score -= 2
    elif rsi > 60:
        score -= 1

    if ma5 > ma10 > ma20:
        score += 2
    elif ma5 > ma20:
        score += 1
    elif ma5 < ma10 < ma20:
        score -= 2
    elif ma5 < ma20:
        score -= 1

    if price > ma20:
        score += 1
    else:
        score -= 1

    if score >= 3:
        return "⬆️ UP", "🟢 Сильний", rsi, price
    elif score >= 1:
        return "⬆️ UP", "🟡 Слабкий", rsi, price
    elif score <= -3:
        return "⬇️ DOWN", "🔴 Сильний", rsi, price
    elif score <= -1:
        return "⬇️ DOWN", "🟡 Слабкий", rsi, price
    else:
        return "↔️", "⚪ Нейтрально", rsi, price

# =============================
# 📨 TELEGRAM
# =============================
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"❌ Telegram: {e}")

# =============================
# 🚀 ВІДПРАВКА СИГНАЛІВ
# =============================
def send_signals():
    now = datetime.now().strftime("%H:%M %d.%m.%Y")
    print(f"📊 Збираю сигнали... {now}")

    buy_signals = []
    sell_signals = []
    neutral_count = 0

    for pair in PAIRS:
        prices = get_candles(pair)
        time.sleep(0.5)

        if not prices or len(prices) < 20:
            continue

        signal, strength, rsi, price = get_signal(prices)

        if "UP" in signal:
            buy_signals.append((pair, strength, rsi, price))
        elif "DOWN" in signal:
            sell_signals.append((pair, strength, rsi, price))
        else:
            neutral_count += 1

    msg = f"📊 <b>СИГНАЛИ РИНКУ</b>\n"
    msg += f"🕐 {now}\n"
    msg += f"━━━━━━━━━━━━━━━\n\n"

    if buy_signals:
        msg += f"⬆️ <b>КУПИТИ — {len(buy_signals)}</b>\n"
        for pair, strength, rsi, price in buy_signals:
            msg += f"💱 <b>{pair}</b> | {strength}\n"
            msg += f"   💰 {price:.5f} | RSI: {rsi}\n"
        msg += "\n"

    if sell_signals:
        msg += f"⬇️ <b>ПРОДАТИ — {len(sell_signals)}</b>\n"
        for pair, strength, rsi, price in sell_signals:
            msg += f"💱 <b>{pair}</b> | {strength}\n"
            msg += f"   💰 {price:.5f} | RSI: {rsi}\n"
        msg += "\n"

    if not buy_signals and not sell_signals:
        msg += "↔️ Немає чітких сигналів\n\n"

    msg += f"⚪ Нейтральних: {neutral_count}\n"
    msg += f"━━━━━━━━━━━━━━━\n"
    msg += f"⚠️ <i>Не фінансова порада!</i>"

    send_telegram(msg)
    print(f"✅ Надіслано! UP:{len(buy_signals)} DOWN:{len(sell_signals)}")

# =============================
# ⏰ ПЛАНУВАЛЬНИК
# =============================
def run_scheduler():
    send_signals()
    schedule.every(15).minutes.do(send_signals)
    while True:
        schedule.run_pending()
        time.sleep(30)

# =============================
# 🏁 СТАРТ
# =============================
if __name__ == "__main__":
    print("🤖 Бот запускається на Render...")
    # Запускаємо бота в окремому потоці
    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()
    # Flask тримає сервер живим
    app.run(host="0.0.0.0", port=10000)
