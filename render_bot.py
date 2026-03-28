#!/usr/bin/env python3
"""SIGNAL AI Telegram Bot v2.0 — Heikin Ashi + Parabolic SAR + Fibonacci + S/R + Sessions"""
import os, math, time, json, threading, requests, io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN  = "8542231431:AAHJ-9Rwr_taqFMaBd9YBau8bVcMU38633Y"
TWELVE_KEY = os.environ.get("TWELVE_KEY")
if not TWELVE_KEY:
    TWELVE_KEY = "99b3ca01dbdf45ccb2f5968b16af1c82"

KYIV = timezone(timedelta(hours=2))  # ← ДОДАНО

# ══ РОЗРАХУНОК ЧАСУ ВХОДУ ══════════════════════════════
def get_entry_time(tf: str):
    """
    Повертає (час_входу, час_виходу, рядок_до_закриття)
    Час входу = початок НАСТУПНОЇ свічки
    """
    try:
        mins = int(tf)
    except:
        mins = 5
    now = datetime.now(KYIV)
    current_start  = (now.minute // mins) * mins
    next_min       = current_start + mins
    entry_dt       = now.replace(second=0, microsecond=0, minute=0) + timedelta(minutes=next_min)
    exit_dt        = entry_dt + timedelta(minutes=mins)
    seconds_in     = mins * 60
    seconds_past   = (now.minute % mins) * 60 + now.second
    closes_in      = seconds_in - seconds_past
    closes_min     = closes_in // 60
    closes_sec     = closes_in % 60
    return (
        entry_dt.strftime("%H:%M"),
        exit_dt.strftime("%H:%M"),
        f"{closes_min}хв {closes_sec:02d}с"
    )

# ══ АВТО-СИГНАЛИ: список підписників ══════════════════
SUBSCRIBERS_FILE = "subscribers.json"
_subscribers = set()
_auto_tf     = {}
AUTO_DEFAULT_TF = "5"

def _load_subscribers():
    try:
        if os.path.exists(SUBSCRIBERS_FILE):
            with open(SUBSCRIBERS_FILE) as f:
                data = json.load(f)
                return set(data.get("subscribers", [])), data.get("auto_tf", {})
    except: pass
    return set(), {}

def _save_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "w") as f:
            json.dump({"subscribers": list(_subscribers),
                       "auto_tf": {str(k): v for k,v in _auto_tf.items()}}, f)
    except: pass

_subscribers, _auto_tf = _load_subscribers()

_last_signals = {}

JOURNAL_FILE = "journal.json"
_journal_lock = threading.Lock()

def load_journal():
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE) as f:
                return json.load(f)
    except:
        pass
    return {}

def save_journal(d):
    with _journal_lock:
        try:
            with open(JOURNAL_FILE, "w") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except:
            pass

all_journal = load_journal()
MAX_JOURNAL_PER_USER = 500

def add_journal_entry(cid, pair, tf, direction, acc, entry_price, result=None, pnl=None):
    k = str(cid)
    if k not in all_journal:
        all_journal[k] = []
    entry = {
        "id": len(all_journal[k]) + 1,
        "time": datetime.now(KYIV).strftime("%H:%M %d.%m.%Y"),
        "pair": pair, "tf": tf,
        "dir": "UP" if direction else "DOWN",
        "acc": acc, "entry": entry_price,
        "result": result, "pnl": pnl
    }
    all_journal[k].append(entry)
    if len(all_journal[k]) > MAX_JOURNAL_PER_USER:
        all_journal[k] = all_journal[k][-MAX_JOURNAL_PER_USER:]
    save_journal(all_journal)
    return entry

def get_journal(cid, limit=10):
    return all_journal.get(str(cid), [])[-limit:]

def calc_money_management(acc, balance=100):
    if acc >= 88:   pct = 5; label = "🔥 Сильний сигнал"
    elif acc >= 80: pct = 3; label = "✅ Середній сигнал"
    elif acc >= 74: pct = 2; label = "⚠️ Слабкий сигнал"
    else:           pct = 0; label = "⛔ Не торгувати"
    amount = round(balance * pct / 100, 2)
    return pct, amount, label

def mm_text(acc, balance=100):
    pct, amount, label = calc_money_management(acc, balance)
    if pct == 0:
        return "\n💰 *Money Management:* ⛔ Пропустити угоду\n"
    return (
        f"\n💰 *Money Management:*\n"
        f"{label}\n"
        f"Ставка: *{pct}%* від депозиту\n"
        f"При $100 → *${amount}*\n"
    )

_news_cache = {"time": 0, "events": []}
NEWS_CACHE_SEC = 600

def fetch_news_events():
    now = time.time()
    if now - _news_cache["time"] < NEWS_CACHE_SEC:
        return _news_cache["events"]
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=8)
        data = r.json()
        high = [e for e in data if e.get("impact") == "High"]
        events = []
        for e in high:
            try:
                t = datetime.strptime(e["date"], "%Y-%m-%dT%H:%M:%S%z")
                events.append({"title": e.get("title",""), "time": t, "currency": e.get("currency","")})
            except:
                pass
        _news_cache["time"] = now
        _news_cache["events"] = events
        return events
    except:
        return []

def check_news_filter(pair_name):
    try:
        events = fetch_news_events()
        if not events:
            return False, ""
        now = datetime.now(timezone.utc)
        currencies = pair_name.replace(" OTC","").replace("/","")[:6]
        cur_list = [currencies[:3], currencies[3:6]]
        for e in events:
            diff = abs((e["time"] - now).total_seconds() / 60)
            if diff <= 30 and e.get("currency","") in cur_list:
                mins = int(diff)
                when = "через" if e["time"] > now else "тому"
                return True, f"⚠️ Новина: *{e['title']}* ({e['currency']}) {mins} хв {when}"
    except:
        pass
    return False, ""

def mtf_analysis(pair_name):
    tfs = ["5", "15", "60"]
    results = {}
    for tf in tfs:
        try:
            sig = generate_signal(pair_name, tf)
            if sig:
                results[tf] = sig
        except:
            pass
    if len(results) < 2:
        return None, ""
    directions = [1 if v["is_buy"] else -1 for v in results.values()]
    agree = sum(directions)
    tf_labels = {"5": "5хв", "15": "15хв", "60": "1год"}
    lines = []
    for tf, sig in results.items():
        arrow = "⬆️" if sig["is_buy"] else "⬇️"
        lines.append(f"{arrow} {tf_labels[tf]}: {sig['acc']}%")
    summary = "\n".join(lines)
    if agree == 3:   return 1,  f"🟢 *MTF: Всі 3 ТФ — ВВЕРХ*\n{summary}"
    elif agree == -3: return -1, f"🔴 *MTF: Всі 3 ТФ — ВНИЗ*\n{summary}"
    elif agree >= 1:  return 1,  f"🟡 *MTF: 2/3 ТФ — ВВЕРХ*\n{summary}"
    elif agree <= -1: return -1, f"🟡 *MTF: 2/3 ТФ — ВНИЗ*\n{summary}"
    else:             return 0,  f"⚪ *MTF: Суперечливі сигнали*\n{summary}"

def check_reversal(cid, pair_name, tf):
    k = str(cid)
    if k not in _last_signals:
        return False, ""
    last = _last_signals.get(k, {})
    if last.get("pair") != pair_name or last.get("tf") != tf:
        return False, ""
    try:
        sig = generate_signal(pair_name, tf)
        if sig and sig["is_buy"] != last["is_buy"]:
            old = "ВВЕРХ ⬆️" if last["is_buy"] else "ВНИЗ ⬇️"
            new = "ВВЕРХ ⬆️" if sig["is_buy"] else "ВНИЗ ⬇️"
            return True, f"🔄 *РОЗВОРОТ!* {pair_name}\n{old} → {new}\nТочність: {sig['acc']}%"
    except:
        pass
    return False, ""

def reversal_monitor():
    CHECK_INTERVAL = 600
    _last_checked = {}
    while True:
        try:
            now = time.time()
            for k, last in list(_last_signals.items()):
                cid   = int(k)
                pair  = last.get("pair")
                tf    = last.get("tf")
                sent_at = last.get("sent_at", 0)
                if not pair or not tf:
                    continue
                if now - sent_at > 7200:
                    _last_signals.pop(k, None)
                    continue
                check_key = f"{pair}_{tf}"
                if now - _last_checked.get(check_key, 0) < CHECK_INTERVAL:
                    continue
                _last_checked[check_key] = now
                from_cache = _candle_cache.get(f"{pair}_{tf}")
                if not from_cache:
                    continue
                try:
                    sig = generate_signal(pair, tf)
                except:
                    continue
                if sig and sig["is_buy"] != last["is_buy"]:
                    old = "⬆️ ВВЕРХ" if last["is_buy"] else "⬇️ ВНИЗ"
                    new = "⬆️ ВВЕРХ" if sig["is_buy"] else "⬇️ ВНИЗ"
                    msg = (
                        f"🔄 *УВАГА! РОЗВОРОТ!*\n\n"
                        f"💱 *{pair}* | {tf}хв\n"
                        f"{old} → *{new}*\n"
                        f"Точність: *{sig['acc']}%*\n\n"
                        f"⚡ Рекомендую закрити попередню угоду!"
                    )
                    try:
                        bot.send_message(cid, msg, parse_mode="Markdown")
                    except:
                        pass
                    _last_signals.pop(k, None)
        except:
            pass
        time.sleep(120)

