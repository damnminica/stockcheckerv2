"""
exclusive_discovery.py — Auto-discovery exclusive baru dari JKT48 API

Dipanggil oleh background_monitor.py setiap cycle untuk:
1. Cek /api/v1/exclusives?lang=id → list semua exclusive aktif
2. Bandingkan dengan known_exclusives.json (persistent state)
3. Kalau ada exclusive baru → tambah ke dynamic_endpoints.json
4. Kirim Telegram notif kalau ada exclusive baru

Transport: aiohttp session persistent (sama seperti monitor) → bypass CF waiting room.
Fallback ke requests kalau dipanggil standalone (test/debug).
"""

import asyncio
import json
import os
import time
import requests  # hanya untuk Telegram dan fallback standalone
from datetime import datetime
import pytz

WIB = pytz.timezone('Asia/Jakarta')

EXCLUSIVES_LIST_API  = "https://jkt48.com/api/v1/exclusives?lang=id"
KNOWN_EXCLUSIVES_FILE  = "/mnt/user-data/outputs/known_exclusives.json"
DYNAMIC_ENDPOINTS_FILE = "/mnt/user-data/outputs/dynamic_endpoints.json"

TELEGRAM_BOT_TOKEN = "8541605155:AAFlFyF1g2DkW-ZonmX2H_7S-k67n3JKjWE"
TELEGRAM_CHAT_ID   = "824000905"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, */*",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    "Referer":         "https://jkt48.com/",
    "Origin":          "https://jkt48.com",
}


def now_wib() -> datetime:
    return datetime.now(WIB)


# ── Telegram (requests OK — Telegram tidak kena CF) ──────────────────────────

def _send_telegram(message: str) -> bool:
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        wib_time = now_wib().strftime('%d/%m/%Y %H:%M:%S WIB')
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"💎 *JKT48 Exclusive Baru!*\n\n{message}\n\n⏰ {wib_time}",
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"  [Discovery] Telegram error: {e}")
        return False


# ── Fetch list exclusives — async pakai session persistent ────────────────────

async def _fetch_exclusives_list_async(session) -> list:
    """Fetch pakai aiohttp session yang sama dengan monitor (cookie jar shared)."""
    for attempt in range(1, 4):
        try:
            async with session.get(
                EXCLUSIVES_LIST_API, allow_redirects=True
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if resp.status == 200 and "text/html" not in content_type:
                    data = await resp.json(content_type=None)
                    if data.get("status") and isinstance(data.get("data"), list):
                        return data["data"]
                    print("  [Discovery] Struktur data tidak valid")
                    return []
                else:
                    print(f"  [Discovery] CF/waiting room? status={resp.status} attempt {attempt}/3")
                    await asyncio.sleep(10 * attempt)
        except asyncio.TimeoutError:
            print(f"  [Discovery] Timeout attempt {attempt}/3")
            await asyncio.sleep(5 * attempt)
        except Exception as e:
            print(f"  [Discovery] Error attempt {attempt}/3: {e}")
            await asyncio.sleep(5)
    return []


def _fetch_exclusives_list_sync() -> list:
    """Fallback sync — dipakai hanya kalau dipanggil standalone tanpa session."""
    for attempt in range(3):
        try:
            resp = requests.get(
                EXCLUSIVES_LIST_API, headers=_HEADERS, timeout=15
            )
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                print(f"  [Discovery] HTML response attempt {attempt+1}")
                time.sleep(10)
                continue
            data = resp.json()
            if data.get("status") and isinstance(data.get("data"), list):
                return data["data"]
        except Exception as e:
            print(f"  [Discovery] Sync error attempt {attempt+1}: {e}")
            time.sleep(5)
    return []


# ── File helpers ──────────────────────────────────────────────────────────────

def _load_known_exclusives() -> dict:
    try:
        if os.path.exists(KNOWN_EXCLUSIVES_FILE):
            with open(KNOWN_EXCLUSIVES_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"  [Discovery] Error loading known exclusives: {e}")
    return {}


def _save_known_exclusives(data: dict):
    try:
        with open(KNOWN_EXCLUSIVES_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  [Discovery] Error saving known exclusives: {e}")


def _load_dynamic_endpoints() -> dict:
    try:
        if os.path.exists(DYNAMIC_ENDPOINTS_FILE):
            with open(DYNAMIC_ENDPOINTS_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"  [Discovery] Error loading dynamic endpoints: {e}")
    return {}


def _save_dynamic_endpoints(data: dict):
    try:
        with open(DYNAMIC_ENDPOINTS_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  [Discovery] Error saving dynamic endpoints: {e}")


def _build_exclusive_api_url(code: str) -> str:
    return f"https://jkt48.com/api/v1/exclusives/{code}?lang=id"


def _build_purchase_url(code: str) -> str:
    return f"https://jkt48.com/purchase/exclusive?code={code}"


def _format_category(raw_cat: str) -> str:
    mapping = {
        "PHOTOCARD":         "Photocard",
        "DIGITAL_PHOTOBOOK": "Digital Photobook",
        "TWO_SHOT":          "2-Shot",
        "VIDEO_CALL":        "Video Call",
        "MEET_AND_GREET":    "Meet & Greet",
        "HANDSHAKE":         "Handshake",
    }
    return mapping.get(raw_cat, raw_cat.replace("_", " ").title())


# ── Core discovery logic (shared antara sync & async) ────────────────────────

def _process_raw_list(raw_list: list) -> list:
    """
    Bandingkan raw_list dari API dengan known_exclusives.json.
    Tambah yang baru ke dynamic_endpoints.json dan kirim Telegram.
    Return list exclusive baru yang ditemukan.
    """
    if not raw_list:
        print("  [Discovery] No data returned from API")
        return []

    print(f"  [Discovery] Got {len(raw_list)} exclusives from API")

    known             = _load_known_exclusives()
    dynamic_endpoints = _load_dynamic_endpoints()
    new_found         = []

    for entry in raw_list:
        exclusive_id = entry.get("exclusive_id")
        if not exclusive_id:
            continue

        eid_str = str(exclusive_id)
        if eid_str in known:
            continue

        # ── Exclusive baru ────────────────────────────────────────────
        title      = (entry.get("title") or "").strip()
        code       = entry.get("code", "")
        raw_cat    = entry.get("category", "")
        date_str   = entry.get("valid_date_from", "")
        short_desc = (entry.get("short_description") or "").strip()
        cat_label  = _format_category(raw_cat)

        if not code:
            print(f"  [Discovery] '{title}' has no code — skip monitoring")
            known[eid_str] = {
                "exclusive_id": exclusive_id, "title": title, "code": "",
                "category": raw_cat, "discovered_at": now_wib().isoformat(),
                "monitored": False, "reason": "no_code",
            }
            continue

        api_url      = _build_exclusive_api_url(code)
        purchase_url = _build_purchase_url(code)
        event_name   = f"{title} [{code}]"

        if event_name not in dynamic_endpoints:
            dynamic_endpoints[event_name] = api_url
            print(f"  [Discovery] ✨ NEW: '{title}' (code={code})")
        else:
            print(f"  [Discovery] Already tracked: '{title}'")

        known[eid_str] = {
            "exclusive_id": exclusive_id, "title": title, "code": code,
            "category": raw_cat, "category_label": cat_label,
            "api_url": api_url, "purchase_url": purchase_url,
            "short_description": short_desc, "valid_date_from": date_str,
            "discovered_at": now_wib().isoformat(),
            "monitored": True, "event_name": event_name,
        }
        new_found.append(known[eid_str])

        # Telegram notif
        msg_lines = [f"*{title}*", f"Kategori: {cat_label}", f"Kode: `{code}`"]
        if short_desc:
            msg_lines.append(f"_{short_desc}_")
        msg_lines.append(f"[Beli tiket]({purchase_url})")
        msg_lines.append("\n📊 Dashboard sudah otomatis tracking exclusive ini.")
        _send_telegram("\n".join(msg_lines))

    _save_known_exclusives(known)
    _save_dynamic_endpoints(dynamic_endpoints)

    if new_found:
        print(f"  [Discovery] Found {len(new_found)} new exclusive(s)!")
    else:
        print(f"  [Discovery] No new exclusives ({len(known)} known)")

    return new_found


# ── Public API ────────────────────────────────────────────────────────────────

def discover_new_exclusives(cookies=None, session_hint=None) -> list:
    """
    Fungsi utama — dipanggil dari background_monitor via run_in_executor.
    session_hint: aiohttp.ClientSession dari monitor (tidak bisa dipakai langsung
    dari thread executor, jadi kita fallback ke sync di sini).

    Untuk bypass CF yang sesungguhnya, monitor memanggil discover_new_exclusives
    melalui fetch_api_data_async secara langsung di iterasi discovery.
    """
    print("  [Discovery] Checking for new exclusives...")
    raw_list = _fetch_exclusives_list_sync()
    return _process_raw_list(raw_list)


async def discover_new_exclusives_async(session) -> list:
    """
    Async version — dipanggil langsung dari monitor_loop (tanpa executor).
    Pakai aiohttp session yang sama → cookie jar shared → CF lolos.
    """
    print("  [Discovery] Checking for new exclusives (async)...")
    raw_list = await _fetch_exclusives_list_async(session)
    return _process_raw_list(raw_list)


def get_all_monitored_endpoints(static_endpoints: dict) -> dict:
    dynamic = _load_dynamic_endpoints()
    merged  = dict(dynamic)
    merged.update(static_endpoints)
    return merged


def get_discovery_summary() -> dict:
    known   = _load_known_exclusives()
    dynamic = _load_dynamic_endpoints()
    return {
        "total_known":     len(known),
        "total_monitored": len(dynamic),
        "monitored_names": list(dynamic.keys()),
        "known_list": [
            {
                "title":        v.get("title"),
                "code":         v.get("code"),
                "category":     v.get("category_label", v.get("category")),
                "discovered_at": v.get("discovered_at"),
                "monitored":    v.get("monitored", False),
            }
            for v in known.values()
        ],
    }
