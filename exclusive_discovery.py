"""
exclusive_discovery.py — Auto-discovery exclusive baru dari JKT48 API

Dipanggil oleh background_monitor.py setiap cycle untuk:
1. Cek /api/v1/exclusives?lang=id → list semua exclusive aktif
2. Bandingkan dengan known_exclusives.json (persistent state)
3. Kalau ada exclusive baru → tambah ke dynamic_endpoints.json (dibaca background_monitor)
4. Kirim Telegram notif kalau ada exclusive baru
"""

import requests
import json
import os
import time
from datetime import datetime, timezone, timedelta
import pytz

WIB = pytz.timezone('Asia/Jakarta')

EXCLUSIVES_LIST_API = "https://jkt48.com/api/v1/exclusives?lang=id"

# File paths (semua di /mnt/user-data/outputs/ supaya persist di Railway)
KNOWN_EXCLUSIVES_FILE = "/mnt/user-data/outputs/known_exclusives.json"
DYNAMIC_ENDPOINTS_FILE = "/mnt/user-data/outputs/dynamic_endpoints.json"

TELEGRAM_BOT_TOKEN = "8541605155:AAFlFyF1g2DkW-ZonmX2H_7S-k67n3JKjWE"
TELEGRAM_CHAT_ID = "824000905"


def now_wib() -> datetime:
    return datetime.now(WIB)


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


