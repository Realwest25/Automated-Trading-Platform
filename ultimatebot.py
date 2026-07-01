import tkinter as tk
from tkinter import messagebox
import customtkinter
import json
import os
import sys
import time
import MetaTrader5 as mt5
import re
import threading
import asyncio
from dotenv import load_dotenv
load_dotenv()
from telethon import TelegramClient, events
from collections import OrderedDict

class NullWriter:
    def write(self, arg): pass
    def flush(self): pass
    def isatty(self): return False
    def read(self, *args): return ""
    def readline(self, *args): return ""
    def close(self): pass

if sys.stdout is None: sys.stdout = NullWriter()
if sys.stderr is None: sys.stderr = NullWriter()
if sys.stdin is None: sys.stdin = NullWriter()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")  

SAFE_CHANNEL_ID = -1002870620311
AGGRESSIVE_CHANNEL_ID = -1002362083959
FOREX_CHANNEL_ID = -1003181193464

SYMBOL_MAP = {
    "XAUUSD": "GOLD", "GOLD": "GOLD",      
    "XAGUSD": "SILVER", "SILVER": "SILVER",
    "XAUEUR": "XAUEUR",
    "AUDUSD": "AUDUSD", "EURUSD": "EURUSD", "GBPUSD": "GBPUSD",
    "US30": "US30", "NDAQ": "US100", "NAS100": "US100"
}

DEVIATION = 20
MAGIC_NUMBER = 20240930

RECONNECT_DELAY  = 30    
RECONNECT_LIMIT  = 10    
LOG_MAX_LINES    = 500   

if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

SESSION_NAME = os.path.join(application_path, 'mt5_trader_session')
CONFIG_FILE = os.path.join(application_path, 'config.json')

bot_thread = None
global_log_widget = None
async_loop = None
global_client = None
SELECTED_ACCOUNT_MODE = "STANDARD"
dashboard_running = False

mt5_lock = threading.Lock()

def print_to_gui(s):
    if global_log_widget:
        timestamp = time.strftime('[%H:%M:%S]')
        try:
            global_log_widget.after(0, lambda: _safe_insert(f"{timestamp} {s}\n"))
        except: pass

def _safe_insert(text):
    if not global_log_widget:
        return
    global_log_widget.insert(tk.END, text)
    global_log_widget.see(tk.END)

    line_count = int(global_log_widget.index(tk.END).split('.')[0])
    if line_count > LOG_MAX_LINES:
        trim_until = int(LOG_MAX_LINES * 0.2)
        global_log_widget.delete("1.0", f"{trim_until}.0")

def ensure_mt5_connection():
    path_file = "terminal.txt"
    if getattr(sys, 'frozen', False):
        path_file = os.path.join(application_path, "terminal.txt")
    custom_path = None
    if os.path.exists(path_file):
        try:
            with open(path_file, "r", encoding="utf-8") as f:
                content = f.read().strip().strip('"')
                if content: custom_path = content
        except: pass
    if mt5.terminal_info(): return True
    if custom_path:
        if not mt5.initialize(path=custom_path):
            print_to_gui(f"KRİTİK HATA: Özel yoldaki MT5 açılamadı! {mt5.last_error()}")
            return False
    else:
        if not mt5.initialize():
            print_to_gui(f"KRİTİK HATA: Varsayılan MT5 açılamadı! {mt5.last_error()}")
            return False
    return True

def calculate_rsi(symbol, timeframe, period=14):
    with mt5_lock:
        if not ensure_mt5_connection(): return 50.0
        fetch_count = max(500, period * 10) 
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, fetch_count)
        
    if rates is None or len(rates) < period + 1:
        return 50.0 
        
    closes = [r[4] for r in rates] 
    gains = []
    losses = []
    
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(closes)-1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def load_lot_config():
    default_config = {
        "SAFE": OrderedDict([
            ("max_total_lot", 50.0), ("default_lot", 0.01),
            ("dca_enabled", False), 
            ("dca_1_point", 100.0), ("dca_1_lot", 0.01), 
            ("dca_2_point", 200.0), ("dca_2_lot", 0.02), 
            ("dca_3_point", 500.0), ("dca_3_lot", 0.05),
            ("dca_4_point", 800.0), ("dca_4_lot", 0.10)
        ]),
        "AGGRESSIVE": OrderedDict([
            ("max_total_lot", 10.0), ("default_lot", 0.1),
            ("rsi_filter_enabled", False),
            ("rsi_overbought", 70.0),
            ("rsi_period", 14) 
        ]),
        "FOREX": OrderedDict([
            ("max_total_lot", 5.0), ("default_lot", 0.01),
            ("dca_enabled", False), 
            ("dca_1_point", 100.0), ("dca_1_lot", 0.01), 
            ("dca_2_point", 200.0), ("dca_2_lot", 0.02), 
            ("dca_3_point", 500.0), ("dca_3_lot", 0.05),
            ("dca_4_point", 800.0), ("dca_4_lot", 0.10)
        ])
    }
    if not os.path.exists(CONFIG_FILE): return default_config
    try:
        with open(CONFIG_FILE, 'r') as f:
            loaded_config = json.load(f)
            for mode in default_config:
                if mode in loaded_config: default_config[mode].update(loaded_config[mode])
            return default_config
    except: return default_config

