import time
try:
    import ujson as json
except ImportError:
    import json

try:
    import urequests as requests
except ImportError:
    requests = None

from config import *

_last_tg_send = 0
_last_update_id = 0
_last_tg_chat_id = 0


def safe_close(r):
    try:
        if r:
            r.close()
    except Exception:
        pass


def url_escape(s):
    out = []
    for b in str(s).encode("utf-8"):
        if (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b in b"-_.~":
            out.append(chr(b))
        elif b == 32:
            out.append("%20")
        else:
            out.append("%{:02X}".format(b))
    return "".join(out)


def tg_send(text, reply_markup=None, chat_id=None):
    global _last_tg_send
    global _last_tg_chat_id

    target_chat_id = chat_id
    if target_chat_id is None:
        target_chat_id = _last_tg_chat_id if _last_tg_chat_id else TG_CHAT_ID
    try:
        target_chat_id_txt = str(int(target_chat_id))
    except Exception:
        target_chat_id_txt = str(target_chat_id)
    if (not TG_ENABLE) or (not TG_TOKEN) or (target_chat_id == 0):
        return False
    if requests is None:
        return False

    now = time.ticks_ms()
    gap = time.ticks_diff(now, _last_tg_send)
    if gap < TG_MIN_GAP_MS:
        time.sleep_ms(TG_MIN_GAP_MS - gap)
        now = time.ticks_ms()

    if text is None:
        text = ""
    try:
        text = str(text)
    except Exception:
        text = "<text error>"
    if len(text) > TG_TEXT_MAX_LEN:
        text = text[:TG_TEXT_MAX_LEN - 12] + "\n...truncated"

    r = None
    try:
        token_tail = "none"
        try:
            token_tail = TG_TOKEN[-6:] if TG_TOKEN else "none"
        except Exception:
            pass
        if not TG_INLINE_KEYBOARD_ENABLE:
            reply_markup = None
        form = "chat_id={}&text={}".format(
            url_escape(target_chat_id_txt),
            url_escape(text),
        )
        if reply_markup is not None:
            try:
                rm = json.dumps(reply_markup)
                form += "&reply_markup={}".format(url_escape(rm))
            except Exception:
                pass
        url = "https://api.telegram.org/bot{}/sendMessage".format(TG_TOKEN)
        r = requests.post(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        code = getattr(r, "status_code", None)
        if code is not None and code != 200:
            desc = "-"
            body = "-"
            try:
                j = r.json()
                if j:
                    desc = j.get("description", "-")
            except Exception:
                pass
            try:
                body = r.text
            except Exception:
                pass
            if isinstance(body, str) and len(body) > 180:
                body = body[:180] + "..."
            print(
                "TG HTTP:", code,
                "chat_id:", target_chat_id_txt,
                "token_tail:", token_tail,
                "desc:", desc,
                "body:", body
            )
            return False
        resp = None
        try:
            resp = r.json()
        except Exception:
            resp = None
        if not (resp and resp.get("ok")):
            desc = "-"
            try:
                desc = resp.get("description", "-") if resp else "-"
            except Exception:
                pass
            print("TG send failed:", desc)
            return False
        _last_tg_send = now
        _last_tg_chat_id = target_chat_id
        return True
    except Exception as e:
        print("TG send error:", e)
        return False
    finally:
        safe_close(r)


def tg_send_alert(text):
    return tg_send(text)


def _tg_post(method, payload):
    if (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return None
    r = None
    try:
        url = "https://api.telegram.org/bot{}/{}".format(TG_TOKEN, method)
        data = json.dumps(payload)
        r = requests.post(url, data=data, headers={"Content-Type": "application/json"})
        try:
            return r.json()
        except Exception:
            return None
    except Exception as e:
        print("TG post error:", e)
        return None
    finally:
        safe_close(r)


def tg_answer_callback(callback_id, text="Updated"):
    if not callback_id:
        return
    _tg_post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def tg_edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = _tg_post("editMessageText", payload)
    return bool(resp and resp.get("ok"))


def tg_get_updates(offset):
    if (not TG_ENABLE) or (not TG_TOKEN) or (requests is None):
        return None
    url = "https://api.telegram.org/bot{}/getUpdates?timeout=0&offset={}&limit={}".format(
        TG_TOKEN, offset, TG_GETUPDATES_LIMIT
    )
    r = None
    try:
        r = requests.get(url)
        try:
            return r.json()
        except Exception:
            return None
    except Exception as e:
        print("TG getUpdates error:", e)
        return None
    finally:
        safe_close(r)


def consume_update_id(uid):
    global _last_update_id
    if uid > _last_update_id:
        _last_update_id = uid
    return _last_update_id


def next_update_offset():
    return _last_update_id + 1


def remember_chat_id(chat_id):
    global _last_tg_chat_id
    if chat_id:
        _last_tg_chat_id = chat_id
