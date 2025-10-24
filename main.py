import os
import time
import ccxt
import pandas as pd
import ta
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TESTNET = os.getenv("TESTNET", "False").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ORDER_SIZE_USDT = float(os.getenv("ORDER_SIZE_USDT", "5.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "25"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "6"))
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "0.5"))
MIN_BALANCE_USDT = float(os.getenv("MIN_BALANCE_USDT", "15.0"))

exchange = ccxt.bybit({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

if TESTNET:
    exchange.set_sandbox_mode(True)
    print("🔸 TESTNET режим увімкнено")
else:
    print("🔴 LIVE режим - реальна торгівля!")

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"{now()} ⚠️ Помилка відправки Telegram: {e}")

def get_balance():
    try:
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free'] if 'USDT' in balance else 0
        return float(usdt_balance)
    except Exception as e:
        print(f"{now()} ❌ Помилка отримання балансу: {e}")
        return 0

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def fetch_ohlcv(symbol, limit=50):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"{now()} ❌ Помилка отримання OHLCV для {symbol}: {e}")
        return None

def calculate_indicators(df):
    try:
        df['EMA20'] = ta.trend.ema_indicator(df['close'], window=20)
        df['EMA50'] = ta.trend.ema_indicator(df['close'], window=50)
        df['RSI'] = ta.momentum.rsi(df['close'], window=14)
        atr = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14)
        df['ATR'] = atr.average_true_range()
        return df
    except Exception as e:
        print(f"{now()} ❌ Помилка розрахунку індикаторів: {e}")
        return None

def get_open_positions():
    try:
        positions = exchange.fetch_positions()
        open_pos = []
        for p in positions:
            contracts = float(p.get('contracts', 0))
            size = float(p.get('size', 0))
            position_amt = float(p.get('positionAmt', 0))
            
            actual_size = contracts or size or position_amt
            
            if abs(actual_size) > 0:
                open_pos.append(p)
        return open_pos
    except Exception as e:
        print(f"{now()} ⚠️ Помилка отримання позицій: {e}")
        return []

def calculate_amount(price):
    return round((ORDER_SIZE_USDT * LEVERAGE) / price, 6)

def get_tick_size(symbol):
    try:
        market = exchange.market(symbol)
        if 'info' in market and 'priceFilter' in market['info']:
            tick_size = float(market['info']['priceFilter'].get('tickSize', 0.01))
            return tick_size
        if 'precision' in market and 'price' in market['precision']:
            decimals = market['precision']['price']
            return 10 ** (-decimals)
        return 0.01
    except:
        return 0.01

def round_to_tick(price, tick_size, round_up=True):
    if tick_size <= 0:
        tick_size = 0.01
    import math
    if round_up:
        return math.ceil(price / tick_size) * tick_size
    else:
        return math.floor(price / tick_size) * tick_size

def close_position(symbol, side):
    try:
        positions = exchange.fetch_positions([symbol])
        for pos in positions:
            contracts = float(pos.get('contracts', 0))
            size = float(pos.get('size', 0))
            position_amt = float(pos.get('positionAmt', 0))
            
            actual_size = contracts or size or position_amt
            
            if abs(actual_size) > 0:
                amount = abs(actual_size)
                close_side = 'sell' if side == "LONG" else 'buy'
                exchange.create_market_order(symbol, close_side, amount, {'reduceOnly': True})
                print(f"{now()} 🔄 Позицію {symbol} закрито через неможливість встановити TP/SL")
                return True
        return False
    except Exception as e:
        print(f"{now()} ❌ Помилка закриття позиції {symbol}: {e}")
        return False