def save_config(config_data):
    try:
        with open(CONFIG_FILE, 'w') as f: json.dump(config_data, f, indent=4)
        print_to_gui("Bilgi: Ayarlar Kaydedildi!")
        messagebox.showinfo("Başarılı", "Ayarlar Kaydedildi!")
    except Exception as e:
        messagebox.showerror("Hata", f"Kayıt hatası: {e}")

def cancel_pending_orders(symbol, mode):
    with mt5_lock:
        if not ensure_mt5_connection(): return
        
        positions = mt5.positions_get(symbol=symbol)
        if positions is not None:
            bot_positions = [pos for pos in positions if pos.magic == MAGIC_NUMBER and mode in pos.comment]
            if len(bot_positions) > 0:
                print_to_gui(f"🛡️ AKILLI ÇÖPÇÜ: {symbol} ({mode}) için açık işlemler var. Limit emirler KORUNUYOR!")
                return
                
        orders = mt5.orders_get(symbol=symbol)
        if orders is None or len(orders) == 0:
            print_to_gui(f"    -> {symbol} ({mode}) için bekleyen limit emir bulunamadı.")
            return
        
        canceled_count = 0
        for order in orders:
            if order.magic == MAGIC_NUMBER and mode in order.comment:
                request = {
                    "action": mt5.TRADE_ACTION_REMOVE,
                    "order": order.ticket
                }
                result = mt5.order_send(request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    canceled_count += 1
                else:
                    print_to_gui(f"    -> İptal Hatası (Bilet {order.ticket}): {result.comment}")
                    
        if canceled_count > 0:
            print_to_gui(f"🧹 Başarılı: {symbol} üzerindeki {canceled_count} adet {mode} hayalet limit emri silindi!")

def parse_signal(message_text, lot_config, mode):
    global SELECTED_ACCOUNT_MODE
    
    if "hedefte" in message_text.lower():
        clean_text = re.sub(r'[^a-zA-Z0-9]', '', message_text).upper()
        detected_symbol = None
        for key in sorted(SYMBOL_MAP.keys(), key=len, reverse=True):
            if key in clean_text:
                detected_symbol = SYMBOL_MAP[key]
                break
                
        if detected_symbol:
            final_symbol = detected_symbol
            if SELECTED_ACCOUNT_MODE == "ULTRALOW":
                final_symbol += "#"
            print_to_gui(f"[{mode}] 🟢 KÂR ALINDI! {final_symbol} için temizlik kontrolü başlatılıyor...")
            cancel_pending_orders(final_symbol, mode)
        else:
            print_to_gui(f"[{mode}] 🟢 Kâr alma mesajı, ancak sembol bulunamadı (Temizlik atlandı).")
        return None
        
    mode_settings = lot_config.get(mode, lot_config["SAFE"])
    
    signal_pattern = r"([A-Z0-9\s]{3,10})\s+(LONG|SHORT)(?:\s+GİR)?\s+HEDEF\s*([\d\.]+)\s*PİP"
    price_pattern = r"price\s*=\s*([\d\.]+)" 
    
    match_signal = re.search(signal_pattern, message_text, re.IGNORECASE | re.DOTALL)
    match_price = re.search(price_pattern, message_text, re.IGNORECASE)
    
    if not (match_signal and match_price):
        if "hedef" in message_text.lower() and ("long" in message_text.lower() or "short" in message_text.lower()):
            print_to_gui(f"⚠️ FORMAT UYUMSUZLUĞU! Mesaj: {message_text[:40]}...")
        return None
        
    try:
        with mt5_lock:
            if not ensure_mt5_connection(): return None
            
            signal_symbol_raw = match_signal.group(1) 
            clean_symbol = re.sub(r'[^a-zA-Z0-9]', '', signal_symbol_raw).upper()
            base_symbol = SYMBOL_MAP.get(clean_symbol, clean_symbol) 
            
            final_symbol = base_symbol
            if SELECTED_ACCOUNT_MODE == "ULTRALOW":
                final_symbol = base_symbol + "#"
                
            if mt5.symbol_info(final_symbol) is None:
                print_to_gui(f"HATA: '{final_symbol}' bulunamadı!")
                return None

            if not mt5.symbol_info(final_symbol).visible:
                 mt5.symbol_select(final_symbol, True)

            action = match_signal.group(2).upper()
            target_pips = int(float(match_signal.group(3)))
            signal_price_from_msg = float(match_price.group(1)) 
            
            tick = mt5.symbol_info_tick(final_symbol)
            if not tick:
                print_to_gui("HATA: Anlık fiyat alınamadı.")
                return None
            live_price = tick.ask if action == 'LONG' else tick.bid
            
        signal_lot = mode_settings.get('default_lot', 0.01)
        
        with mt5_lock:
            symbol_info = mt5.symbol_info(final_symbol)
            point_value = symbol_info.point
            digits = symbol_info.digits
        
        price_difference_tp = target_pips * point_value
        tp = signal_price_from_msg + price_difference_tp if action == 'LONG' else signal_price_from_msg - price_difference_tp
        
        print_to_gui(f"[{mode}] Sinyal: {final_symbol} {action} | Fiyat: {signal_price_from_msg} | TP: {tp:.{digits}f} | Lot: {signal_lot}")
        return {'symbol': final_symbol, 'action': 'BUY' if action == 'LONG' else 'SELL', 'sl': 0.0, 'tp': tp, 'lot': signal_lot, 'reference_price': live_price}
    except Exception as e:
        print_to_gui(f"HATA: Sinyal okuma hatası: {e}"); return None

def execute_trade(signal, mode):
    config = load_lot_config()
    mode_settings = config.get(mode, config["SAFE"])
    
    trade_type = mt5.ORDER_TYPE_BUY if signal['action'] == 'BUY' else mt5.ORDER_TYPE_SELL
    
    if mode == "AGGRESSIVE" and mode_settings.get("rsi_filter_enabled", False):
        if signal['action'] == 'BUY':
            overbought = mode_settings.get("rsi_overbought", 70.0)
            period = int(mode_settings.get("rsi_period", 14))
            
            rsi_d1 = calculate_rsi(signal['symbol'], mt5.TIMEFRAME_D1, period)
            if rsi_d1 > overbought:
                print_to_gui(f"🛑 İPTAL: {signal['symbol']} D1 RSI ({rsi_d1:.1f}) yüksek. Makro tepede BUY girilmedi!")
                return
            rsi_h4 = calculate_rsi(signal['symbol'], mt5.TIMEFRAME_H4, period)
            if rsi_h4 > overbought:
                print_to_gui(f"🛑 İPTAL: {signal['symbol']} H4 RSI ({rsi_h4:.1f}) yüksek. Tepeye BUY girilmedi!")
                return
            rsi_h1 = calculate_rsi(signal['symbol'], mt5.TIMEFRAME_H1, period)
            if rsi_h1 > overbought:
                print_to_gui(f"🛑 İPTAL: {signal['symbol']} H1 RSI ({rsi_h1:.1f}) yüksek. Tepeye BUY girilmedi!")
                return
            rsi_m15 = calculate_rsi(signal['symbol'], mt5.TIMEFRAME_M15, period)
            if rsi_m15 > overbought:
                print_to_gui(f"🛑 İPTAL: {signal['symbol']} M15 RSI ({rsi_m15:.1f}) yüksek. Tepeye BUY girilmedi!")
                return
    
    max_lot_limit = mode_settings.get("max_total_lot", 999.0)
    
    with mt5_lock:
        if not ensure_mt5_connection(): return
        positions = mt5.positions_get()
        if positions is None: return
        
        current_mode_lots = sum(pos.volume for pos in positions if mode in pos.comment)
        if (current_mode_lots + signal['lot']) > max_lot_limit:
            print_to_gui(f"!!! [{mode}] Max Lot ({max_lot_limit}) aşılacak. İptal."); return
            
        is_basket_trade = False
        
        if mode in ["SAFE", "FOREX"]:
            existing_same_positions = [
                pos for pos in positions 
                if pos.symbol == signal['symbol'] and pos.magic == MAGIC_NUMBER and mode in pos.comment and pos.type == trade_type
            ]
            
            is_basket_trade = len(existing_same_positions) > 0
            
            if is_basket_trade:
                tps = [pos.tp for pos in existing_same_positions if pos.tp > 0]
                all_tps = tps + [signal['tp']]
                best_tp = max(all_tps) if signal['action'] == 'BUY' else min(all_tps)
                signal['tp'] = best_tp

                updated_pos = 0
                for pos in existing_same_positions:
                    if pos.tp != best_tp:
                        res = mt5.order_send({
                            "action": mt5.TRADE_ACTION_SLTP,
                            "symbol": pos.symbol,
                            "sl": pos.sl,
                            "tp": best_tp,
                            "position": pos.ticket
                        })
                        if res.retcode == mt5.TRADE_RETCODE_DONE:
                            updated_pos += 1
                        else:
                            print_to_gui(f"    ⚠️ Pozisyon TP güncelleme hatası (#{pos.ticket}): {res.comment}")

                updated_orders = 0
                pending_orders = mt5.orders_get(symbol=signal['symbol'])
                if pending_orders:
                    for order in pending_orders:
                        if order.magic == MAGIC_NUMBER and mode in order.comment and order.tp != best_tp:
                            res = mt5.order_send({
                                "action": mt5.TRADE_ACTION_MODIFY,
                                "order": order.ticket,
                                "price": order.price_open,
                                "sl": order.sl,
                                "tp": best_tp,
                                "type_time": mt5.ORDER_TIME_GTC
                            })
                            if res.retcode == mt5.TRADE_RETCODE_DONE:
                                updated_orders += 1
                            else:
                                print_to_gui(f"    ⚠️ DCA TP güncelleme hatası (#{order.ticket}): {res.comment}")

                print_to_gui(f"🧺 SEPET: {signal['symbol']} → Ortak TP {best_tp:.4f} | {updated_pos} pozisyon + {updated_orders} DCA emri güncellendi.")

        tick = mt5.symbol_info_tick(signal['symbol'])
        symbol_info = mt5.symbol_info(signal['symbol'])
        if not tick or not symbol_info: return
        
        current_price = tick.ask if trade_type == mt5.ORDER_TYPE_BUY else tick.bid
        
        if trade_type == mt5.ORDER_TYPE_BUY and current_price >= signal['tp']:
            print_to_gui(f"⏳ GECİKMİŞ SİNYAL: Güncel fiyat ({current_price}) zaten hedefe ({signal['tp']}) ulaşmış. İptal!")
            return
        if trade_type == mt5.ORDER_TYPE_SELL and current_price <= signal['tp']:
            print_to_gui(f"⏳ GECİKMİŞ SİNYAL: Güncel fiyat ({current_price}) zaten hedefe ({signal['tp']}) ulaşmış. İptal!")
            return

        vol_step = symbol_info.volume_step
        calc_lot = round(float(signal['lot']) / vol_step) * vol_step
        final_lot = max(symbol_info.volume_min, min(calc_lot, symbol_info.volume_max))
        signal['lot'] = float(f"{final_lot:.2f}")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal['symbol'],
            "volume": signal['lot'],
            "type": trade_type,
            "price": current_price,
            "sl": signal['sl'],
            "tp": signal['tp'],
            "deviation": DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": f"TG_BOT_{mode}_{signal['action']}",
            "type_filling": mt5.ORDER_FILLING_IOC
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print_to_gui(f"!!! EMİR HATASI: {result.comment} (Kod: {result.retcode})")
        else: 
            print_to_gui(f"*** [{mode}] İŞLEM AÇILDI! Bilet: {result.order} | Lot: {signal['lot']}")

            if mode in ["SAFE", "FOREX"] and mode_settings.get("dca_enabled", False):
                if is_basket_trade:
                    print_to_gui(f"⚙️ [{mode}] Sepet işlemi olduğu için ek DCA limit emirleri DİZİLMEDİ.")
                else:
                    print_to_gui(f"⚙️ [{mode}] Masa boştu, ilk işlem. DCA limit emirleri gönderiliyor...")
                    point_value = symbol_info.point
                    
                    for i in range(1, 5):
                        dca_points = mode_settings.get(f"dca_{i}_point", 0.0)
                        raw_dca_lot = mode_settings.get(f"dca_{i}_lot", 0.0)
                        
                        if dca_points > 0 and raw_dca_lot > 0:
                            calc_dca_lot = round(float(raw_dca_lot) / vol_step) * vol_step
                            dca_lot = max(symbol_info.volume_min, min(calc_dca_lot, symbol_info.volume_max))
                            dca_lot = float(f"{dca_lot:.2f}")

                            price_diff = dca_points * point_value
                            limit_price = current_price - price_diff if signal['action'] == 'BUY' else current_price + price_diff
                            limit_type = mt5.ORDER_TYPE_BUY_LIMIT if signal['action'] == 'BUY' else mt5.ORDER_TYPE_SELL_LIMIT
                                
                            pending_request = {
                                "action": mt5.TRADE_ACTION_PENDING,
                                "symbol": signal['symbol'],
                                "volume": dca_lot,
                                "type": limit_type,
                                "price": limit_price,
                                "sl": 0.0,
                                "tp": signal['tp'], 
                                "deviation": DEVIATION,
                                "magic": MAGIC_NUMBER, 
                                "comment": f"TG_DCA_{i}_{mode}",
                                "type_time": mt5.ORDER_TIME_GTC
                            }
                            
                            pending_result = mt5.order_send(pending_request)
                            if pending_result.retcode == mt5.TRADE_RETCODE_DONE:
                                print_to_gui(f"    -> Kademe {i}: {limit_type} @ {limit_price:.{symbol_info.digits}f} | Lot: {dca_lot} | Başarılı.")
                            else:
                                print_to_gui(f"    -> Kademe {i} Hatası: {pending_result.comment}")

async def handler(event):
    chat_id = event.chat_id
    mode = None
    if chat_id == SAFE_CHANNEL_ID: mode = "SAFE"
    elif chat_id == AGGRESSIVE_CHANNEL_ID: mode = "AGGRESSIVE"
    elif chat_id == FOREX_CHANNEL_ID: mode = "FOREX"
    if not mode: return
    
    msg_preview = event.message.message.replace('\n', ' ')[:50]
    print_to_gui(f"--> [{mode}] Gelen: {msg_preview}...") 
    
    lot_config = load_lot_config()
    signal_data = parse_signal(event.message.message, lot_config, mode)
    if signal_data: 
        asyncio.create_task(asyncio.to_thread(execute_trade, signal_data, mode))

async def telegram_main(channels, status_label):
    global global_client, async_loop

    attempt = 0

    while dashboard_running:
        attempt += 1

        if RECONNECT_LIMIT > 0 and attempt > RECONNECT_LIMIT:
            print_to_gui(f"❌ {RECONNECT_LIMIT} deneme başarısız. Bot durduruluyor.")
            if status_label:
                status_label.after(0, lambda: status_label.configure(text="BAĞLANTI BAŞARISIZ", text_color="red"))
            break

        try:
            if attempt > 1:
                print_to_gui(f"🔄 Yeniden bağlanılıyor... (Deneme {attempt})")

            global_client = TelegramClient(SESSION_NAME, API_ID, API_HASH, loop=async_loop)
            await global_client.start()

            print_to_gui("✅ Telegram Bağlandı.")
            ensure_mt5_connection()

            attempt = 0  

            if status_label:
                status_label.after(0, lambda: status_label.configure(text="BAĞLANDI & ÇALIŞIYOR", text_color="#00ff00"))

            global_client.add_event_handler(handler, events.NewMessage(chats=channels))
            await global_client.run_until_disconnected()

        except Exception as e:
            print_to_gui(f"⚠️ Bağlantı koptu: {e}")
            if global_client:
                try:
                    await global_client.disconnect() 
                except: pass

        if not dashboard_running:
            break

        if status_label:
            status_label.after(0, lambda: status_label.configure(
                text=f"YENİDEN BAĞLANIYOR... ({RECONNECT_DELAY}s)", text_color="orange"))

        print_to_gui(f"⏳ {RECONNECT_DELAY} saniye bekleniyor...")
        await asyncio.sleep(RECONNECT_DELAY)

def run_bot_in_thread(channels, status_label):
    global async_loop
    async_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(async_loop)
    async_loop.run_until_complete(telegram_main(channels, status_label))

def start_bot_thread(status_label, safe_var, agg_var, forex_var, account_type_var):
    global bot_thread, dashboard_running, SELECTED_ACCOUNT_MODE
    SELECTED_ACCOUNT_MODE = account_type_var.get()
    print_to_gui(f"Bilgi: Bot başlatılıyor... Mod: {SELECTED_ACCOUNT_MODE}")
    if bot_thread and bot_thread.is_alive(): return
    channels = []
    if safe_var.get(): channels.append(SAFE_CHANNEL_ID)
    if agg_var.get(): channels.append(AGGRESSIVE_CHANNEL_ID)
    if forex_var.get(): channels.append(FOREX_CHANNEL_ID)
    if not channels: messagebox.showwarning("Uyarı", "Kanal seçmelisiniz!"); return
    status_label.configure(text="BAĞLANIYOR...", text_color="orange")
    bot_thread = threading.Thread(target=run_bot_in_thread, args=(channels, status_label), daemon=True)
    bot_thread.start()
    dashboard_running = True

def stop_bot_thread(status_label):
    global bot_thread, global_client, async_loop, dashboard_running
    dashboard_running = False 
    if not (bot_thread and bot_thread.is_alive()): return
    try:
        if global_client and async_loop: asyncio.run_coroutine_threadsafe(global_client.disconnect(), async_loop)
        with mt5_lock:
            mt5.shutdown()
        status_label.configure(text="DURDURULDU", text_color="red")
        print_to_gui("Bot durduruldu.")
    except: pass
    bot_thread = None

def update_gui_stats(lbl_balance, lbl_equity, lbl_profit, lbl_account):
    if not dashboard_running:
        lbl_balance.after(2000, lambda: update_gui_stats(lbl_balance, lbl_equity, lbl_profit, lbl_account))
        return

    with mt5_lock:
        if ensure_mt5_connection():
            try:
                account_info = mt5.account_info()
                if account_info:
                    bal = account_info.balance
                    eq = account_info.equity
                    profit = account_info.profit
                    login = account_info.login
                    server = account_info.server
                    
                    lbl_account.configure(text=f"Hesap: {login} ({server})")
                    lbl_balance.configure(text=f"$ {bal:.2f}")
                    lbl_equity.configure(text=f"$ {eq:.2f}")
                    
                    p_color = "#00ff00" if profit >= 0 else "#ff4d4d"
                    lbl_profit.configure(text=f"$ {profit:.2f}", text_color=p_color)
                else:
                    lbl_account.configure(text="Hesap Bilgisi Yok")
            except: pass
    lbl_balance.after(3000, lambda: update_gui_stats(lbl_balance, lbl_equity, lbl_profit, lbl_account))

def update_live_rsi_table(lbl_d1, lbl_h4, lbl_h1, lbl_m15, acc_var):
    conf = load_lot_config().get("AGGRESSIVE", {})
    period = int(conf.get("rsi_period", 14))
    
    if ensure_mt5_connection():
        try:
            base_sym = "GOLD"
            final_sym = base_sym + "#" if acc_var.get() == "ULTRALOW" else base_sym
            
            if mt5.symbol_info(final_sym) is not None:
                with mt5_lock:
                    if not mt5.symbol_info(final_sym).visible:
                        mt5.symbol_select(final_sym, True)
                    
                r_d1 = calculate_rsi(final_sym, mt5.TIMEFRAME_D1, period)
                r_h4 = calculate_rsi(final_sym, mt5.TIMEFRAME_H4, period)
                r_h1 = calculate_rsi(final_sym, mt5.TIMEFRAME_H1, period)
                r_m15 = calculate_rsi(final_sym, mt5.TIMEFRAME_M15, period)
                
                def get_color(val):
                    if val >= 70.0: return "#ff4d4d" 
                    if val <= 30.0: return "#00ff00" 
                    return "white" 

                lbl_d1.configure(text=f"{r_d1:.1f}", text_color=get_color(r_d1)) if r_d1 != 50.0 else lbl_d1.configure(text="--", text_color="gray")
                lbl_h4.configure(text=f"{r_h4:.1f}", text_color=get_color(r_h4)) if r_h4 != 50.0 else lbl_h4.configure(text="--", text_color="gray")
                lbl_h1.configure(text=f"{r_h1:.1f}", text_color=get_color(r_h1)) if r_h1 != 50.0 else lbl_h1.configure(text="--", text_color="gray")
                lbl_m15.configure(text=f"{r_m15:.1f}", text_color=get_color(r_m15)) if r_m15 != 50.0 else lbl_m15.configure(text="--", text_color="gray")
            else:
                lbl_d1.configure(text="Hata", text_color="red")
                lbl_h4.configure(text="Hata", text_color="red")
                lbl_h1.configure(text="Hata", text_color="red")
                lbl_m15.configure(text="Hata", text_color="red")
        except: pass
        
    lbl_d1.after(3000, lambda: update_live_rsi_table(lbl_d1, lbl_h4, lbl_h1, lbl_m15, acc_var))

def setup_gui():
    config = load_lot_config()
    customtkinter.set_appearance_mode("Dark")
    customtkinter.set_default_color_theme("dark-blue")
    
    root = customtkinter.CTk()
    root.title("ULTIMATE FX BOT 2026 - PRO EDITION v2.6.6")
    root.geometry("850x880") 
    
    header_frame = customtkinter.CTkFrame(root, fg_color="transparent")
    header_frame.pack(fill="x", padx=20, pady=5)
    title_label = customtkinter.CTkLabel(header_frame, text="ULTIMATE FX BOT 2026 v2.6.6", font=("Roboto", 24, "bold"), text_color="#3B8ED0")
    title_label.pack()
    subtitle_label = customtkinter.CTkLabel(header_frame, text="Full Basket Sync • DCA TP Update • Strict Cleaner • Sniper Aggressive", font=("Roboto", 12))
    subtitle_label.pack()

    dashboard_frame = customtkinter.CTkFrame(root)
    dashboard_frame.pack(fill="x", padx=20, pady=5)
    lbl_account = customtkinter.CTkLabel(dashboard_frame, text="Bağlanıyor...", font=("Arial", 12))
    lbl_account.grid(row=0, column=0, columnspan=3, pady=(2,0))
    dashboard_frame.grid_columnconfigure((0,1,2), weight=1)
    
    card1 = customtkinter.CTkFrame(dashboard_frame, fg_color="#2b2b2b", corner_radius=10)
    card1.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
    customtkinter.CTkLabel(card1, text="BAKİYE", font=("Arial", 9, "bold"), text_color="gray").pack(pady=(5,0))
    lbl_balance = customtkinter.CTkLabel(card1, text="$ 0.00", font=("Arial", 16, "bold"), text_color="white")
    lbl_balance.pack(pady=(0,5))
    
    card2 = customtkinter.CTkFrame(dashboard_frame, fg_color="#2b2b2b", corner_radius=10)
    card2.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
    customtkinter.CTkLabel(card2, text="VARLIK", font=("Arial", 9, "bold"), text_color="gray").pack(pady=(5,0))
    lbl_equity = customtkinter.CTkLabel(card2, text="$ 0.00", font=("Arial", 16, "bold"), text_color="#3498db")
    lbl_equity.pack(pady=(0,5))
    
    card3 = customtkinter.CTkFrame(dashboard_frame, fg_color="#2b2b2b", corner_radius=10)
    card3.grid(row=1, column=2, padx=10, pady=5, sticky="ew")
    customtkinter.CTkLabel(card3, text="ANLIK P/L", font=("Arial", 9, "bold"), text_color="gray").pack(pady=(5,0))
    lbl_profit = customtkinter.CTkLabel(card3, text="$ 0.00", font=("Arial", 16, "bold"), text_color="#00ff00")
    lbl_profit.pack(pady=(0,5))

    middle_frame = customtkinter.CTkFrame(root, fg_color="transparent")
    middle_frame.pack(fill="both", expand=True, padx=20, pady=5)
    middle_frame.grid_columnconfigure(0, weight=1)
    middle_frame.grid_columnconfigure(1, weight=2)

    control_box = customtkinter.CTkFrame(middle_frame)
    control_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    
    customtkinter.CTkLabel(control_box, text="KONTROL PANELİ", font=("Arial", 14, "bold")).pack(pady=(15, 5))
    status_label = customtkinter.CTkLabel(control_box, text="DURUM: BEKLİYOR", font=("Arial", 12, "bold"), text_color="gray")
    status_label.pack(pady=2)
    
    channel_frame = customtkinter.CTkFrame(control_box, fg_color="transparent")
    channel_frame.pack(pady=5)
    
    safe_var = customtkinter.BooleanVar(value=True)
    forex_var = customtkinter.BooleanVar(value=True)
    agg_var = customtkinter.BooleanVar(value=False)
    
    customtkinter.CTkCheckBox(channel_frame, text="SAFE Bot", variable=safe_var).pack(anchor="w", pady=5)
    customtkinter.CTkCheckBox(channel_frame, text="FOREX Bot", variable=forex_var).pack(anchor="w", pady=5)
    customtkinter.CTkCheckBox(channel_frame, text="AGGRESSIVE Bot", variable=agg_var).pack(anchor="w", pady=5)
    
    acc_frame = customtkinter.CTkFrame(control_box, fg_color="transparent")
    acc_frame.pack(pady=10)
    customtkinter.CTkLabel(acc_frame, text="HESAP TÜRÜ", font=("Arial", 12, "bold")).pack(anchor="center", pady=(0, 5))
    
    radio_subframe = customtkinter.CTkFrame(acc_frame, fg_color="transparent")
    radio_subframe.pack(anchor="center")
    
    account_type_var = customtkinter.StringVar(value="STANDARD")
    customtkinter.CTkRadioButton(radio_subframe, text="STANDART", variable=account_type_var, value="STANDARD").pack(anchor="w", pady=5)
    customtkinter.CTkRadioButton(radio_subframe, text="ULTRA LOW", variable=account_type_var, value="ULTRALOW").pack(anchor="w", pady=5)

    start_btn = customtkinter.CTkButton(control_box, text="BAŞLAT", fg_color="#00b894", hover_color="#00a884", height=40, font=("Arial", 14, "bold"),
                                        command=lambda: start_bot_thread(status_label, safe_var, agg_var, forex_var, account_type_var))
    start_btn.pack(fill="x", padx=20, pady=(15, 10))
    stop_btn = customtkinter.CTkButton(control_box, text="DURDUR", fg_color="#d63031", hover_color="#c0392b", height=40, font=("Arial", 14, "bold"),
                                       command=lambda: stop_bot_thread(status_label))
    stop_btn.pack(fill="x", padx=20, pady=(0, 15))

    right_panel = customtkinter.CTkFrame(middle_frame, fg_color="transparent")
    right_panel.grid(row=0, column=1, sticky="nsew")
    right_panel.grid_columnconfigure(0, weight=1)
    right_panel.grid_rowconfigure(0, weight=1)

    tab_view = customtkinter.CTkTabview(right_panel)
    tab_view.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
    
    safe_tab = tab_view.add("SAFE")
    agg_tab = tab_view.add("AGGRESSIVE")
    forex_tab = tab_view.add("FOREX")
    
    entries = {"SAFE": {}, "AGGRESSIVE": {}, "FOREX": {}}
    dca_vars = {
        "SAFE": customtkinter.BooleanVar(value=config["SAFE"].get("dca_enabled", False)),
        "FOREX": customtkinter.BooleanVar(value=config["FOREX"].get("dca_enabled", False))
    }
    rsi_vars = {
        "AGGRESSIVE": customtkinter.BooleanVar(value=config["AGGRESSIVE"].get("rsi_filter_enabled", False))
    }

    def build_tab_ui(tab, mode, conf):
        tab.grid_columnconfigure(0, weight=1)
        
        gen_frame = customtkinter.CTkFrame(tab, fg_color="#2b2b2b", corner_radius=8)
        gen_frame.pack(fill="x", padx=5, pady=5)
        for c in range(7): gen_frame.grid_columnconfigure(c, weight=1 if c % 3 == 0 else 0)
        
        customtkinter.CTkLabel(gen_frame, text="GENEL AYARLAR", font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=7, pady=(10, 5))
        
        entries[mode]["max_total_lot"] = tk.StringVar(value=str(conf.get("max_total_lot", 50.0)))
        customtkinter.CTkLabel(gen_frame, text="Max Lot:", font=("Arial", 12, "bold")).grid(row=1, column=1, padx=(0, 2), pady=(5, 15), sticky="e")
        customtkinter.CTkEntry(gen_frame, textvariable=entries[mode]["max_total_lot"], width=80, justify="center").grid(row=1, column=2, pady=(5, 15), sticky="w")

        entries[mode]["default_lot"] = tk.StringVar(value=str(conf.get("default_lot", 0.01)))
        customtkinter.CTkLabel(gen_frame, text="Default Lot:", font=("Arial", 12, "bold")).grid(row=1, column=4, padx=(0, 2), pady=(5, 15), sticky="e")
        customtkinter.CTkEntry(gen_frame, textvariable=entries[mode]["default_lot"], width=80, justify="center").grid(row=1, column=5, pady=(5, 15), sticky="w")

        if mode in ["SAFE", "FOREX"]:
            dca_frame = customtkinter.CTkFrame(tab, fg_color="#2b2b2b", corner_radius=8)
            dca_frame.pack(fill="x", padx=5, pady=5)
            for c in range(7): dca_frame.grid_columnconfigure(c, weight=1 if c % 3 == 0 else 0)
            
            customtkinter.CTkCheckBox(dca_frame, text="DCA MODU", variable=dca_vars[mode], text_color="#00e676", font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=7, pady=(15, 10))

            for i in range(1, 5):
                p_key = f"dca_{i}_point"
                l_key = f"dca_{i}_lot"
                entries[mode][p_key] = tk.StringVar(value=str(conf.get(p_key, 0.0)))
                entries[mode][l_key] = tk.StringVar(value=str(conf.get(l_key, 0.0)))
                customtkinter.CTkLabel(dca_frame, text=f"Kademe{i}:", font=("Arial", 12, "bold")).grid(row=i, column=1, padx=(0, 2), pady=5, sticky="e")
                customtkinter.CTkEntry(dca_frame, textvariable=entries[mode][p_key], width=80, justify="center").grid(row=i, column=2, pady=5, sticky="w")
                customtkinter.CTkLabel(dca_frame, text="Lot Size:", font=("Arial", 12, "bold")).grid(row=i, column=4, padx=(0, 2), pady=5, sticky="e")
                customtkinter.CTkEntry(dca_frame, textvariable=entries[mode][l_key], width=80, justify="center").grid(row=i, column=5, pady=(5, 15 if i == 4 else 5), sticky="w")
        else:
            rsi_frame = customtkinter.CTkFrame(tab, fg_color="#2b2b2b", corner_radius=8)
            rsi_frame.pack(fill="x", padx=5, pady=5)
            for c in range(7): rsi_frame.grid_columnconfigure(c, weight=1 if c % 3 == 0 else 0)
            
            customtkinter.CTkCheckBox(rsi_frame, text="D1, H4, H1 & M15 RSI FİLTRESİ", variable=rsi_vars[mode], text_color="#ffcc00", font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=7, pady=(10, 5))

            entries[mode]["rsi_overbought"] = tk.StringVar(value=str(conf.get("rsi_overbought", 70.0)))
            entries[mode]["rsi_period"] = tk.StringVar(value=str(conf.get("rsi_period", 14)))
            
            customtkinter.CTkLabel(rsi_frame, text="Buy Engeli (>):", font=("Arial", 12, "bold")).grid(row=1, column=1, padx=(0, 2), pady=(5, 5), sticky="e")
            customtkinter.CTkEntry(rsi_frame, textvariable=entries[mode]["rsi_overbought"], width=80, justify="center").grid(row=1, column=2, pady=(5, 5), sticky="w")
            customtkinter.CTkLabel(rsi_frame, text="RSI Periyodu:", font=("Arial", 12, "bold")).grid(row=1, column=4, padx=(0, 2), pady=(5, 5), sticky="e")
            customtkinter.CTkEntry(rsi_frame, textvariable=entries[mode]["rsi_period"], width=80, justify="center").grid(row=1, column=5, pady=(5, 5), sticky="w")
            
            customtkinter.CTkLabel(tab, text="Sadece LONG (Buy) işlemleri makro ve mikro 4 katmanla filtrelenir.", text_color="#ffcc00", font=("Arial", 10, "italic")).pack(pady=5)

            live_rsi_frame = customtkinter.CTkFrame(tab, fg_color="#2b2b2b", corner_radius=8)
            live_rsi_frame.pack(fill="x", padx=5, pady=5)
            live_rsi_frame.grid_columnconfigure((0,1,2,3), weight=1)
            
            customtkinter.CTkLabel(live_rsi_frame, text="GOLD CANLI RSI İZLEME PANELİ", font=("Arial", 12, "bold"), text_color="#3498db").grid(row=0, column=0, columnspan=4, pady=(10, 10))
            
            rsi_lbls = {}
            for col, name in enumerate(("D1", "H4", "H1", "M15")):
                customtkinter.CTkLabel(live_rsi_frame, text=f"{name} RSI", font=("Arial", 11, "bold"), text_color="gray").grid(row=1, column=col)
                lbl = customtkinter.CTkLabel(live_rsi_frame, text="--", font=("Arial", 16, "bold"))
                lbl.grid(row=2, column=col, pady=(0, 10))
                rsi_lbls[name] = lbl

            update_live_rsi_table(rsi_lbls["D1"], rsi_lbls["H4"], rsi_lbls["H1"], rsi_lbls["M15"], account_type_var)

    build_tab_ui(safe_tab, "SAFE", config["SAFE"])
    build_tab_ui(agg_tab, "AGGRESSIVE", config["AGGRESSIVE"])
    build_tab_ui(forex_tab, "FOREX", config["FOREX"])

    def save_all():
        new_conf = {"SAFE": {}, "AGGRESSIVE": {}, "FOREX": {}}
        try:
            for m in entries:
                for k in entries[m]: 
                    new_conf[m][k] = float(entries[m][k].get())
                if m in ["SAFE", "FOREX"]:
                    new_conf[m]["dca_enabled"] = dca_vars[m].get()
                else:
                    new_conf[m]["rsi_filter_enabled"] = rsi_vars[m].get()
                    new_conf[m]["dca_enabled"] = False 
            save_config(new_conf)
        except Exception as e: 
            messagebox.showerror("Hata", f"Lütfen sayısal değer girin! Hata detayı: {e}")
            
    save_btn = customtkinter.CTkButton(right_panel, text="AYARLARI KAYDET", command=save_all, fg_color="#0984e3", width=140, height=32, font=("Arial", 12, "bold"))
    save_btn.grid(row=1, column=0, pady=(5, 10))

    log_frame = customtkinter.CTkFrame(root)
    log_frame.pack(fill="both", expand=True, padx=20, pady=(5, 15))
    customtkinter.CTkLabel(log_frame, text="CANLI LOG KAYDI", font=("Arial", 12, "bold"), anchor="w").pack(fill="x", padx=10, pady=2)
    log_widget = customtkinter.CTkTextbox(log_frame, font=('Consolas', 11), text_color="#00e676", fg_color="#1e1e1e")
    log_widget.pack(fill="both", expand=True, padx=5, pady=5)
    global global_log_widget; global_log_widget = log_widget

    update_gui_stats(lbl_balance, lbl_equity, lbl_profit, lbl_account)

    def on_close():
        stop_bot_thread(status_label)
        root.destroy()
        sys.exit()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == '__main__':
    if not os.path.exists(CONFIG_FILE): save_config(load_lot_config())
    setup_gui()