def auto_signal_loop():
    while True:
        time.sleep(300)
        if not _subscribers:
            continue
        scan_pairs = FOREX_PAIRS[:6] + OTC_PAIRS[:4] + CRYPTO_PAIRS[:3]
        results = []
        for p in scan_pairs:
            try:
                tf = "5"
                sig = generate_signal(p["name"], tf)
                if sig and sig["acc"] >= 85 and not sig.get("blocked"):
                    results.append((p["name"], tf, sig))
            except:
                pass
        results.sort(key=lambda x: -x[2]["acc"])
        best = results[:2]
        if not best:
            continue
        for cid in list(_subscribers):
            try:
                bot.send_message(cid, "⚡ *Авто-сигнали SIGNAL AI*", parse_mode="Markdown")
                for pair, tf, sig in best:
                    txt = format_signal(pair, tf, sig)
                    bot.send_message(cid, txt, parse_mode="Markdown",
                                     reply_markup=result_kb(pair, tf))
                    _last_signals[str(cid)] = {
                        "pair": pair, "tf": tf,
                        "is_buy": sig["is_buy"],
                        "sent_at": time.time()
                    }
                    time.sleep(0.5)
            except:
                pass

TWELVE_URL = "https://api.twelvedata.com"
STATS_FILE = "stats.json"

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не встановлено!")

bot = TeleBot(BOT_TOKEN)

FOREX_PAIRS=[
    {"name":"EUR/USD","symbol":"EUR/USD","p":1.08,"d":5},
    {"name":"GBP/USD","symbol":"GBP/USD","p":1.27,"d":5},
    {"name":"USD/JPY","symbol":"USD/JPY","p":149.5,"d":3},
    {"name":"AUD/USD","symbol":"AUD/USD","p":0.645,"d":5},
    {"name":"NZD/USD","symbol":"NZD/USD","p":0.596,"d":5},
    {"name":"USD/CAD","symbol":"USD/CAD","p":1.357,"d":5},
    {"name":"USD/CHF","symbol":"USD/CHF","p":0.903,"d":5},
    {"name":"EUR/GBP","symbol":"EUR/GBP","p":0.853,"d":5},
    {"name":"EUR/JPY","symbol":"EUR/JPY","p":161.5,"d":3},
    {"name":"GBP/JPY","symbol":"GBP/JPY","p":189.8,"d":3},
    {"name":"AUD/CAD","symbol":"AUD/CAD","p":0.874,"d":5},
    {"name":"AUD/JPY","symbol":"AUD/JPY","p":96.4,"d":3},
    {"name":"CHF/JPY","symbol":"CHF/JPY","p":165.5,"d":3},
    {"name":"EUR/AUD","symbol":"EUR/AUD","p":1.672,"d":5},
    {"name":"EUR/CAD","symbol":"EUR/CAD","p":1.464,"d":5},
    {"name":"GBP/AUD","symbol":"GBP/AUD","p":1.975,"d":5},
    {"name":"GBP/CAD","symbol":"GBP/CAD","p":1.722,"d":5},
]
OTC_PAIRS=[{"name":p["name"]+" OTC","symbol":p["symbol"],"p":p["p"],"d":p["d"]}
           for p in FOREX_PAIRS[:11]]
CRYPTO_PAIRS=[
    {"name":"BTC/USD","symbol":"BTC/USD","p":67000,"d":0},
    {"name":"ETH/USD","symbol":"ETH/USD","p":3500,"d":2},
    {"name":"BNB/USD","symbol":"BNB/USD","p":420,"d":2},
    {"name":"SOL/USD","symbol":"SOL/USD","p":180,"d":2},
    {"name":"XRP/USD","symbol":"XRP/USD","p":0.62,"d":4},
    {"name":"ADA/USD","symbol":"ADA/USD","p":0.45,"d":4},
    {"name":"DOGE/USD","symbol":"DOGE/USD","p":0.18,"d":5},
    {"name":"LTC/USD","symbol":"LTC/USD","p":95,"d":2},
]
STOCKS_PAIRS=[
    {"name":"Apple","symbol":"AAPL","p":189,"d":2},
    {"name":"Tesla","symbol":"TSLA","p":245,"d":2},
    {"name":"NVIDIA","symbol":"NVDA","p":875,"d":2},
    {"name":"Amazon","symbol":"AMZN","p":185,"d":2},
    {"name":"Google","symbol":"GOOGL","p":165,"d":2},
    {"name":"Microsoft","symbol":"MSFT","p":415,"d":2},
    {"name":"Meta","symbol":"META","p":510,"d":2},
    {"name":"Netflix","symbol":"NFLX","p":625,"d":2},
]
ALL_PAIRS={p["name"]:p for p in FOREX_PAIRS+OTC_PAIRS+CRYPTO_PAIRS+STOCKS_PAIRS}
TIMEFRAMES={"1":"1 хв","3":"3 хв","5":"5 хв","15":"15 хв","30":"30 хв","60":"1 год"}
CRYPTO_TF={"5":"5 хв","15":"15 хв","30":"30 хв","60":"1 год","240":"4 год"}
STOCKS_TF={"5":"5 хв","15":"15 хв","30":"30 хв","60":"1 год"}

_lock=threading.Lock()
def load_stats():
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE) as f: return json.load(f)
    except: pass
    return {}
def save_stats(d):
    with _lock:
        try:
            with open(STATS_FILE,"w") as f: json.dump(d,f)
        except: pass
all_stats=load_stats()

_rl_last = {}
_rl_count = {}
RATE_SEC = 3; RATE_MIN = 20
MAX_USERS = 500; MAX_PAIRS = 50

def check_rate_limit(cid):
    now = time.time(); k = str(cid)
    if now - _rl_last.get(k, 0) < RATE_SEC: return False
    cnt, win = _rl_count.get(k, (0, now))
    if now - win > 60: cnt, win = 0, now
    if cnt >= RATE_MIN: return False
    _rl_last[k] = now; _rl_count[k] = (cnt+1, win)
    return True

def get_stats(cid):
    k=str(cid)
    if k not in all_stats:
        if len(all_stats) >= MAX_USERS:
            oldest = min(all_stats, key=lambda x: all_stats[x].get("total", 0))
            del all_stats[oldest]
        all_stats[k]={"total":0,"wins":0,"losses":0,"streak":0,"pairs":{}}
    return all_stats[k]

def save_user_stats(): save_stats(all_stats)

def ema(a,p):
    if len(a)<p: return a[-1] if a else 0
    k=2/(p+1); v=sum(a[:p])/p
    for x in a[p:]: v=x*k+v*(1-k)
    return v

def calc_rsi(c,p=14):
    if len(c)<p+1: return 50
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    l=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    ag=sum(g[-p:])/p; al=sum(l[-p:])/p
    return round(100-100/(1+ag/al),1) if al else 100

def calc_macd(c):
    if len(c)<26: return 0,0
    ml=ema(c,12)-ema(c,26)
    mv=[ema(c[:i],12)-ema(c[:i],26) for i in range(26,len(c)+1)]
    sig=ema(mv,9) if len(mv)>=9 else ml
    return ml, ml-sig

def calc_stoch(c,h,l,k=14):
    if len(c)<k: return 50,50
    hh=max(h[-k:]); ll=min(l[-k:])
    kv=round((c[-1]-ll)/(hh-ll)*100,1) if hh!=ll else 50
    return kv, kv

def calc_bb(c,p=20):
    if len(c)<p: return 50
    s=sum(c[-p:])/p; std=(sum((x-s)**2 for x in c[-p:])/p)**0.5
    up=s+2*std; lo=s-2*std
    return round(max(0,min(100,(c[-1]-lo)/max(1e-9,up-lo)*100)),1)

def calc_willr(c,h,l,p=14):
    if len(c)<p: return -50
    hh=max(h[-p:]); ll=min(l[-p:])
    return round((hh-c[-1])/max(1e-9,hh-ll)*-100,1)

def calc_stc(c,cy=10,fa=23,sl=50):
    if len(c)<sl+cy: return None
    ml=[ema(c[:i],fa)-ema(c[:i],sl) for i in range(sl,len(c)+1)]
    if len(ml)<cy: return None
    hh=max(ml[-cy:]); ll=min(ml[-cy:])
    return round((ml[-1]-ll)/max(1e-9,hh-ll)*100,1)

def calc_adx(c,h,l,p=14):
    if len(c)<p+2: return 0
    trs,pm,nm=[],[],[]
    for i in range(1,len(c)):
        trs.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
        up=h[i]-h[i-1]; dn=l[i-1]-l[i]
        pm.append(up if up>dn and up>0 else 0)
        nm.append(dn if dn>up and dn>0 else 0)
    atr=sum(trs[-p:])/p
    if not atr: return 0
    pdi=sum(pm[-p:])/p/atr*100; ndi=sum(nm[-p:])/p/atr*100
    return round(abs(pdi-ndi)/max(1e-9,pdi+ndi)*100)

def calc_atr(c,h,l,p=14):
    if len(c)<2: return 0
    tr=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return sum(tr[-p:])/min(p,len(tr)) if tr else 0

def calc_momentum(c,p=10):
    if len(c)<p+1: return 0
    return round((c[-1]-c[-p-1])/c[-p-1]*100,3) if c[-p-1] else 0

