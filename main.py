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

ORDER_SIZE_USDT = float(os.getenv("ORDER_SIZE_USDT", "10.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "15"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "5"))
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
MIN_PROFIT_PERCENT = float(os.getenv("MIN_PROFIT_PERCENT", "0.3"))
MIN_BALANCE_USDT = float(os.getenv("MIN_BALANCE_USDT", "10.0"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "120"))

if TIMEFRAME == "1m":
    ATR_WINDOW = 7
    RSI_WINDOW = 3
    HISTORY_LIMIT = 150
elif TIMEFRAME == "3m":
    ATR_WINDOW = 10
    RSI_WINDOW = 4
    HISTORY_LIMIT = 200
else:
    ATR_WINDOW = 14
    RSI_WINDOW = 5
    HISTORY_LIMIT = 250

pnl_stats = {
    "total_trades": 0,
    "winning_trades": 0,
    "losing_trades": 0,
    "total_pnl": 0.0,
    "biggest_win": 0.0,
    "biggest_loss": 0.0
}

last_entry_time = {}

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

def fetch_ohlcv(symbol, limit=None):
    try:
        if limit is None:
            limit = HISTORY_LIMIT
        bars = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"{now()} ❌ Помилка отримання OHLCV для {symbol}: {e}")
        return None

def calculate_indicators(df):
    try:
        df['EMA9'] = ta.trend.ema_indicator(df['close'], window=9)
        df['EMA21'] = ta.trend.ema_indicator(df['close'], window=21)
        df['EMA200'] = ta.trend.ema_indicator(df['close'], window=200)
        df['RSI'] = ta.momentum.rsi(df['close'], window=RSI_WINDOW)
        atr = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=ATR_WINDOW)
        df['ATR'] = atr.average_true_range()
        df['volume_ema'] = df['volume'].ewm(span=20).mean()
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

def update_pnl_stats(pnl, trade_type="manual"):
    global pnl_stats
    pnl_stats["total_trades"] += 1
    pnl_stats["total_pnl"] += pnl
    
    if pnl > 0:
        pnl_stats["winning_trades"] += 1
        if pnl > pnl_stats["biggest_win"]:
            pnl_stats["biggest_win"] = pnl
    else:
        pnl_stats["losing_trades"] += 1
        if pnl < pnl_stats["biggest_loss"]:
            pnl_stats["biggest_loss"] = pnl
    
    winrate = (pnl_stats["winning_trades"] / pnl_stats["total_trades"] * 100) if pnl_stats["total_trades"] > 0 else 0
    print(f"{now()} 📊 PnL: {pnl:+.2f} USDT | Total: {pnl_stats['total_pnl']:+.2f} USDT | Winrate: {winrate:.1f}% ({pnl_stats['winning_trades']}/{pnl_stats['total_trades']})")

def print_pnl_stats():
    if pnl_stats["total_trades"] == 0:
        return
    winrate = pnl_stats["winning_trades"] / pnl_stats["total_trades"] * 100
    msg = (
        f"\n{'='*60}\n"
        f"📊 PnL СТАТИСТИКА:\n"
        f"{'='*60}\n"
        f"Всього угод: {pnl_stats['total_trades']}\n"
        f"Прибуткових: {pnl_stats['winning_trades']} | Збиткових: {pnl_stats['losing_trades']}\n"
        f"Winrate: {winrate:.2f}%\n"
        f"Загальний PnL: {pnl_stats['total_pnl']:+.2f} USDT\n"
        f"Найбільший профіт: +{pnl_stats['biggest_win']:.2f} USDT\n"
        f"Найбільший збиток: {pnl_stats['biggest_loss']:.2f} USDT\n"
        f"{'='*60}\n"
    )
    print(msg)
    send_telegram(msg.replace('=', '─'))

def close_position(symbol, side, reason="manual", entry_price=None):
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
                
                ticker = exchange.fetch_ticker(symbol)
                exit_price = float(ticker['last'])
                
                if entry_price:
                    if side == "LONG":
                        pnl = (exit_price - entry_price) / entry_price * 100 * ORDER_SIZE_USDT * LEVERAGE
                    else:
                        pnl = (entry_price - exit_price) / entry_price * 100 * ORDER_SIZE_USDT * LEVERAGE
                    update_pnl_stats(pnl)
                
                exchange.create_market_order(symbol, close_side, amount, {'reduceOnly': True})
                print(f"{now()} 🔄 Позицію {symbol} закрито | Причина: {reason}")
                send_telegram(f"🔄 Закрито {side} {symbol}\nПричина: {reason}\nPnL: {pnl:+.2f} USDT" if entry_price else f"🔄 Закрито {side} {symbol}")
                return True
        return False
    except Exception as e:
        print(f"{now()} ❌ Помилка закриття позиції {symbol}: {e}")
        return False

def exit_signal(df, side, symbol=""):
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema9 = last['EMA9']
        ema21 = last['EMA21']
        rsi = last['RSI']
        
        if pd.isna([ema9, ema21, rsi]).any():
            return False
        
        if side == "LONG":
            if ema9 < ema21:
                print(f"{now()} 🔴 {symbol} EXIT: EMA9 перетнула EMA21 вниз (було LONG)")
                return True
            if rsi < 30:
                print(f"{now()} 🔴 {symbol} EXIT: RSI перепроданість {rsi:.1f} (було LONG)")
                return True
        
        elif side == "SHORT":
            if ema9 > ema21:
                print(f"{now()} 🟢 {symbol} EXIT: EMA9 перетнула EMA21 вгору (було SHORT)")
                return True
            if rsi > 70:
                print(f"{now()} 🟢 {symbol} EXIT: RSI перекупленість {rsi:.1f} (було SHORT)")
                return True
        
        return False
    except Exception as e:
        print(f"{now()} ❌ Помилка перевірки exit signal: {e}")
        return False