def _fetch_exclusives_list(cookies=None) -> list:
    """
    Hit /api/v1/exclusives?lang=id → kembalikan list raw exclusive dicts.
    Returns [] kalau gagal.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9",
        "Referer": "https://jkt48.com/exclusive",
    }
    for attempt in range(3):
        try:
            resp = requests.get(
                EXCLUSIVES_LIST_API,
                headers=headers,
                cookies=cookies,
                timeout=15,
            )
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" in content_type:
                print(f"  [Discovery] HTML response (waiting room?) attempt {attempt+1}")
                time.sleep(10)
                continue
            data = resp.json()
            if data.get("status") and isinstance(data.get("data"), list):
                return data["data"]
            print(f"  [Discovery] Unexpected structure from exclusives list API")
            return []
        except requests.exceptions.Timeout:
            print(f"  [Discovery] Timeout attempt {attempt+1}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f"  [Discovery] Error attempt {attempt+1}: {e}")
            time.sleep(5)
    return []


def _load_known_exclusives() -> dict:
    """
    Load dict {exclusive_id: {...metadata}} dari file persisten.
    Key = str(exclusive_id).
    """
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
    """
    Load dict {event_name: api_url} exclusive yang sudah di-auto-add.
    Ini yang dibaca background_monitor.py sebagai tambahan API_ENDPOINTS.
    """
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
    """Dari code exclusive (e.g. EXBE10) → URL API stock-nya"""
    return f"https://jkt48.com/api/v1/exclusives/{code}?lang=id"


def _build_purchase_url(code: str) -> str:
    return f"https://jkt48.com/purchase/exclusive?code={code}"


def _format_category(raw_cat: str) -> str:
    mapping = {
        "PHOTOCARD": "Photocard",
        "DIGITAL_PHOTOBOOK": "Digital Photobook",
        "TWO_SHOT": "2-Shot",
        "VIDEO_CALL": "Video Call",
        "MEET_AND_GREET": "Meet & Greet",
        "HANDSHAKE": "Handshake",
    }
    return mapping.get(raw_cat, raw_cat.replace("_", " ").title())


def discover_new_exclusives(cookies=None) -> list:
    """
    Fungsi utama. Dipanggil dari background_monitor.py setiap beberapa cycle.

    Returns:
        list of dict — exclusive yang baru ditemukan (isinya metadata),
        kosong kalau tidak ada yang baru.

    Side effects:
        - Update KNOWN_EXCLUSIVES_FILE
        - Update DYNAMIC_ENDPOINTS_FILE (tambah event baru)
        - Kirim Telegram notif per exclusive baru
    """
    print("  [Discovery] Checking for new exclusives...")

    raw_list = _fetch_exclusives_list(cookies=cookies)
    if not raw_list:
        print("  [Discovery] No data returned from exclusives list API")
        return []

    print(f"  [Discovery] Got {len(raw_list)} exclusives from API")

    known = _load_known_exclusives()
    dynamic_endpoints = _load_dynamic_endpoints()

    new_found = []

    for entry in raw_list:
        exclusive_id = entry.get("exclusive_id")
        if not exclusive_id:
            continue

        eid_str = str(exclusive_id)

        # Sudah pernah ketemu sebelumnya → skip
        if eid_str in known:
            continue

        # ── Exclusive baru! ──────────────────────────────────────────
        title = (entry.get("title") or "").strip()
        code = entry.get("code", "")
        raw_cat = entry.get("category", "")
        date_str = entry.get("valid_date_from", "")
        short_desc = (entry.get("short_description") or "").strip()

        cat_label = _format_category(raw_cat)

        # Hanya exclusive dengan code yang bisa di-monitor stock-nya
        if not code:
            print(f"  [Discovery] Exclusive '{title}' (id={eid_str}) has no code — skip stock monitoring")
            # Tetap simpan ke known supaya tidak diproses ulang
            known[eid_str] = {
                "exclusive_id": exclusive_id,
                "title": title,
                "code": "",
                "category": raw_cat,
                "discovered_at": now_wib().isoformat(),
                "monitored": False,
                "reason": "no_code",
            }
            continue

        api_url = _build_exclusive_api_url(code)
        purchase_url = _build_purchase_url(code)

        # Event name yang akan muncul di dashboard (sama formatnya dengan hardcoded di monitor)
        event_name = f"{title} [{code}]"

        # Tambah ke dynamic endpoints kalau belum ada
        if event_name not in dynamic_endpoints:
            dynamic_endpoints[event_name] = api_url
            print(f"  [Discovery] ✨ NEW EXCLUSIVE: '{title}' (code={code})")
        else:
            print(f"  [Discovery] Already in endpoints: '{title}'")

        # Simpan ke known
        known[eid_str] = {
            "exclusive_id": exclusive_id,
            "title": title,
            "code": code,
            "category": raw_cat,
            "category_label": cat_label,
            "api_url": api_url,
            "purchase_url": purchase_url,
            "short_description": short_desc,
            "valid_date_from": date_str,
            "discovered_at": now_wib().isoformat(),
            "monitored": True,
            "event_name": event_name,
        }

        new_found.append(known[eid_str])

        # Telegram notif
        msg_lines = [
            f"*{title}*",
            f"Kategori: {cat_label}",
            f"Kode: `{code}`",
        ]
        if short_desc:
            msg_lines.append(f"_{short_desc}_")
        msg_lines.append(f"[Beli tiket]({purchase_url})")
        msg_lines.append(f"\n📊 Dashboard sudah otomatis tracking exclusive ini.")

        _send_telegram("\n".join(msg_lines))

    # Simpan state terbaru
    _save_known_exclusives(known)
    _save_dynamic_endpoints(dynamic_endpoints)

    if new_found:
        print(f"  [Discovery] Found {len(new_found)} new exclusive(s)!")
    else:
        print(f"  [Discovery] No new exclusives found ({len(known)} known)")

    return new_found


def get_all_monitored_endpoints(static_endpoints: dict) -> dict:
    """
    Merge static (hardcoded) API_ENDPOINTS dengan dynamic endpoints hasil discovery.
    Dynamic endpoints TIDAK override static (static prioritas).

    Args:
        static_endpoints: dict dari API_ENDPOINTS di background_monitor.py

    Returns:
        dict merged {event_name: api_url}
    """
    dynamic = _load_dynamic_endpoints()
    merged = dict(dynamic)  # mulai dari dynamic
    merged.update(static_endpoints)  # static override kalau ada konflik
    return merged


def get_discovery_summary() -> dict:
    """Untuk ditampilkan di dashboard Streamlit — summary discovery state"""
    known = _load_known_exclusives()
    dynamic = _load_dynamic_endpoints()
    return {
        "total_known": len(known),
        "total_monitored": len(dynamic),
        "monitored_names": list(dynamic.keys()),
        "known_list": [
            {
                "title": v.get("title"),
                "code": v.get("code"),
                "category": v.get("category_label", v.get("category")),
                "discovered_at": v.get("discovered_at"),
                "monitored": v.get("monitored", False),
            }
            for v in known.values()
        ],
    }