def calc_heikin_ashi(o,c,h,l):
    if len(c)<3: return 0,""
    ha_c=[(o[i]+h[i]+l[i]+c[i])/4 for i in range(len(c))]
    ha_o=[0]*len(c); ha_o[0]=(o[0]+c[0])/2
    for i in range(1,len(c)): ha_o[i]=(ha_o[i-1]+ha_c[i-1])/2
    ha_h=[max(h[i],ha_o[i],ha_c[i]) for i in range(len(c))]
    ha_l=[min(l[i],ha_o[i],ha_c[i]) for i in range(len(c))]
    bull=sum(1 for i in range(-3,0) if ha_c[i]>ha_o[i])
    bear=sum(1 for i in range(-3,0) if ha_c[i]<ha_o[i])
    body=abs(ha_c[-1]-ha_o[-1])
    no_lo=(min(ha_c[-1],ha_o[-1])-ha_l[-1])<body*0.1
    no_hi=(ha_h[-1]-max(ha_c[-1],ha_o[-1]))<body*0.1
    if bull==3 and no_lo: return 1,"🔥 HA: 3 бичячі без тіні"
    if bear==3 and no_hi: return -1,"🔥 HA: 3 ведмежі без тіні"
    if bull>=2 and ha_c[-1]>ha_o[-1]: return 1,f"HA: {bull} бичячі ▲"
    if bear>=2 and ha_c[-1]<ha_o[-1]: return -1,f"HA: {bear} ведмежі ▼"
    if ha_c[-1]>ha_o[-1]: return 1,"HA: бичяча свічка"
    if ha_c[-1]<ha_o[-1]: return -1,"HA: ведмежа свічка"
    return 0,"HA: нейтраль"

def calc_parabolic_sar(h,l,af0=0.02,afm=0.2):
    if len(h)<5: return 0,""
    bull=l[0]<l[1]; sar=l[0] if bull else h[0]; ep=h[0] if bull else l[0]; af=af0
    prev_bull=bull
    for i in range(1,len(h)):
        prev_bull=bull
        sar=sar+af*(ep-sar)
        if bull:
            sar=min(sar,l[i-1],l[i-2] if i>=2 else l[i-1])
            if l[i]<sar: bull=False; sar=ep; ep=l[i]; af=af0
            elif h[i]>ep: ep=h[i]; af=min(af+af0,afm)
        else:
            sar=max(sar,h[i-1],h[i-2] if i>=2 else h[i-1])
            if h[i]>sar: bull=True; sar=ep; ep=h[i]; af=af0
            elif l[i]<ep: ep=l[i]; af=min(af+af0,afm)
    fresh=bull!=prev_bull
    if fresh and bull: return 1,"🔥 PSAR: свіжий розворот ▲"
    if fresh and not bull: return -1,"🔥 PSAR: свіжий розворот ▼"
    return (1,"PSAR: бичячий ▲") if bull else (-1,"PSAR: ведмежий ▼")

def calc_fibonacci(h,l,c,lb=30):
    if len(h)<lb: lb=len(h)
    rh=max(h[-lb:]); rl=min(l[-lb:]); diff=rh-rl
    if diff<1e-9: return 0,"",[]
    fibs={0.236:rh-diff*0.236,0.382:rh-diff*0.382,
          0.500:rh-diff*0.500,0.618:rh-diff*0.618,0.786:rh-diff*0.786}
    price=c[-1]; atr=calc_atr(c,h,l); zone=max(atr*0.8,diff*0.02)
    for lvl,fp in sorted(fibs.items()):
        if abs(price-fp)<zone:
            up=c[-1]>c[-3] if len(c)>=3 else False
            if up: return 1,f"Fib {lvl:.3f} підтримка ▲",list(fibs.values())
            else:  return -1,f"Fib {lvl:.3f} опір ▼",list(fibs.values())
    return 0,"",list(fibs.values())

def calc_support_resistance(c,h,l,n=3):
    if len(c)<10: return [],[]
    sup=[]; res=[]
    for i in range(2,len(l)-2):
        if l[i]<l[i-1] and l[i]<l[i-2] and l[i]<l[i+1] and l[i]<l[i+2]: sup.append(l[i])
        if h[i]>h[i-1] and h[i]>h[i-2] and h[i]>h[i+1] and h[i]>h[i+2]: res.append(h[i])
    def cluster(lv,tol=0.002):
        if not lv: return []
        lv=sorted(set(lv)); r=[lv[0]]
        for v in lv[1:]:
            if abs(v-r[-1])/max(1e-9,r[-1])>tol: r.append(v)
        return r[-n:]
    return cluster(sup),cluster(res)[:n]

def sr_signal(price,sup,res,atr):
    if not atr: return 0,""
    z=atr*0.5
    for s in sup:
        if abs(price-s)<z: return 1,f"Відскок від підтримки"
    for r in res:
        if abs(price-r)<z: return -1,f"Відскок від опору"
    for r in res:
        if price>r and price-r<z*2: return 1,"Пробій опору ▲"
    for s in sup:
        if price<s and s-price<z*2: return -1,"Пробій підтримки ▼"
    return 0,""

def get_session():
    h=datetime.now(timezone.utc).hour
    if 7<=h<9:    return "Лондон відкриття 🟢","excellent",1.15
    elif 9<=h<12: return "Лондон+NY 🟢","excellent",1.20
    elif 12<=h<16: return "Нью-Йорк 🟡","good",1.10
    elif 16<=h<18: return "NY закриття 🟡","average",0.95
    elif 18<=h<21: return "Між сесіями 🔴","poor",0.80
    elif 21<=h<23: return "Токіо 🟡","average",0.90
    else:          return "Нічна сесія 🔴","poor",0.75

_candle_cache = {}
TF_CACHE_SEC = {"1":30,"3":90,"5":150,"15":300,"30":600,"60":1200,"240":2400}

def get_candles(symbol,tf,count=100):
    cache_key = f"{symbol}_{tf}"
    ttl = TF_CACHE_SEC.get(tf, 150)
    if cache_key in _candle_cache:
        ts, c, h, l, o = _candle_cache[cache_key]
        if time.time() - ts < ttl:
            return c, h, l, o
    tf_map={"1":"1min","3":"3min","5":"5min","15":"15min","30":"30min","60":"1h","240":"4h"}
    interval=tf_map.get(tf,"5min")
    try:
        url=f"{TWELVE_URL}/time_series?symbol={symbol}&interval={interval}&outputsize={count}&apikey={TWELVE_KEY}&format=JSON"
        r=requests.get(url,timeout=12); d=r.json()
        if d.get("status")=="error" or not d.get("values"): return [],[],[],[]
        vals=list(reversed(d["values"]))
        c=[float(v["close"]) for v in vals]
        h=[float(v["high"]) for v in vals]
        l=[float(v["low"]) for v in vals]
        o=[float(v["open"]) for v in vals]
        _candle_cache[cache_key] = (time.time(), c, h, l, o)
        return c,h,l,o
    except: return [],[],[],[]

def get_price(symbol,fb):
    try:
        r=requests.get(f"{TWELVE_URL}/price?symbol={symbol}&apikey={TWELVE_KEY}",timeout=5)
        p=r.json().get("price")
        if p: return float(p)
    except: pass
    return fb