def open_position(symbol, side, atr):
    order_opened = False
    try:
        market = exchange.market(symbol)
        ticker = exchange.fetch_ticker(symbol)
        price = float(ticker['last'])
        amount = calculate_amount(price)
        
        tick_size = get_tick_size(symbol)

        if pd.isna(atr) or atr <= 0:
            print(f"{now()} ⚠️ ATR недійсний для {symbol}, використовую мінімальний профіт")
            tp_percent = MIN_PROFIT_PERCENT
            sl_percent = 0.3
        else:
            tp_percent_atr = atr / price * 100 * 3
            sl_percent_atr = atr / price * 100 * 1.5
            
            if pd.isna(tp_percent_atr):
                tp_percent = MIN_PROFIT_PERCENT
            else:
                tp_percent = max(MIN_PROFIT_PERCENT, tp_percent_atr)
            
            if pd.isna(sl_percent_atr):
                sl_percent = 0.3
            else:
                sl_percent = max(0.3, sl_percent_atr)

        tp_price_raw = price * (1 + tp_percent/100) if side == "LONG" else price * (1 - tp_percent/100)
        sl_price_raw = price * (1 - sl_percent/100) if side == "LONG" else price * (1 + sl_percent/100)
        
        if side == "LONG":
            tp_price = round_to_tick(tp_price_raw, tick_size, round_up=True)
            sl_price = round_to_tick(sl_price_raw, tick_size, round_up=False)
        else:
            tp_price = round_to_tick(tp_price_raw, tick_size, round_up=False)
            sl_price = round_to_tick(sl_price_raw, tick_size, round_up=True)

        exchange.set_leverage(LEVERAGE, symbol)
        
        order = exchange.create_market_order(
            symbol, 
            'buy' if side == "LONG" else 'sell', 
            amount
        )
        order_opened = True
        
        print(f"{now()} 📊 Ордер відкрито: {side} {symbol}")
        print(f"    💰 Ціна входу: {price:.4f} USDT")
        print(f"    📈 Take Profit: {tp_price} USDT ({tp_percent:.2f}%)")
        print(f"    📉 Stop Loss: {sl_price} USDT ({sl_percent:.2f}%)")

        bybit_symbol = symbol.replace('/', '').replace(':USDT', '')
        
        params = {
            'category': 'linear',
            'symbol': bybit_symbol,
            'takeProfit': str(tp_price),
            'stopLoss': str(sl_price),
            'tpTriggerBy': 'LastPrice',
            'slTriggerBy': 'LastPrice',
            'positionIdx': 0
        }
        
        max_retries = 3
        tp_sl_success = False
        
        for attempt in range(max_retries):
            try:
                exchange.private_post_v5_position_trading_stop(params)
                print(f"{now()} ✅ TP/SL встановлено для {symbol}")
                tp_sl_success = True
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"{now()} ⚠️ Спроба {attempt + 1} встановлення TP/SL не вдалася, повтор...")
                    time.sleep(1)
                else:
                    print(f"{now()} ❌ КРИТИЧНА ПОМИЛКА: TP/SL не встановлено для {symbol} після {max_retries} спроб: {e}")
        
        if not tp_sl_success:
            print(f"{now()} 🚨 УВАГА: Закриваю позицію {symbol} через неможливість встановити TP/SL")
            close_position(symbol, side)
            send_telegram(f"⚠️ <b>Помилка відкриття позиції</b>\n\n"
                         f"Монета: {symbol}\n"
                         f"Причина: TP/SL не встановлено\n"
                         f"Позицію закрито автоматично")
            return False
        
        position_value = ORDER_SIZE_USDT * LEVERAGE
        profit_usdt = position_value * tp_percent / 100
        loss_usdt = position_value * sl_percent / 100
        
        telegram_message = (
            f"{'🟢' if side == 'LONG' else '🔴'} <b>Нова позиція відкрита!</b>\n\n"
            f"💰 <b>Монета:</b> {symbol}\n"
            f"📊 <b>Напрямок:</b> {side}\n"
            f"💵 <b>Ціна входу:</b> {price:.4f} USDT\n\n"
            f"📈 <b>Take Profit:</b> {tp_price} USDT (+{tp_percent:.2f}%)\n"
            f"💚 <b>Потенційний профіт:</b> ~{profit_usdt:.2f} USDT\n\n"
            f"📉 <b>Stop Loss:</b> {sl_price} USDT (-{sl_percent:.2f}%)\n"
            f"❌ <b>Максимальний збиток:</b> ~{loss_usdt:.2f} USDT\n\n"
            f"📊 <b>Розмір:</b> {amount} контрактів\n"
            f"⚡️ <b>Плече:</b> {LEVERAGE}x\n"
            f"💼 <b>Обсяг:</b> {position_value:.2f} USDT\n\n"
            f"🕐 {datetime.utcnow().strftime('%d.%m.%Y %H:%M:%S')} UTC"
        )
        
        send_telegram(telegram_message)
        
        return True
        
    except Exception as e:
        print(f"{now()} ❌ Помилка відкриття позиції {symbol}: {e}")
        if order_opened:
            print(f"{now()} 🚨 Спроба закрити позицію через помилку...")
            close_position(symbol, side)
        return False

def signal(df):
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        if pd.isna(last['EMA20']) or pd.isna(last['EMA50']) or pd.isna(last['RSI']):
            return None
        
        if last['EMA20'] > last['EMA50'] and last['RSI'] > 55 and last['RSI'] < 70:
            return "LONG"
        elif last['EMA20'] < last['EMA50'] and last['RSI'] < 45 and last['RSI'] > 30:
            return "SHORT"
        
        return None
    except Exception as e:
        print(f"{now()} ❌ Помилка генерації сигналу: {e}")
        return None

