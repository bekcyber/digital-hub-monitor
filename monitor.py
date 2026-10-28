#!/usr/bin/env python3
"""Digital Hub kuzatuvchi.

Sayt(lar)ni va Telegram botni tekshiradi. Holat o‘zgarganda
(ishlamay qolsa / qayta tiklansa) egasiga Telegramda xabar yuboradi.
Faqat standart kutubxonalar ishlatiladi, hech narsa o‘rnatish shart emas.

Kerakli sirlar (GitHub Secrets):
  BOT_TOKEN  - BotFather bergan bot tokeni
  CHAT_ID    - xabar keladigan Telegram ID raqamingiz
  SITE_URLS  - sayt havolalari, vergul bilan ajratilgan (ixtiyoriy)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT = os.environ.get("CHAT_ID", "").strip()
SITES = [u.strip() for u in os.environ.get("SITE_URLS", "").replace("\n", ",").split(",") if u.strip()]
DRY = os.environ.get("DRY_RUN") == "1"
WAIT = float(os.environ.get("RETRY_WAIT", "5"))
STATE_FILE = "state.json"
TZ = timezone(timedelta(hours=5))  # Toshkent vaqti
REMIND_EVERY = 6 * 3600  # muammo davom etsa, har 6 soatda eslatadi
DAILY_HOUR = 9  # kunlik hisobot vaqti (Toshkent)
UA = "Mozilla/5.0 (DigitalHubMonitor)"


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def tg(method, **params):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
        return json.loads(r.read().decode())


def send(text):
    """Xabar yuboradi. Muvaffaqiyatli bo‘lsa True qaytaradi."""
    print("XABAR:", text.replace("\n", " | "))
    if DRY:
        return True
    try:
        res = tg("sendMessage", chat_id=CHAT, text=text, disable_web_page_preview="true")
        return bool(res.get("ok"))
    except Exception as e:  # noqa: BLE001
        print("Xabar yuborilmadi:", e)
        return False


def check_site(url):
    detail = ""
    for i in range(3):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                return True, "%s, %.1fs" % (r.status, time.time() - t0)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):  # server javob beryapti
                return True, "%s (server javob beryapti)" % e.code
            detail = "HTTP %s" % e.code
        except Exception as e:  # noqa: BLE001
            detail = str(e)[:120]
        if i < 2:
            time.sleep(WAIT)
    return False, detail


def check_bot():
    detail = ""
    for i in range(3):
        try:
            me = tg("getMe")
            if me.get("ok"):
                break
            detail = str(me.get("description", "noaniq xato"))
        except Exception as e:  # noqa: BLE001
            detail = str(e)[:120]
        if i < 2:
            time.sleep(WAIT)
    else:
        return False, "Telegram bot tokeniga javob bermayapti: " + detail
    try:
        info = tg("getWebhookInfo").get("result", {})
    except Exception:  # noqa: BLE001
        return True, "ok"
    if info.get("url"):  # faqat webhook ishlatilgan bo‘lsa chuqurroq tekshiradi
        err = info.get("last_error_message")
        if err and time.time() - info.get("last_error_date", 0) < 1800:
            return False, "Webhook xatosi: " + err
        pending = info.get("pending_update_count", 0)
        if pending > 50:
            return False, "Javob berilmagan xabarlar ko‘p: %d ta" % pending
    return True, "ok"


def main():
    if not DRY and (not TOKEN or not CHAT):
        print("Xato: BOT_TOKEN va CHAT_ID sirlari kiritilmagan.")
        sys.exit(1)

    state = load_state()
    checks = state.setdefault("checks", {})
    now = time.time()
    stamp = datetime.now(TZ)

    results = []  # (kalit, nom, ok, tafsilot)
    if TOKEN:
        results.append(("bot", "Telegram bot", *check_bot()))
    for u in SITES:
        results.append(("site:" + u, u, *check_site(u)))
    for key, name, ok, detail in results:
        print("%s %s - %s" % ("OK  " if ok else "XATO", name, detail))

    if not state.get("started"):
        lines = ["✅ Kuzatuvchi ishga tushdi", "Tekshiriladi: %d ta narsa" % len(results)]
        lines += [("🟢 " if ok else "🔴 ") + name for _, name, ok, _ in results]
        if send("\n".join(lines)):
            state["started"] = True

    for key, name, ok, detail in results:
        prev = checks.get(key)
        if ok:
            if prev and prev.get("s") == "down":
                mins = int((now - prev.get("since", now)) / 60)
                msg = "🟢 Qayta ishga tushdi\n%s\nTushib qolgan vaqt: taxminan %d daqiqa" % (name, mins)
                if send(msg):
                    checks[key] = {"s": "ok"}
            else:
                checks[key] = {"s": "ok"}
        elif not prev or prev.get("s") != "down":
            msg = "🔴 Muammo\n%s\nSabab: %s\nVaqt: %s" % (name, detail, stamp.strftime("%H:%M"))
            if send(msg):
                checks[key] = {"s": "down", "since": now, "alert": now}
        elif now - prev.get("alert", 0) > REMIND_EVERY:
            hrs = (now - prev.get("since", now)) / 3600
            msg = "🔴 Hali ham ishlamayapti (%.1f soat)\n%s\nSabab: %s" % (hrs, name, detail)
            if send(msg):
                prev["alert"] = now

    today = stamp.strftime("%Y-%m-%d")
    if stamp.hour >= DAILY_HOUR and state.get("last_daily") != today:
        lines = [("🟢 " if ok else "🔴 ") + "%s: %s" % (name, detail) for _, name, ok, detail in results]
        if send("☀️ Kunlik hisobot, %s\n%s" % (today, "\n".join(lines))):
            state["last_daily"] = today

    save_state(state)


if __name__ == "__main__":
    main()