def generate_signal(pair_name,tf):
    m=ALL_PAIRS.get(pair_name,FOREX_PAIRS[0])
    is_otc="OTC" in pair_name
    c,h,l,o=get_candles(m["symbol"],tf,100)
    real=len(c)>=20
    live=get_price(m["symbol"],m["p"])
    if not real:
        seed=sum(ord(x) for x in pair_name)+(int(tf) if tf.isdigit() else 5)*7+int(time.time()//300)
        def sr(i): v=math.sin(seed*1.1+i*0.7)*43758.5453; return v-math.floor(v)
        base=live; cv=[base]; hv=[base]; lv=[base]; ov=[base]
        for i in range(1,80):
            trend=(sr(i+5)-0.495)*0.003; vol=sr(i+10)*0.002+0.0005
            op=cv[-1]; cl=op*(1+trend+(sr(i+20)-0.5)*vol)
            hi=max(op,cl)*(1+sr(i+30)*0.001); lo=min(op,cl)*(1-sr(i+40)*0.001)
            ov.append(op); cv.append(cl); hv.append(hi); lv.append(lo)
        c,h,l,o=cv,hv,lv,ov
    rsi      = calc_rsi(c)
    macd,mh  = calc_macd(c)
    e9=ema(c,9); e21=ema(c,21); e50=ema(c,50)
    k_val,_  = calc_stoch(c,h,l)
    bb       = calc_bb(c)
    willr    = calc_willr(c,h,l)
    stc      = calc_stc(c)
    adx      = calc_adx(c,h,l)
    atr      = calc_atr(c,h,l)
    mom      = calc_momentum(c)
    ha_val, ha_lbl         = calc_heikin_ashi(o,c,h,l)
    psar_val, psar_lbl     = calc_parabolic_sar(h,l)
    fib_val, fib_lbl, _    = calc_fibonacci(h,l,c)
    sup, res               = calc_support_resistance(c,h,l)
    sr_val, sr_lbl         = sr_signal(live,sup,res,atr)
    sess_name, sess_q, sess_mult = get_session()
    def candle_pat():
        if len(c)<3: return 0,""
        b2=abs(c[-2]-o[-2]); r2=max(1e-9,h[-2]-l[-2])
        b1=abs(c[-1]-o[-1]); r1=max(1e-9,h[-1]-l[-1])
        doji=b2/r2<0.15
        engb=(c[-2]<o[-2] and c[-1]>o[-1] and c[-1]>o[-2] and o[-1]<c[-2])
        engbb=(c[-2]>o[-2] and c[-1]<o[-1] and c[-1]<o[-2] and o[-1]>c[-2])
        t3b=all(c[-(i+1)]>o[-(i+1)] and c[-(i+1)]>c[-(i+2)] for i in range(3)) if len(c)>=4 else False
        t3bb=all(c[-(i+1)]<o[-(i+1)] and c[-(i+1)]<c[-(i+2)] for i in range(3)) if len(c)>=4 else False
        if engb: return 1,"🕯 Бичяче поглинання"
        if engbb: return -1,"🕯 Ведмеже поглинання"
        if t3b: return 1,"🕯 3 бичячі свічки"
        if t3bb: return -1,"🕯 3 ведмежі свічки"
        if doji and c[-1]>o[-1]: return 1,"🕯 Доджі→BUY"
        if doji and c[-1]<o[-1]: return -1,"🕯 Доджі→SELL"
        return 0,""
    pat_val, pat_lbl = candle_pat()
    votes=[]
    def v(n,val,lbl,w=1.0): votes.append({"n":n,"v":val,"l":lbl,"w":w})
    if rsi<25:    v("RSI",1,f"RSI {rsi} — сильна перепроданість 🔥",2.5)
    elif rsi>75:  v("RSI",-1,f"RSI {rsi} — сильна перекупленість 🔥",2.5)
    elif rsi<40:  v("RSI",1,f"RSI {rsi} — перепроданість",2.0)
    elif rsi>60:  v("RSI",-1,f"RSI {rsi} — перекупленість",2.0)
    elif rsi<48:  v("RSI",1,f"RSI {rsi} — бичачий нахил",1.0)
    elif rsi>52:  v("RSI",-1,f"RSI {rsi} — ведмежий нахил",1.0)
    else:         v("RSI",0,f"RSI {rsi} — нейтраль",0.3)
    if macd>0 and mh>0:   v("MACD",1,"MACD: лінія+гіст ▲ ✅",2.0)
    elif macd<0 and mh<0: v("MACD",-1,"MACD: лінія+гіст ▼ ✅",2.0)
    elif mh>0:            v("MACD",1,"MACD: гіст зростає",1.0)
    elif mh<0:            v("MACD",-1,"MACD: гіст падає",1.0)
    else:                 v("MACD",0,"MACD нейтраль",0.3)
    if e9>e21*1.0002:   v("EMA9/21",1,"EMA9 > EMA21 ▲",2.0)
    elif e9<e21*0.9998: v("EMA9/21",-1,"EMA9 < EMA21 ▼",2.0)
    else:               v("EMA9/21",0,"EMA9 ≈ EMA21",0.3)
    if live>e50*1.001:   v("EMA50",1,"Ціна вище EMA50",1.5)
    elif live<e50*0.999: v("EMA50",-1,"Ціна нижче EMA50",1.5)
    if k_val<20:   v("Stoch",1,f"Stoch {k_val} — перепроданість ✅",2.0)
    elif k_val>80: v("Stoch",-1,f"Stoch {k_val} — перекупленість ✅",2.0)
    elif k_val<45: v("Stoch",1,f"Stoch {k_val} — BUY зона",1.0)
    elif k_val>55: v("Stoch",-1,f"Stoch {k_val} — SELL зона",1.0)
    if bb<10:     v("BB",1,"BB нижня смуга BUY 🔥",2.0)
    elif bb>90:   v("BB",-1,"BB верхня смуга SELL 🔥",2.0)
    elif bb<25:   v("BB",1,f"BB нижня зона {bb}%",1.0)
    elif bb>75:   v("BB",-1,f"BB верхня зона {bb}%",1.0)
    if willr<-85:   v("W%R",1,f"W%R {willr} — сильна перепроданість 🔥",2.0)
    elif willr>-15: v("W%R",-1,f"W%R {willr} — сильна перекупленість 🔥",2.0)
    elif willr<-60: v("W%R",1,f"W%R {willr} — перепроданість",1.0)
    else:           v("W%R",-1,f"W%R {willr} — перекупленість",1.0)
    if stc is not None:
        if stc<15:   v("STC",1,f"STC {stc} — сильний BUY 🔥🔥",3.5)
        elif stc>85: v("STC",-1,f"STC {stc} — сильний SELL 🔥🔥",3.5)
        elif stc<30: v("STC",1,f"STC {stc} — BUY зона 🔥",2.5)
        elif stc>70: v("STC",-1,f"STC {stc} — SELL зона 🔥",2.5)
        elif stc<50: v("STC",1,f"STC {stc} — зростає",1.0)
        else:        v("STC",-1,f"STC {stc} — падає",1.0)
    if mom>0.2:    v("Momentum",1,f"Mom +{mom}% бичачий",1.5)
    elif mom<-0.2: v("Momentum",-1,f"Mom {mom}% ведмежий",1.5)
    if pat_val!=0: v("Патерн",pat_val,pat_lbl,2.0)
    if sr_val!=0:  v("S/R",sr_val,sr_lbl,2.5)
    if ha_val!=0:
        strong="🔥" in ha_lbl
        v("Heikin Ashi",ha_val,ha_lbl,3.5 if strong else 2.5)
    if psar_val!=0:
        fresh="свіжий" in psar_lbl or "розворот" in psar_lbl
        v("Parab SAR",psar_val,psar_lbl,3.0 if fresh else 2.0)
    if fib_val!=0: v("Fibonacci",fib_val,fib_lbl,2.0)
    tf_map_w={
        "1":{"Heikin Ashi":1.8,"Parab SAR":1.6,"STC":1.4,"Stoch":1.4,"Momentum":1.5,"MACD":0.6,"EMA50":0.4},
        "3":{"Heikin Ashi":1.6,"Parab SAR":1.5,"STC":1.5,"EMA9/21":1.3,"Stoch":1.3,"Momentum":1.4,"Fibonacci":1.3,"MACD":0.8,"EMA50":0.6},
        "5":{"Heikin Ashi":1.6,"Parab SAR":1.5,"STC":1.5,"EMA9/21":1.3,"Stoch":1.3,"Momentum":1.4,"Fibonacci":1.3,"MACD":0.8,"EMA50":0.6},
        "15":{"EMA50":1.5,"MACD":1.3,"S/R":1.5,"RSI":1.2,"Fibonacci":1.4,"Parab SAR":1.2},
        "30":{"EMA50":1.5,"MACD":1.3,"S/R":1.5,"RSI":1.2,"Fibonacci":1.4},
        "60":{"EMA50":1.6,"MACD":1.4,"S/R":1.6,"RSI":1.3,"Fibonacci":1.5},
    }
    wm=tf_map_w.get(tf,{})
    for vt in votes:
        if vt["n"] in wm: vt["w"]*=wm[vt["n"]]
    buy_w=sum(x["w"] for x in votes if x["v"]==1)
    sell_w=sum(x["w"] for x in votes if x["v"]==-1)
    bc=sum(1 for x in votes if x["v"]==1)
    sc=sum(1 for x in votes if x["v"]==-1)
    tot=buy_w+sell_w
    is_buy=buy_w>=sell_w
    dom=max(buy_w,sell_w)
    ratio=dom/max(1e-9,tot)
    top_ns=["STC","RSI","EMA9/21","Stoch","Heikin Ashi","Parab SAR","Fibonacci"]
    top_vs=[next((x["v"] for x in votes if x["n"]==n),0) for n in top_ns]
    top_a=[v for v in top_vs if v!=0]
    c_agree=sum(1 for v in top_a if (v==1)==is_buy)
    consensus=f"{c_agree}/{len(top_a)}" if top_a else "—"
    adx_ok=adx>=20
    adx_b=min(12,adx//3) if adx_ok else -5
    cons_b=round(c_agree/max(1,len(top_a))*12)
    pat_b=5 if (pat_val==1 and is_buy) or (pat_val==-1 and not is_buy) else 0
    sr_b=6 if (sr_val==1 and is_buy) or (sr_val==-1 and not is_buy) else 0
    tf_b={"1":0,"3":6,"5":5,"15":3,"30":2,"60":1}.get(tf,0)
    ha_b=5 if (ha_val==1 and is_buy) or (ha_val==-1 and not is_buy) else 0
    psar_b=5 if (psar_val==1 and is_buy) or (psar_val==-1 and not is_buy) else 0
    acc_raw=round(54+ratio*26+adx_b+cons_b+pat_b+sr_b+tf_b+ha_b+psar_b)
    acc=min(94,max(68,round(acc_raw*sess_mult)))
    stc_v = next((x["v"] for x in votes if x["n"]=="STC"), 0)
    rsi_v = next((x["v"] for x in votes if x["n"]=="RSI"), 0)
    psar_v= next((x["v"] for x in votes if x["n"]=="Parab SAR"), 0)
    ha_v  = next((x["v"] for x in votes if x["n"]=="Heikin Ashi"), 0)
    stc_blocks = (stc is not None) and ((stc>=85 and is_buy) or (stc<=15 and not is_buy))
    rsi_extreme = (rsi>=75 and is_buy) or (rsi<=25 and not is_buy)
    psar_against= psar_v!=0 and (psar_v==1)!=is_buy and ("розворот" in psar_lbl or "свіжий" in psar_lbl)
    ha_against  = ha_v!=0 and (ha_v==1)!=is_buy and "🔥" in ha_lbl
    block_reasons=[]
    if stc_blocks:   block_reasons.append(f"STC={stc:.0f} {'перекупленість' if is_buy else 'перепроданість'}")
    if rsi_extreme:  block_reasons.append(f"RSI={rsi} {'перекупленість' if is_buy else 'перепроданість'}")
    if psar_against: block_reasons.append("PSAR свіжий розворот проти")
    if ha_against:   block_reasons.append("HA сильний сигнал проти")
    hard_block = stc_blocks or (rsi_extreme and (psar_against or ha_against)) or len(block_reasons)>=2
    if hard_block:
        strength="⛔ НЕ ТОРГУВАТИ"; blocked=True; acc=min(acc,60)
    elif not adx_ok and ratio<0.65: strength="⛔ ФІЛЬТР ADX"; blocked=True
    elif ratio<0.58: strength="⚠️ СЛАБКИЙ"; blocked=False
    elif ratio<0.68: strength="✅ СЕРЕДНІЙ"; blocked=False
    elif ratio<0.80: strength="🔥 СИЛЬНИЙ"; blocked=False
    else:            strength="🔥🔥 ДУЖЕ СИЛЬНИЙ"; blocked=False
    d_=m["d"]
    if atr==0: atr=live*0.001
    tp_m={"1":1.3,"3":1.5,"5":1.7,"15":2.0,"30":2.5,"60":3.0}.get(tf,1.7)
    sl_m={"1":1.0,"3":1.1,"5":1.2,"15":1.4,"30":1.6,"60":2.0}.get(tf,1.2)
    tp=round(live+atr*tp_m,d_) if is_buy else round(live-atr*tp_m,d_)
    sl=round(live-atr*sl_m,d_) if is_buy else round(live+atr*sl_m,d_)
    rr=round(tp_m/sl_m,1)
    return {"is_buy":is_buy,"acc":acc,"strength":strength,"blocked":blocked,
            "live":live,"tp":tp,"sl":sl,"rr":rr,"adx":adx,"adx_ok":adx_ok,
            "rsi":rsi,"stc":stc,"ha_lbl":ha_lbl,"psar_lbl":psar_lbl,
            "fib_lbl":fib_lbl,"sr_lbl":sr_lbl,"pat_lbl":pat_lbl,
            "votes":votes,"bc":bc,"sc":sc,"buy_w":round(buy_w,1),"sell_w":round(sell_w,1),
            "consensus":consensus,"sess":sess_name,"sess_q":sess_q,
            "real":real,"is_otc":is_otc,"block_reasons":block_reasons}

def generate_chart(pair, tf, c, h, l, o, sig):
    n = min(40, len(c))
    c=c[-n:]; h=h[-n:]; l=l[-n:]; o=o[-n:]
    x = list(range(n))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7),
        gridspec_kw={'height_ratios': [3, 1]}, facecolor='#0d1424')
    ax1.set_facecolor('#0d1424')
    for i in range(n):
        col = '#00ff88' if c[i] >= o[i] else '#ff3366'
        ax1.bar(i, abs(c[i]-o[i]), bottom=min(c[i],o[i]), width=0.7, color=col, zorder=3)
        ax1.plot([i,i], [l[i],h[i]], color=col, linewidth=1, zorder=2)
    def ema_arr(data, p):
        if len(data)<p: return [data[0]]*len(data)
        k=2/(p+1); v=sum(data[:p])/p; res=[v]
        for x in data[p:]: v=x*k+v*(1-k); res.append(v)
        full = [data[0]]*(p-1)+res
        return full[-len(data):]
    e9=ema_arr(c,9); e21=ema_arr(c,21)
    ax1.plot(x, e9,  color='#00d4ff', linewidth=1.3, label='EMA9',  zorder=4)
    ax1.plot(x, e21, color='#ffcc00', linewidth=1.3, label='EMA21', zorder=4)
    def psar_pts(h,l,af0=0.02,afm=0.2):
        if len(h)<3: return []
        bull=l[0]<l[1]; sar=l[0] if bull else h[0]; ep=h[0] if bull else l[0]; af=af0; pts=[]
        for i in range(1,len(h)):
            sar=sar+af*(ep-sar)
            if bull:
                sar=min(sar,l[i-1],l[i-2] if i>=2 else l[i-1])
                if l[i]<sar: bull=False; sar=ep; ep=l[i]; af=af0
                elif h[i]>ep: ep=h[i]; af=min(af+af0,afm)
            else:
                sar=max(sar,h[i-1],h[i-2] if i>=2 else h[i-1])
                if h[i]>sar: bull=True; sar=ep; ep=h[i]; af=af0
                elif l[i]<ep: ep=l[i]; af=min(af+af0,afm)
            pts.append((sar,bull))
        return pts
    for i,(sv,sb) in enumerate(psar_pts(h,l)):
        ax1.scatter(i+1,sv,color='#00ff88' if sb else '#ff3366',s=8,zorder=5,marker='.')
    is_buy=sig['is_buy']; rng=max(h[-1]-l[-1],0.0001)
    ay = l[-1]-rng*1.2 if is_buy else h[-1]+rng*1.2
    ax1.annotate('', xy=(n-1, l[-1] if is_buy else h[-1]),
        xytext=(n-1, ay),
        arrowprops=dict(arrowstyle='->', color='#00ff88' if is_buy else '#ff3366', lw=2.5, mutation_scale=22))
    ax1.text(n-1, ay-rng*0.8 if is_buy else ay+rng*0.8,
             f"{'BUY ▲' if is_buy else 'SELL ▼'}  {sig['acc']}%",
             color='#00ff88' if is_buy else '#ff3366',
             fontsize=11, fontweight='bold', ha='center', zorder=10)
    dp=sig.get('dp',5)
    ax1.axhline(sig['tp'],color='#00ff88',linewidth=1,linestyle='--',alpha=0.7)
    ax1.axhline(sig['sl'],color='#ff3366',linewidth=1,linestyle='--',alpha=0.7)
    ax1.text(0.5,sig['tp'],f"TP {sig['tp']:.{dp}f}",color='#00ff88',fontsize=8,va='bottom')
    ax1.text(0.5,sig['sl'],f"SL {sig['sl']:.{dp}f}",color='#ff3366',fontsize=8,va='top')
    p_bb=20
    if len(c)>=p_bb:
        sma=[sum(c[max(0,i-p_bb):i+1])/min(i+1,p_bb) for i in range(len(c))]
        std_v=[((sum((c[max(0,j-p_bb):j+1][k]-sma[j])**2 for k in range(min(j+1,p_bb)))/min(j+1,p_bb))**0.5) for j in range(len(c))]
        bb_up=[sma[i]+2*std_v[i] for i in range(len(c))]
        bb_lo=[sma[i]-2*std_v[i] for i in range(len(c))]
        ax1.plot(x,bb_up,color='#3a5a80',linewidth=0.8,linestyle=':',alpha=0.6)
        ax1.plot(x,bb_lo,color='#3a5a80',linewidth=0.8,linestyle=':',alpha=0.6)
        ax1.fill_between(x,bb_up,bb_lo,alpha=0.04,color='#0088ff')
    tf_lbl={"1":"1хв","3":"3хв","5":"5хв","15":"15хв","30":"30хв","60":"1год"}.get(str(tf),"5хв")
    ax1.set_title(f"⚡ SIGNAL AI  {pair} | {tf_lbl} | Точність {sig['acc']}%",
                  color='#00d4ff', fontsize=13, fontweight='bold', pad=10)
    ax1.legend(loc='upper left',facecolor='#090e1a',labelcolor='white',fontsize=8,framealpha=0.7)
    ax1.tick_params(colors='#6a8ab0'); ax1.yaxis.tick_right()
    for sp in ['top','right','bottom','left']: ax1.spines[sp].set_color('#1a2a40')
    ax1.set_xlim(-1, n+1)
    ax2.set_facecolor('#0d1424')
    rsi_v=[]
    for i in range(len(c)):
        sub=c[max(0,i-14):i+1]
        if len(sub)<2: rsi_v.append(50); continue
        g=[max(sub[j]-sub[j-1],0) for j in range(1,len(sub))]
        lo=[max(sub[j-1]-sub[j],0) for j in range(1,len(sub))]
        ag=sum(g)/len(g); al=sum(lo)/len(lo)
        rsi_v.append(round(100-100/(1+ag/al),1) if al else 100)
    rsi_v=rsi_v[-n:]
    ax2.plot(x,rsi_v,color='#0088ff',linewidth=1.5,zorder=3)
    ax2.axhline(70,color='#ff3366',linewidth=0.8,linestyle='--',alpha=0.5)
    ax2.axhline(30,color='#00ff88',linewidth=0.8,linestyle='--',alpha=0.5)
    ax2.axhline(50,color='#3a5a80',linewidth=0.5,alpha=0.4)
    ax2.fill_between(x,rsi_v,50,where=[r>50 for r in rsi_v],alpha=0.15,color='#00ff88')
    ax2.fill_between(x,rsi_v,50,where=[r<50 for r in rsi_v],alpha=0.15,color='#ff3366')
    ax2.set_ylim(0,100); ax2.set_ylabel('RSI',color='#6a8ab0',fontsize=9)
    ax2.tick_params(colors='#6a8ab0'); ax2.set_xlim(-1,n+1)
    for sp in ['top','right','bottom','left']: ax2.spines[sp].set_color('#1a2a40')
    plt.tight_layout(pad=0.5)
    buf=io.BytesIO()
    plt.savefig(buf,format='png',dpi=130,bbox_inches='tight',facecolor='#0d1424',edgecolor='none')
    buf.seek(0); plt.close()
    return buf