def open_position(symbol, side, atr):
    global last_entry_time
    
    current_time = time.time()
    if symbol in last_entry_time:
        time_since_last = current_time - last_entry_time[symbol]
        if time_since_last < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - time_since_last
            print(f"{now()} ⏳ {symbol} у cooldown, залишилось {remaining:.0f}с")
            return False
    
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
            close_position(symbol, side, "TP/SL failed")
            send_telegram(f"⚠️ <b>Помилка відкриття позиції</b>\n\n"
                         f"Монета: {symbol}\n"
                         f"Причина: TP/SL не встановлено\n"
                         f"Позицію закрито автоматично")
            return False
        
        last_entry_time[symbol] = time.time()
        
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

def signal(df, symbol=""):
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        ema9 = last['EMA9']
        ema21 = last['EMA21']
        ema200 = last['EMA200']
        rsi = last['RSI']
        price = last['close']
        volume = last['volume']
        vol_ema = last['volume_ema']
        
        if pd.isna([ema9, ema21, ema200, rsi, vol_ema]).any():
            return None
        
        print(f"{now()} 🔍 {symbol}: price={price:.2f} | EMA9={ema9:.2f} EMA21={ema21:.2f} EMA200={ema200:.2f} | RSI={rsi:.1f} | vol={volume:.0f} avgVol={vol_ema:.0f}")
        
        if volume < vol_ema * 1.1:
            print(f"{now()}    └─ ❌ Низький об'єм, пропускаю")
            return None
        
        in_uptrend = price > ema200
        in_downtrend = price < ema200
        
        if ema9 > ema21 and in_uptrend and last['close'] > last['open'] and rsi > 45 and rsi < 80:
            if prev['close'] < last['close']:
                print(f"{now()}    └─ ✅ LONG сигнал!")
                return "LONG"
        
        if ema9 < ema21 and in_downtrend and last['close'] < last['open'] and rsi < 55 and rsi > 20:
            if prev['close'] > last['close']:
                print(f"{now()}    └─ ✅ SHORT сигнал!")
                return "SHORT"
        
        return None
    except Exception as e:
        print(f"{now()} ❌ Помилка генерації сигналу: {e}")
        return None

def main():
    print(f"\n{'='*60}")
    print(f"🤖 Bybit PRO Scalper Bot запущено о {now()}")
    print(f"{'='*60}")
    print(f"⚙️ Налаштування:")
    print(f"  • Режим: {'TESTNET' if TESTNET else 'LIVE'}")
    print(f"  • Розмір позиції: {ORDER_SIZE_USDT} USDT")
    print(f"  • Плече: {LEVERAGE}x")
    print(f"  • Макс. позицій: {MAX_POSITIONS}")
    print(f"  • Таймфрейм: {TIMEFRAME}")
    print(f"  • Мін. профіт: {MIN_PROFIT_PERCENT}%")
    print(f"  • Мін. баланс: {MIN_BALANCE_USDT} USDT")
    print(f"  • Cooldown: {COOLDOWN_SECONDS}с")
    print(f"\n📊 Індикатори (адаптивні для {TIMEFRAME}):")
    print(f"  • EMA: 9, 21, 200 (тренд)")
    print(f"  • RSI({RSI_WINDOW}) - швидкий моментум")
    print(f"  • ATR({ATR_WINDOW}) - волатильність")
    print(f"  • Volume EMA(20) - фільтр об'єму")
    print(f"\n🎯 PRO функції:")
    print(f"  ✅ Exit signals (EMA cross + RSI reversal)")
    print(f"  ✅ PnL tracking (winrate, equity)")
    print(f"  ✅ Cooldown захист")
    print(f"  ✅ Адаптивні параметри (1m/3m/5m)")
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
    last_pnl_report = time.time()
    
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
            
            if time.time() - last_pnl_report > 3600 and pnl_stats["total_trades"] > 0:
                print_pnl_stats()
                last_pnl_report = time.time()
            
            open_positions = get_open_positions()
            
            for pos in open_positions:
                try:
                    pos_symbol = pos.get('symbol')
                    contracts = float(pos.get('contracts', 0))
                    if abs(contracts) == 0:
                        continue
                    
                    pos_side = "LONG" if contracts > 0 else "SHORT"
                    entry_price = float(pos.get('entryPrice', 0))
                    
                    df = fetch_ohlcv(pos_symbol, limit=100)
                    if df is not None and len(df) > 50:
                        df = calculate_indicators(df)
                        if df is not None and exit_signal(df, pos_side, pos_symbol):
                            close_position(pos_symbol, pos_side, "EXIT signal", entry_price)
                            time.sleep(1)
                except Exception:
                    continue
            
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
                    if df is None or len(df) < 220:
                        continue
                    
                    df = calculate_indicators(df)
                    if df is None:
                        continue
                    
                    sig = signal(df, symbol)
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