def main():
    print(f"\n{'='*60}")
    print(f"🤖 Bybit Trading Bot запущено о {now()}")
    print(f"{'='*60}")
    print(f"⚙️ Налаштування:")
    print(f"  • Режим: {'TESTNET' if TESTNET else 'LIVE'}")
    print(f"  • Розмір позиції: {ORDER_SIZE_USDT} USDT")
    print(f"  • Плече: {LEVERAGE}x")
    print(f"  • Макс. позицій: {MAX_POSITIONS}")
    print(f"  • Таймфрейм: {TIMEFRAME}")
    print(f"  • Мін. профіт: {MIN_PROFIT_PERCENT}%")
    print(f"  • Мін. баланс: {MIN_BALANCE_USDT} USDT")
    print(f"{'='*60}\n")
    
    if not API_KEY or not API_SECRET:
        print("❌ ПОМИЛКА: API_KEY та API_SECRET не встановлені!")
        print("📝 Створіть файл .env та додайте:")
        print("   API_KEY=ваш_ключ")
        print("   API_SECRET=ваш_секрет")
        return
    
    current_balance = get_balance()
    print(f"{now()} 💰 Поточний баланс: {current_balance:.2f} USDT")
    
    if current_balance < MIN_BALANCE_USDT:
        error_msg = f"❌ Недостатньо коштів! Баланс: {current_balance:.2f} USDT, потрібно мінімум: {MIN_BALANCE_USDT} USDT"
        print(error_msg)
        send_telegram(f"🚫 <b>Помилка запуску бота</b>\n\n{error_msg}")
        return
    
    try:
        markets = exchange.fetch_markets()
        symbols = [s['symbol'] for s in markets if s['quote'] == 'USDT' and s.get('type') == 'swap']
        print(f"{now()} 🔹 Знайдено {len(symbols)} торгових пар USDT")
        
        if len(symbols) == 0:
            print(f"{now()} ⚠️ Немає доступних торгових пар. Перевірте підключення до API.")
            return
            
    except Exception as e:
        print(f"{now()} ❌ Помилка отримання ринків: {e}")
        print("⚠️ Перевірте API ключі та підключення до інтернету")
        return
    
    startup_message = (
        f"✅ <b>Бот запущено успішно!</b>\n\n"
        f"{'🔸 Режим: TESTNET' if TESTNET else '🔴 Режим: LIVE'}\n"
        f"💰 Баланс: {current_balance:.2f} USDT\n"
        f"📊 Розмір позиції: {ORDER_SIZE_USDT} USDT\n"
        f"⚡️ Плече: {LEVERAGE}x\n"
        f"📈 Макс. позицій: {MAX_POSITIONS}\n"
        f"⏱ Таймфрейм: {TIMEFRAME}\n"
        f"💚 Мін. профіт: {MIN_PROFIT_PERCENT}%\n"
        f"🪙 Торгових пар: {len(symbols)}\n\n"
        f"🕐 {datetime.utcnow().strftime('%d.%m.%Y %H:%M:%S')} UTC"
    )
    send_telegram(startup_message)
    
    scan_count = 0
    last_balance_check = time.time()
    
    while True:
        try:
            scan_count += 1
            
            if time.time() - last_balance_check > 3600:
                current_balance = get_balance()
                print(f"{now()} 💰 Перевірка балансу: {current_balance:.2f} USDT")
                
                if current_balance < MIN_BALANCE_USDT:
                    warning_msg = f"⚠️ Низький баланс: {current_balance:.2f} USDT (мінімум: {MIN_BALANCE_USDT} USDT)"
                    print(f"{now()} {warning_msg}")
                    send_telegram(f"⚠️ <b>Попередження про баланс</b>\n\n{warning_msg}")
                
                last_balance_check = time.time()
            
            open_positions = get_open_positions()
            
            print(f"\n{now()} 🔍 Скан #{scan_count} | Позицій: {len(open_positions)}/{MAX_POSITIONS}")
            
            if len(open_positions) >= MAX_POSITIONS:
                print(f"{now()} ⏸️ Досягнуто максимум позицій ({MAX_POSITIONS})")
                time.sleep(60)
                continue
            
            positions_opened = 0
            
            for symbol in symbols:
                if len(open_positions) + positions_opened >= MAX_POSITIONS:
                    break
                    
                if any(p.get('symbol') == symbol for p in open_positions):
                    continue
                
                try:
                    df = fetch_ohlcv(symbol)
                    if df is None or len(df) < 50:
                        continue
                    
                    df = calculate_indicators(df)
                    if df is None:
                        continue
                    
                    sig = signal(df)
                    if sig:
                        print(f"\n{now()} 🎯 Сигнал {sig} для {symbol}")
                        if open_position(symbol, sig, df.iloc[-1]['ATR']):
                            positions_opened += 1
                            time.sleep(2)
                        
                except Exception as e:
                    continue
            
            if positions_opened > 0:
                print(f"\n{now()} ✨ Відкрито нових позицій: {positions_opened}")
            
            time.sleep(30)
            
        except KeyboardInterrupt:
            print(f"\n\n{now()} 🛑 Бот зупинено користувачем")
            break
        except Exception as e:
            print(f"{now()} ❌ Критична помилка: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