def bar(val,n=10):
    f=round(max(0,min(100,val))/100*n)
    return "▰"*f+"▱"*(n-f)

# ══ ФОРМАТУВАННЯ — З ЧАСОМ ВХОДУ ══════════════════════
def format_signal(pair, tf, d):
    now_dt = datetime.now(KYIV)
    tf_lbl = TIMEFRAMES.get(tf, CRYPTO_TF.get(tf, STOCKS_TF.get(tf, tf+"хв")))

    # ── РОЗРАХУНОК ЧАСУ ВХОДУ ──────────────────────────
    entry_time, exit_time, closes_in_str = get_entry_time(tf)
    # ────────────────────────────────────────────────────

    is_buy   = d["is_buy"]
    arrow    = "⬆️" if is_buy else "⬇️"
    dir_txt  = "ВВЕРХ" if is_buy else "ВНИЗ"
    dir_em   = "🟢" if is_buy else "🔴"
    acc      = d["acc"]
    acc_em   = "🔥" if acc>=86 else "✅" if acc>=78 else "⚠️"
    src      = "🔴 Live" if d["real"] else "⚙️ Розрахунок"

    buy_r  = d["buy_w"] / (max(0.1, d["buy_w"]+d["sell_w"]))
    t_pct  = round(buy_r*100) if is_buy else round((1-buy_r)*100)
    t_str  = ("Слабий" if t_pct<60 else
              "Середній" if t_pct<75 else
              "Сильний" if t_pct<88 else "Дуже сильний")

    target    = 1 if is_buy else -1
    top_v     = [x for x in d["votes"] if x["v"]==target]
    top_v.sort(key=lambda x: -x["w"])
    top3      = top_v[:4]
    top_lines = "\n".join(f"✅ {x['l']}" for x in top3) if top3 else "⚪ Слабкий консенсус"

    new_inds = []
    if d.get("ha_lbl"):   new_inds.append(f"🕯 {d['ha_lbl']}")
    if d.get("psar_lbl"): new_inds.append(f"📍 {d['psar_lbl']}")
    if d.get("fib_lbl"):  new_inds.append(f"📐 {d['fib_lbl']}")
    if d.get("sr_lbl"):   new_inds.append(f"📊 S/R: {d['sr_lbl']}")
    if d.get("pat_lbl"):  new_inds.append(f"🕯 {d['pat_lbl']}")
    new_ind_txt = ("\n".join(new_inds)+"\n\n") if new_inds else ""

    stc = d.get("stc")
    stc_line = ""
    if stc is not None:
        si = "🟢" if stc<25 else "🔴" if stc>75 else "🟡" if stc<50 else "🟠"
        sz = ("Перепроданість" if stc<25 else
              "Перекупленість" if stc>75 else
              "Зростає" if stc<50 else "Падає")
        stc_line = f"{si} STC: {stc} — {sz}\n"

    adx_em = "✅" if d["adx_ok"] else "⚠️"

    reasons = d.get("block_reasons", [])
    if d.get("blocked") and reasons:
        block_warn = "\n⛔ *НЕ ТОРГУВАТИ*\n" + "\n".join(f"• {r}" for r in reasons) + "\n"
    elif d.get("blocked"):
        block_warn = "\n⛔ *СИГНАЛ ЗАБЛОКОВАНИЙ — НЕ ТОРГУВАТИ*\n"
    else:
        block_warn = ""

    mm = mm_text(acc)
    has_news, news_warn = check_news_filter(pair)
    news_line = f"\n{news_warn}\n" if has_news else ""

    lines = [
        "╔══ ⚡ *SIGNAL AI v2.0* ══╗",
        "",
        f"🏷 *{pair}*  ⏱ {tf_lbl}  {src}",
        f"📍 {d['sess']}",
        "",
        # ── ЧАС ВХОДУ ──────────────────────────────────
        f"⏰ Свічка закриється через: *{closes_in_str}*",
        f"🚀 *Час входу: {entry_time}*",
        f"🏁 *Час виходу: {exit_time}*",
        # ────────────────────────────────────────────────
        "",
        f"📈 *Сила тренду* — {t_str} *{t_pct}%*",
        f"`{bar(t_pct)}`",
        "",
        f"{dir_em} *Напрямок: {arrow} {dir_txt}*",
        "",
        f"{acc_em} Точність: *{acc}%*   {d['strength']}",
        f"ADX: *{d['adx']}* {adx_em}   Консенсус: *{d['consensus']}*",
        f"BUY {d['bc']} ({d['buy_w']})  |  SELL {d['sc']} ({d['sell_w']})",
        block_warn,
        stc_line + new_ind_txt[:-1] if new_ind_txt else stc_line,
        "",
        "🔬 *Сигнали:*",
        top_lines,
        "",
        f"💰 Вхід: `{d['live']}`",
        f"🎯 TP: `{d['tp']}`  🛑 SL: `{d['sl']}`  RR: 1:{d['rr']}",
        "",
        mm,
        news_line,
        "└─────────────────────────┘",
        "⚠️ _Не є фінансовою порадою_",
    ]
    return "\n".join(lines)

def sessions_text():
    now=datetime.now(timezone.utc)
    h=now.hour
    sess=[
        (7,9,"🟢 Лондон відкриття","Висока волатильність, відмінні сигнали"),
        (9,12,"🟢 Лондон + Нью-Йорк","НАЙКРАЩИЙ час — максимальна ліквідність"),
        (12,16,"🟡 Нью-Йорк","Хороша волатильність, торгуй з підтвердженням"),
        (16,18,"🟡 NY закриття","Помірна активність"),
        (18,21,"🔴 Між сесіями","Слабка активність, обережно"),
        (21,23,"🟡 Токіо відкриття","Помірна активність на JPY парах"),
        (23,7,"🔴 Нічна","Низька ліквідність — краще не торгувати"),
    ]
    lines=["⏰ *Торгові сесії (UTC+2)*\n"]
    for sh,eh,name,desc in sess:
        active=(sh<=h<eh) or (sh>eh and (h>=sh or h<eh))
        marker="👉 " if active else "   "
        lines.append(f"{marker}*{name}* ({sh:02d}:00-{eh:02d}:00)\n_{desc}_\n")
    return "\n".join(lines)

def stats_text(cid):
    s=get_stats(cid); t=s["total"]; w=s["wins"]; l=s.get("losses",0)
    wr=round(w/t*100) if t else 0
    st=s.get("streak",0)
    streak_txt=f"🔥 Серія перемог: {st}" if st>0 else(f"❄️ Серія поразок: {abs(st)}" if st<0 else "➖ Нема серії")
    top_pairs=""
    if s.get("pairs"):
        sorted_p=sorted(s["pairs"].items(),key=lambda x:-x[1]["total"])[:3]
        top_pairs="\n\n🏆 *Топ пари:*\n"
        for pn,pd in sorted_p:
            pwr=round(pd["wins"]/pd["total"]*100) if pd["total"] else 0
            top_pairs+=f"• {pn}: {pd['total']} угод, {pwr}% WR\n"
    return (f"📊 *Ваша статистика*\n\n"
            f"Всього: *{t}* угод\n"
            f"Виграші: *{w}* ✅\n"
            f"Програші: *{l}* ❌\n"
            f"Win Rate: *{wr}%*\n"
            f"`{bar(wr)}`\n\n"
            f"{streak_txt}{top_pairs}")

def run_scanner(cid,tf="5"):
    scan=FOREX_PAIRS[:8]+OTC_PAIRS[:5]
    results=[]
    for p in scan:
        try:
            sig=generate_signal(p["name"],tf)
            if sig and sig["acc"]>=82 and not sig.get("blocked"):
                results.append((p["name"],tf,sig))
        except: pass
    if not results:
        try: bot.send_message(cid,"🔍 Сканування завершено\n\nСильних сигналів не знайдено.")
        except: pass
        return
    results.sort(key=lambda x:-x[2]["acc"])
    try:
        bot.send_message(cid,f"🔍 *Знайдено {len(results[:3])} сильних сигнали:*",parse_mode="Markdown")
        for pr,tf2,sig in results[:3]:
            kb=result_kb(pr,tf2)
            bot.send_message(cid,format_signal(pr,tf2,sig),parse_mode="Markdown",reply_markup=kb)
            time.sleep(0.5)
    except: pass

def main_kb():
    kb=InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("📈 FOREX",callback_data="menu_forex"),
           InlineKeyboardButton("🌙 OTC",callback_data="menu_otc"))
    kb.add(InlineKeyboardButton("₿ КРИПТО",callback_data="menu_crypto"),
           InlineKeyboardButton("📊 АКЦІЇ",callback_data="menu_stocks"))
    kb.add(InlineKeyboardButton("🔍 Авто-сканер",callback_data="scanner"),
           InlineKeyboardButton("📊 Статистика",callback_data="stats"))
    kb.add(InlineKeyboardButton("🕐 Сесії",callback_data="sessions"),
           InlineKeyboardButton("ℹ️ Про бота",callback_data="about"))
    return kb

def pairs_kb(pairs,back):
    kb=InlineKeyboardMarkup(row_width=3)
    kb.add(*[InlineKeyboardButton(p["name"],callback_data=f"pair_{p['name']}") for p in pairs])
    kb.add(InlineKeyboardButton("◀️ Назад",callback_data=back))
    return kb

def tf_kb(pair):
    is_crypto=any(pair==p["name"] for p in CRYPTO_PAIRS)
    is_stocks=any(pair==p["name"] for p in STOCKS_PAIRS)
    tfs=CRYPTO_TF if is_crypto else(STOCKS_TF if is_stocks else TIMEFRAMES)
    back="crypto_back" if is_crypto else("stocks_back" if is_stocks else("otc_back" if "OTC" in pair else "forex_back"))
    kb=InlineKeyboardMarkup(row_width=3)
    kb.add(*[InlineKeyboardButton(v,callback_data=f"tf|{pair}|{k}") for k,v in tfs.items()])
    kb.add(InlineKeyboardButton("◀️ Назад",callback_data=back))
    return kb

def result_kb(pair,tf):
    kb=InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("✅ Виграш",callback_data=f"win|{pair}|{tf}"),
           InlineKeyboardButton("❌ Програш",callback_data=f"loss|{pair}|{tf}"))
    kb.add(InlineKeyboardButton("🔄 Новий сигнал",callback_data=f"tf|{pair}|{tf}"),
           InlineKeyboardButton("🏠 Меню",callback_data="main"))
    return kb

def start_kb():
    from telebot.types import ReplyKeyboardMarkup, KeyboardButton
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        KeyboardButton("📈 FOREX"),   KeyboardButton("🌙 OTC"),
        KeyboardButton("₿ КРИПТО"),   KeyboardButton("📊 АКЦІЇ"),
        KeyboardButton("🔍 Сканер"),  KeyboardButton("📊 Статистика"),
        KeyboardButton("🕐 Сесії"),   KeyboardButton("🏠 Меню"),
        KeyboardButton("🔔 Авто-сигнали"), KeyboardButton("📓 Журнал"),
    )
    return kb

_REPLY_MAP = {
    "📈 FOREX":"menu_forex","🌙 OTC":"menu_otc","₿ КРИПТО":"menu_crypto",
    "📊 АКЦІЇ":"menu_stocks","🔍 СКАНЕР":"scanner","📊 СТАТИСТИКА":"stats",
    "🕐 СЕСІЇ":"sessions","🏠 МЕНЮ":"main","🔔 АВТО-СИГНАЛИ":"auto_signals","📓 ЖУРНАЛ":"journal",
}

def send_main(cid,mid=None):
    txt=("╔══ ⚡ *SIGNAL AI v2.0* ══╗\n\n"
         "14 індикаторів для точного аналізу:\n\n"
         "• RSI • MACD • EMA 9/21/50\n"
         "• Williams %R • Stochastic • BB\n"
         "• STC • Momentum • ADX\n"
         "• 🆕 Heikin Ashi • 🆕 Parabolic SAR\n"
         "• 🆕 Fibonacci • 🆕 S/R рівні\n"
         "• 🆕 Торгові сесії\n\n"
         "⏰ Точний час входу до кожного сигналу!\n\n"
         "📡 TwelveData API  |  🎯 Точність: ~82-94%\n\n"
         "💡 *Напиши назву пари:*\n"
         "`EURUSD` • `chfjpy` • `btc` • `AAPL`\n\n"
         "╚══ Або оберіть категорію ══╝")
    if mid:
        try: bot.edit_message_text(txt,cid,mid,parse_mode="Markdown",reply_markup=main_kb()); return
        except: pass
    bot.send_message(cid,txt,parse_mode="Markdown",reply_markup=main_kb())

def do_signal(cid,mid,pair,tf):
    if pair not in ALL_PAIRS:
        try: bot.edit_message_text("❌ Невідома пара",cid,mid,reply_markup=main_kb())
        except: pass
        return
    if tf not in {**TIMEFRAMES,**CRYPTO_TF,**STOCKS_TF}:
        try: bot.edit_message_text("❌ Невідомий таймфрейм",cid,mid,reply_markup=main_kb())
        except: pass
        return
    if not check_rate_limit(cid):
        try: bot.edit_message_text("⏳ Зачекайте кілька секунд",cid,mid,reply_markup=main_kb())
        except: pass
        return
    tf_lbl=TIMEFRAMES.get(tf,CRYPTO_TF.get(tf,tf+"хв"))
    steps=[("⟳ Завантаження даних...","▰▰▰▱▱▱▱▱▱▱ 30%"),
           ("⟳ HA + PSAR + Fibonacci...","▰▰▰▰▰▰▱▱▱▱ 60%"),
           ("⟳ S/R рівні + Сесія...","▰▰▰▰▰▰▰▰▱▱ 80%"),
           ("⟳ Генерую сигнал...","▰▰▰▰▰▰▰▰▰▱ 95%")]
    last=""
    for step,b in steps:
        try:
            txt=f"⚡ *SIGNAL AI v2.0*\n\n{step}\n\n`{pair}` | `{tf_lbl}`\n\n{b}"
            if txt!=last: bot.edit_message_text(txt,cid,mid,parse_mode="Markdown"); last=txt
        except: pass
        time.sleep(0.7)
    sig = None
    try:
        sig = generate_signal(pair,tf)
    except Exception as e:
        print(f"[SIGNAL ERR] {pair} {tf}: {e}")
    if sig is None:
        try: bot.delete_message(cid, mid)
        except: pass
        try:
            bot.send_message(cid,
                f"⚠️ *Помилка аналізу*\n\n`{pair}` | `{tf_lbl}`\n\nСпробуйте ще раз.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🔄 Спробувати",callback_data=f"tf|{pair}|{tf}"),
                    InlineKeyboardButton("🏠 Меню",callback_data="main")))
        except: pass
        return
    chart_buf = None
    try:
        m2 = ALL_PAIRS.get(pair, FOREX_PAIRS[0])
        c2,h2,l2,o2 = get_candles(m2["symbol"],tf,100)
        if len(c2)>=20:
            sig["dp"] = m2["d"]
            chart_buf = generate_chart(pair, tf, c2, h2, l2, o2, sig)
    except Exception as e:
        print(f"[CHART ERR] {e}")
    txt = format_signal(pair,tf,sig)
    _last_signals[str(cid)] = {"pair": pair, "tf": tf, "is_buy": sig["is_buy"], "sent_at": time.time()}
    add_journal_entry(cid, pair, tf, sig["is_buy"], sig["acc"], sig["live"])
    try: bot.delete_message(cid, mid)
    except: pass
    try:
        if chart_buf:
            caption = txt if len(txt) <= 1024 else txt[:1020] + "..."
            bot.send_photo(cid, chart_buf, caption=caption,
                           parse_mode="Markdown", reply_markup=result_kb(pair,tf))
            if len(txt) > 1024:
                bot.send_message(cid, txt, parse_mode="Markdown")
        else:
            bot.send_message(cid, txt, parse_mode="Markdown", reply_markup=result_kb(pair,tf))
    except Exception as e:
        print(f"[ERR] {e}")
        try: bot.send_message(cid, txt, parse_mode="Markdown", reply_markup=result_kb(pair,tf))
        except: pass

_PAIR_LOOKUP = {}
for _p in FOREX_PAIRS + OTC_PAIRS + CRYPTO_PAIRS + STOCKS_PAIRS:
    _name = _p["name"]
    _PAIR_LOOKUP[_name.replace("/","").replace(" ","").upper()] = _name
    _PAIR_LOOKUP[_name.upper()] = _name
    _PAIR_LOOKUP[_name] = _name
_PAIR_LOOKUP.update({
    "AAPL":"Apple","TSLA":"Tesla","NVDA":"NVIDIA","AMZN":"Amazon",
    "GOOGL":"Google","MSFT":"Microsoft","META":"Meta","NFLX":"Netflix",
    "BITCOIN":"BTC/USD","ETHEREUM":"ETH/USD","SOLANA":"SOL/USD",
    "RIPPLE":"XRP/USD","CARDANO":"ADA/USD","DOGECOIN":"DOGE/USD",
    "LITECOIN":"LTC/USD","BINANCE":"BNB/USD","BNB":"BNB/USD",
})

def normalize_pair(text):
    t = text.strip().upper().replace("-","").replace("_","")
    if t in _PAIR_LOOKUP: return _PAIR_LOOKUP[t]
    t2 = t.replace("/","").replace(" ","")
    if t2 in _PAIR_LOOKUP: return _PAIR_LOOKUP[t2]
    if "OTC" in t:
        t_otc = t.replace(" ","").replace("/","")
        if t_otc in _PAIR_LOOKUP: return _PAIR_LOOKUP[t_otc]
    t_usd = t2 + "USD"
    if t_usd in _PAIR_LOOKUP: return _PAIR_LOOKUP[t_usd]
    for key, val in _PAIR_LOOKUP.items():
        if len(t2) >= 3 and key.startswith(t2) and len(key) <= len(t2)+3:
            return val
    return None

@bot.message_handler(commands=["start","menu"])
def cmd_start(msg):
    send_main(msg.chat.id)
    bot.send_message(msg.chat.id,
        "⌨️ Клавіатура активована!\n_Або просто напиши назву пари: `eurusd`, `chfjpy`, `btc`_",
        parse_mode="Markdown", reply_markup=start_kb())

@bot.message_handler(commands=["stats"])
def cmd_stats(msg): bot.send_message(msg.chat.id,stats_text(msg.chat.id),parse_mode="Markdown",reply_markup=main_kb())

@bot.message_handler(commands=["scan"])
def cmd_scan(msg):
    bot.send_message(msg.chat.id,"🔍 *Запускаю сканер...*",parse_mode="Markdown")
    threading.Thread(target=run_scanner,args=(msg.chat.id,),daemon=True).start()

@bot.message_handler(commands=["subscribe","auto"])
def cmd_subscribe(msg):
    cid = msg.chat.id
    if cid in _subscribers:
        _subscribers.discard(cid); _save_subscribers()
        bot.send_message(cid, "🔕 *Авто-сигнали вимкнено*", parse_mode="Markdown")
    else:
        _subscribers.add(cid); _save_subscribers()
        bot.send_message(cid, "🔔 *Авто-сигнали увімкнено!*\n\n⚡ Найсильніші сигнали (≥85%) кожні 5 хвилин.", parse_mode="Markdown")

@bot.message_handler(commands=["journal"])
def cmd_journal(msg):
    cid = msg.chat.id
    entries = get_journal(cid, 10)
    if not entries:
        bot.send_message(cid, "📓 *Журнал угод порожній*", parse_mode="Markdown"); return
    lines = ["📓 *Журнал угод (останні 10)*\n"]
    wins = sum(1 for e in entries if e.get("result") == "win")
    losses = sum(1 for e in entries if e.get("result") == "loss")
    for e in reversed(entries):
        res_em = "✅" if e.get("result") == "win" else ("❌" if e.get("result") == "loss" else "⏳")
        arrow = "⬆️" if e["dir"] == "UP" else "⬇️"
        lines.append(f"{res_em} {arrow} *{e['pair']}* {e['tf']}хв | {e['acc']}% | {e['time']}")
    lines.append(f"\n📊 В/П: *{wins}/{losses}*")
    if wins + losses > 0:
        wr = round(wins / (wins + losses) * 100)
        lines.append(f"🎯 Win Rate: *{wr}%*")
    bot.send_message(cid, "\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

@bot.message_handler(commands=["mtf"])
def cmd_mtf(msg):
    cid = msg.chat.id
    args = msg.text.split()
    pair_raw = args[1] if len(args) > 1 else "EUR/USD"
    pair = normalize_pair(pair_raw) or "EUR/USD"
    bot.send_message(cid, f"🔍 *MTF аналіз {pair}...*", parse_mode="Markdown")
    def do_mtf():
        direction, summary = mtf_analysis(pair)
        em = "🟢" if direction == 1 else ("🔴" if direction == -1 else "⚪")
        bot.send_message(cid, f"{em} *Мульти-таймфрейм*\n\n{summary}", parse_mode="Markdown", reply_markup=main_kb())
    threading.Thread(target=do_mtf, daemon=True).start()

@bot.message_handler(func=lambda m: True)
def cmd_text(msg):
    cid = msg.chat.id
    text = (msg.text or "").strip()
    if not text: return
    upper = text.upper()
    if upper in _REPLY_MAP:
        action = _REPLY_MAP[upper]
        if action == "main":          send_main(cid)
        elif action == "menu_forex":  bot.send_message(cid,"📈 *FOREX пари*\nОберіть пару:",parse_mode="Markdown",reply_markup=pairs_kb(FOREX_PAIRS,"main"))
        elif action == "menu_otc":    bot.send_message(cid,"🌙 *OTC пари*\nОберіть пару:",parse_mode="Markdown",reply_markup=pairs_kb(OTC_PAIRS,"main"))
        elif action == "menu_crypto": bot.send_message(cid,"₿ *КРИПТО*\nОберіть пару:",parse_mode="Markdown",reply_markup=pairs_kb(CRYPTO_PAIRS,"main"))
        elif action == "menu_stocks": bot.send_message(cid,"📊 *АКЦІЇ*\nОберіть:",parse_mode="Markdown",reply_markup=pairs_kb(STOCKS_PAIRS,"main"))
        elif action == "scanner":     bot.send_message(cid,"🔍 *Запускаю сканер...*",parse_mode="Markdown"); threading.Thread(target=run_scanner,args=(cid,),daemon=True).start()
        elif action == "stats":       bot.send_message(cid,stats_text(cid),parse_mode="Markdown",reply_markup=main_kb())
        elif action == "sessions":    bot.send_message(cid,sessions_text(),parse_mode="Markdown",reply_markup=main_kb())
        elif action == "auto_signals":
            if cid in _subscribers: _subscribers.discard(cid); _save_subscribers(); bot.send_message(cid,"🔕 *Авто-сигнали вимкнено*",parse_mode="Markdown")
            else: _subscribers.add(cid); _save_subscribers(); bot.send_message(cid,"🔔 *Авто-сигнали увімкнено!*",parse_mode="Markdown")
        elif action == "journal": cmd_journal(msg)
        return
    pair = normalize_pair(text)
    if pair:
        is_crypto = any(pair == p["name"] for p in CRYPTO_PAIRS)
        is_stocks = any(pair == p["name"] for p in STOCKS_PAIRS)
        is_otc    = "OTC" in pair
        tfs = CRYPTO_TF if is_crypto else (STOCKS_TF if is_stocks else TIMEFRAMES)
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(*[InlineKeyboardButton(v, callback_data=f"tf|{pair}|{k}") for k,v in tfs.items()])
        kb.add(InlineKeyboardButton("◀️ Меню", callback_data="main"))
        cat = "₿ Крипто" if is_crypto else ("📊 Акції" if is_stocks else ("🌙 OTC" if is_otc else "📈 Forex"))
        bot.send_message(cid, f"✅ *{pair}* знайдено  {cat}\n\n⏱ Оберіть таймфрейм:", parse_mode="Markdown", reply_markup=kb)
    else:
        bot.send_message(cid,
            "❓ *Пару не знайдено*\n\n"
            "Спробуй: `EURUSD`, `chfjpy`, `btc`, `AAPL`\n\nАбо оберіть з меню 👇",
            parse_mode="Markdown", reply_markup=main_kb())

@bot.callback_query_handler(func=lambda c: True)
def handle_cb(call):
    cid=call.message.chat.id; mid=call.message.message_id; d=call.data
    bot.answer_callback_query(call.id)
    try:
        if d=="main": send_main(cid,mid)
        elif d in("menu_forex","forex_back"): bot.edit_message_text("📈 *FOREX пари*\nОберіть пару:",cid,mid,parse_mode="Markdown",reply_markup=pairs_kb(FOREX_PAIRS,"main"))
        elif d in("menu_otc","otc_back"):     bot.edit_message_text("🌙 *OTC пари*\nОберіть пару:",cid,mid,parse_mode="Markdown",reply_markup=pairs_kb(OTC_PAIRS,"main"))
        elif d in("menu_crypto","crypto_back"): bot.edit_message_text("₿ *КРИПТО*\nОберіть пару:",cid,mid,parse_mode="Markdown",reply_markup=pairs_kb(CRYPTO_PAIRS,"main"))
        elif d in("menu_stocks","stocks_back"): bot.edit_message_text("📊 *АКЦІЇ*\nОберіть:",cid,mid,parse_mode="Markdown",reply_markup=pairs_kb(STOCKS_PAIRS,"main"))
        elif d=="stats":    bot.edit_message_text(stats_text(cid),cid,mid,parse_mode="Markdown",reply_markup=main_kb())
        elif d=="sessions": bot.edit_message_text(sessions_text(),cid,mid,parse_mode="Markdown",reply_markup=main_kb())
        elif d=="scanner":
            bot.edit_message_text("🔍 *Авто-сканер*\nШукаю найсильніші сигнали...",cid,mid,parse_mode="Markdown")
            threading.Thread(target=run_scanner,args=(cid,),daemon=True).start()
        elif d=="about":
            bot.edit_message_text(
                "ℹ️ *SIGNAL AI v2.0*\n\n*14 індикаторів:*\n"
                "• RSI, MACD, EMA 9/21/50\n• Stochastic, BB, Williams %R\n"
                "• STC, Momentum, ADX\n• 🆕 Heikin Ashi\n• 🆕 Parabolic SAR\n"
                "• 🆕 Fibonacci рівні\n• 🆕 Підтримка/Опір\n• 🆕 Свічкові патерни\n\n"
                "⏰ *Час входу* до кожного сигналу!\n\n📡 TwelveData API\n🎯 Точність: ~82-94%",
                cid,mid,parse_mode="Markdown",reply_markup=main_kb())
        elif d.startswith("pair_"):
            pair=d[5:]
            if pair not in ALL_PAIRS: bot.answer_callback_query(call.id,"❌ Невідома пара"); return
            bot.edit_message_text(f"⏱ *Таймфрейм для {pair}*\nОберіть:",cid,mid,parse_mode="Markdown",reply_markup=tf_kb(pair))
        elif d.startswith("tf|"):
            parts = d.split("|", 2)
            if len(parts) == 3:
                _,pair,tf = parts
                if pair not in ALL_PAIRS or tf not in {**TIMEFRAMES,**CRYPTO_TF,**STOCKS_TF}:
                    bot.answer_callback_query(call.id,"❌ Некоректні дані"); return
                threading.Thread(target=do_signal,args=(cid,mid,pair,tf),daemon=True).start()
        elif d.startswith(("win|","loss|")):
            parts = d.split("|", 2)
            if len(parts) != 3: return
            res,pair,tf = parts
            s=get_stats(cid); s["total"]+=1
            if res=="win":
                s["wins"]+=1; s["streak"]=max(s.get("streak",0)+1,1); em="✅ Виграш записано!"
            else:
                s["losses"]=s.get("losses",0)+1; s["streak"]=min(s.get("streak",0)-1,-1); em="❌ Програш записано"
            if pair not in s["pairs"]: s["pairs"][pair]={"total":0,"wins":0}
            s["pairs"][pair]["total"]+=1
            if res=="win": s["pairs"][pair]["wins"]+=1
            save_user_stats()
            j = get_journal(cid, 1)
            if j and j[-1].get("pair") == pair and j[-1].get("result") is None:
                j[-1]["result"] = "win" if res=="win" else "loss"
                save_journal(all_journal)
            wr=round(s["wins"]/s["total"]*100)
            bot.send_message(cid,f"{em}\n\n📊 WR: *{wr}%* ({s['wins']}W/{s.get('losses',0)}L)\n\nОберіть наступну дію:",
                             parse_mode="Markdown",reply_markup=main_kb())
    except Exception as e:
        if "not modified" not in str(e):
            print(f"[CB ERR] {e}")
            try: bot.send_message(cid,"Оберіть категорію:",reply_markup=main_kb())
            except: pass

if __name__=="__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger(__name__)
    print("✅ SIGNAL AI Bot v2.0 запущено!")
    threading.Thread(target=auto_signal_loop, daemon=True).start()
    threading.Thread(target=reversal_monitor, daemon=True).start()
    for attempt in range(8):
        try: bot.close()
        except: pass
        try:
            bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook видалено"); break
        except Exception as e:
            logger.warning(f"delete_webhook спроба {attempt+1}: {e}")
            time.sleep(3 + attempt * 2)
    logger.info("Чекаємо 15 сек...")
    time.sleep(15)
    while True:
        try:
            logger.info("Starting polling...")
            bot.infinity_polling(timeout=25, long_polling_timeout=20,
                skip_pending=True, none_stop=True, restart_on_change=False,
                allowed_updates=["message","callback_query"])
        except Exception as e:
            err = str(e)
            logger.error(f"Polling crashed: {err}")
            if "409" in err: time.sleep(30)
            else: time.sleep(10)
            try: bot.close()
            except: pass
            try: bot.delete_webhook(drop_pending_updates=True); time.sleep(5)
            except: pass
