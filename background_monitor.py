"""
JKT48 Background Stock Monitor
Runs 24/7 on Railway server - monitors stock changes and logs to file
Optimized: WIB timezone only, Telegram only, +1 day event date offset
Transport: aiohttp persistent session (bypass Cloudflare waiting room)
"""
from __future__ import annotations

import asyncio
import aiohttp
import json
import time
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import pytz
import locale
import requests  # hanya untuk Telegram (fire-and-forget, tidak kena CF)
from exclusive_discovery import (
    discover_new_exclusives_async,
    get_all_monitored_endpoints,
)
import proxy_pool
import cf_solver
from jkt48_schema import to_bonus_url, normalize_bonus
from curl_cffi.requests import AsyncSession


def _current_proxy_url():
    px = proxy_pool.requests_proxies()
    return px.get("https") if px else None


async def _apply_clearance(session, force=False):
    """Solve/refresh cf_clearance (CapSolver) untuk proxy sticky, set di session
    (cookie + User-Agent) supaya semua request lolos Cloudflare. No-op kalau CapSolver mati."""
    if not cf_solver.enabled():
        return
    proxy_url = _current_proxy_url()
    if not proxy_url:
        return
    loop = asyncio.get_event_loop()
    cf, ua = await loop.run_in_executor(None, cf_solver.get_clearance, proxy_url, force)
    if cf:
        try:
            session.cookies.set("cf_clearance", cf, domain=".jkt48.com")
        except Exception:
            session.cookies["cf_clearance"] = cf
        if ua:
            session.headers["User-Agent"] = ua

# Constants
WIB = pytz.timezone('Asia/Jakarta')
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum di-set di environment — notifikasi Telegram dinonaktifkan.")

# Semua event dikelola otomatis oleh exclusive_discovery.py
# Tidak ada hardcode — dynamic_endpoints.json dikelola background worker

REFRESH_INTERVAL = 60  # seconds (dinaikkan dari 30s untuk hemat kuota proxy)
DISCOVERY_INTERVAL = 10  # Check for new exclusives every N iterations (~5 menit)
CHANGE_LOG_FILE = "/mnt/user-data/outputs/change_log.json"
PREVIOUS_DATA_FILE = "/mnt/user-data/outputs/previous_data.json"
CONFIG_FILE = "/mnt/user-data/outputs/monitor_config.json"
COOKIE_FILE = "/mnt/user-data/outputs/cf_cookie.json"
SUMMARY_CACHE_FILE = "/mnt/user-data/outputs/summary_cache.json"

CATEGORY_DISPLAY = {
    "TWO_SHOT":          "2-Shot",
    "PHOTOCARD":         "Photocard",
    "DIGITAL_PHOTOBOOK": "Digital Photobook",
    "VIDEO_CALL":        "Video Call",
    "MEET_AND_GREET":    "Meet & Greet",
    "HANDSHAKE":         "Handshake",
}

MEMBER_TEAM_MAP = {
    # LOVE
    "Fiony Alveria": "LOVE", "Michelle Alexandra": "LOVE", "Cathleen Nixie": "LOVE",
    "Alya Amanda": "LOVE", "Aurhel Alana": "LOVE", "Celline Thefani": "LOVE",
    "Cynthia Yaputera": "LOVE", "Anindya Ramadhani": "LOVE", "Aurellia": "LOVE",
    "Fritzy Rosmerian": "LOVE", "Grace Octaviani": "LOVE", "Indah Cahya": "LOVE",
    "Nayla Suji": "LOVE", "Hillary Abigail": "LOVE", "Jazzlyn Trisha": "LOVE",
    # PASSION
    "Jessica Chandra": "PASSION", "Mutiara Azzahra": "PASSION", "Desy Natalia": "PASSION",
    "Angelina Christy": "PASSION", "Michelle Levia": "PASSION", "Kathrina Irene": "PASSION",
    "Victoria Kimberly": "PASSION", "Abigail Rachel": "PASSION", "Ribka Budiman": "PASSION",
    "Cornelia Vanisa": "PASSION", "Lulu Salsabila": "PASSION", "Dena Natalia": "PASSION",
    "Raisha Syifa": "PASSION", "Feni Fitriyanti": "PASSION", "Catherina Vallencia": "PASSION",
    # DREAM
    "Marsha Lenathea": "DREAM", "Freya Jayawardana": "DREAM", "Febriola Sinambela": "DREAM",
    "Gita Sekar Andarini": "DREAM", "Helisma Putri": "DREAM", "Gabriela Abigail": "DREAM",
    "Jesslyn Elly": "DREAM", "Nina Tutachia": "DREAM", "Shabilqis Naila": "DREAM",
    "Oline Manuel": "DREAM", "Adeline Wijaya": "DREAM", "Chelsea Davina": "DREAM",
    "Greesella Adhalia": "DREAM", "Gendis Mayrannisa": "DREAM",
    # TRAINEE
    "Jemima Evodie": "TRAINEE", "Nur Intan": "TRAINEE", "Jacqueline Immanuela": "TRAINEE",
    "Afera Thalia": "TRAINEE", "Astrella Virgiananda": "TRAINEE", "Aulia Riza": "TRAINEE",
    "Bong Aprilli": "TRAINEE", "Carissa Dini": "TRAINEE", "Christabella Bonita": "TRAINEE",
    "Fahira Putri": "TRAINEE", "Fatimah Azzahra": "TRAINEE", "Hagia Sopia": "TRAINEE",
    "Heidi Suyangga": "TRAINEE", "Humaira Ramadhani": "TRAINEE", "Maxine Faye": "TRAINEE",
    "Mikaela Kusjanto": "TRAINEE", "Putry Jazyta": "TRAINEE", "Ralyne Van Irwan": "TRAINEE",
    "Sona Kalyana": "TRAINEE",
}

# Helper functions
def now_wib():
    """Get current time in WIB"""
    return datetime.now(WIB)

def format_event_date(api_date_str):
    """Convert API date to display format with +1 day offset"""
    try:
        date_obj = datetime.strptime(api_date_str, '%Y-%m-%d') + timedelta(days=1)
        try:
            locale.setlocale(locale.LC_TIME, 'id_ID.UTF-8')
        except:
            try:
                locale.setlocale(locale.LC_TIME, 'id_ID')
            except:
                pass
        return date_obj.strftime("%A, %d %B %Y")
    except:
        return api_date_str

def get_adjusted_event_date(api_date_str):
    """Get event date with +1 day offset"""
    try:
        date_obj = datetime.strptime(api_date_str, '%Y-%m-%d') + timedelta(days=1)
        return date_obj.strftime('%Y-%m-%d')
    except:
        return api_date_str

# Load config (Telegram settings + Cloudflare cookie)
def load_config():
    """Load configuration from file"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
    
    return {
        "telegram": {"token": "", "chat_id": "", "enabled": False},
        "monitored_events": [],
        "cf_cookie_name": "",   # Dynamic cookie name (e.g., __cfwaitingroom_xxx)
        "cf_cookie_value": ""   # Cookie value
    }

def load_cf_cookie():
    """Load Cloudflare waiting room cookie (supports dynamic cookie names)"""
    try:
        config = load_config()
        
        # Check if we have cookie name and value stored
        cf_cookie_name = config.get("cf_cookie_name", "")
        cf_cookie_value = config.get("cf_cookie_value", "")
        
        if cf_cookie_name and cf_cookie_value:
            print(f"  🍪 Using cookie: {cf_cookie_name}")
            return {cf_cookie_name: cf_cookie_value}
        
        # Backward compatibility: check old cf_cookie field
        old_cookie = config.get("cf_cookie", "")
        if old_cookie:
            # Assume it's the standard __cf_waitingroom
            return {"__cf_waitingroom": old_cookie}
        
        # Try loading from separate cookie file (alternative)
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r') as f:
                cookie_data = json.load(f)
                
                # Look for any __cfwaitingroom* cookie
                for cookie_name, cookie_value in cookie_data.items():
                    if cookie_name.startswith("__cfwaitingroom"):
                        print(f"  🍪 Found cookie: {cookie_name}")
                        return {cookie_name: cookie_value}
    except Exception as e:
        print(f"Error loading CF cookie: {e}")
    
    return None

def save_config(config):
    """Save configuration to file"""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

def load_previous_data():
    """Load previous API data"""
    try:
        if os.path.exists(PREVIOUS_DATA_FILE):
            with open(PREVIOUS_DATA_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading previous data: {e}")
    return {}

def save_previous_data(data):
    """Save current API data for next comparison"""
    try:
        with open(PREVIOUS_DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error saving previous data: {e}")

def load_change_log():
    """Load change log from file"""
    try:
        if os.path.exists(CHANGE_LOG_FILE):
            with open(CHANGE_LOG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading change log: {e}")
    return []

def save_change_log(changes):
    """Save change log to file — SEMUA perubahan disimpan, tanpa batas."""
    try:
        with open(CHANGE_LOG_FILE, 'w') as f:
            json.dump(changes, f, indent=2, default=str)
    except Exception as e:
        print(f"Error saving change log: {e}")

def send_telegram_notification(message):
    """Send notification via Telegram with hardcoded credentials and WIB timezone"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        wib_time = now_wib().strftime('%d/%m/%Y %H:%M:%S WIB')
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": f"🎵 *JKT48 Stock Alert*\n\n{message}\n\n⏰ {wib_time}",
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def build_and_save_summary_cache(all_event_data, known_raw):
    """
    Agregasi data semua event → summary per member × kategori, simpan ke JSON.
    Dipanggil setiap iterasi oleh monitor_loop() setelah semua event di-fetch.

    all_event_data: dict {event_name: raw_api_data}
    known_raw:      dict {event_name: metadata dari known_exclusives.json}
    """
    # Flat list of rows: satu row per member per event (AVAILABLE-ONLY, tanpa tickets_sold)
    rows = []
    for event_name, event_data in all_event_data.items():
        if not event_data:
            continue

        meta = known_raw.get(event_name, {})
        category_raw   = meta.get("category", "")
        category_label = CATEGORY_DISPLAY.get(category_raw, category_raw.replace("_", " ").title())
        event_title    = meta.get("title", event_name)

        # Agregasi per member (sum available semua session dalam satu event)
        member_agg = {}
        for session in event_data.get('session', []):
            for detail in session.get('session_detail', []):
                name  = detail['jkt48_member_name']
                avail = detail.get('available_quota', 0)
                is_so = avail == 0

                if name not in member_agg:
                    member_agg[name] = {'avail': 0, 'slots': 0, 'so_slots': 0}
                member_agg[name]['avail']    += avail
                member_agg[name]['slots']    += 1
                member_agg[name]['so_slots'] += int(is_so)

        for member, agg in member_agg.items():
            team   = MEMBER_TEAM_MAP.get(member, 'Unknown')
            all_so = agg['slots'] > 0 and agg['so_slots'] == agg['slots']
            rows.append({
                'event_name':     event_name,
                'event_title':    event_title,
                'category_raw':   category_raw,
                'category_label': category_label,
                'member':         member,
                'team':           team,
                'available':      agg['avail'],
                'slots':          agg['slots'],
                'so_slots':       agg['so_slots'],
                'all_sold_out':   all_so,
            })

    # Groupby member × kategori (gabungkan kalau member ada di beberapa event kategori sama)
    grouped = {}
    for r in rows:
        key = (r['member'], r['category_raw'])
        if key not in grouped:
            grouped[key] = {
                'member':         r['member'],
                'team':           r['team'],
                'category_raw':   r['category_raw'],
                'category_label': r['category_label'],
                'event_titles':   [],
                'available':      0,
                'slots':          0,
                'so_slots':       0,
                'all_rows_so':    True,   # akan di-AND
            }
        g = grouped[key]
        if r['event_title'] not in g['event_titles']:
            g['event_titles'].append(r['event_title'])
        g['available'] += r['available']
        g['slots']     += r['slots']
        g['so_slots']  += r['so_slots']
        g['all_rows_so'] = g['all_rows_so'] and r['all_sold_out']

    # Finalisasi
    summary_rows = []
    for g in grouped.values():
        summary_rows.append({
            'member':         g['member'],
            'team':           g['team'],
            'category_raw':   g['category_raw'],
            'category_label': g['category_label'],
            'event_titles':   g['event_titles'],
            'available':      g['available'],
            'slots':          g['slots'],
            'so_slots':       g['so_slots'],
            'all_sold_out':   g['all_rows_so'],
        })

    cache = {
        'updated_at':    now_wib().isoformat(),
        'updated_at_wib': now_wib().strftime('%d/%m/%Y %H:%M:%S WIB'),
        'total_events':  len(all_event_data),
        'rows':          summary_rows,
    }

    try:
        with open(SUMMARY_CACHE_FILE, 'w') as f:
            json.dump(cache, f, ensure_ascii=False)
        print(f"  💾 Summary cache saved: {len(summary_rows)} rows ({len(all_event_data)} events)")
    except Exception as e:
        print(f"  ❌ Error saving summary cache: {e}")

def _make_session_headers() -> dict:
    """Headers identik dengan bot Discord — ini yang lolos Cloudflare."""
    return {
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


async def create_session():
    """
    curl_cffi AsyncSession dengan impersonasi Chrome.
    Meniru fingerprint TLS/HTTP2 Chrome asli -> lolos Cloudflare, sementara
    aiohttp/requests polos ketahuan 'bukan browser' dan kena 403 challenge.
    """
    session = AsyncSession(
        impersonate="chrome",
        timeout=25,
        headers={"Referer": "https://jkt48.com/", "Origin": "https://jkt48.com"},
    )
    print("  🌐 curl_cffi AsyncSession (impersonate=chrome)")
    if proxy_pool.has_proxies():
        print(f"  🔌 Proxy pool aktif: {proxy_pool.count()} proxy (rotasi round-robin)")
    else:
        print("  🔌 Proxy: koneksi langsung (tanpa proxy)")
    if cf_solver.enabled():
        print("  🔓 CapSolver aktif — cf_clearance untuk lolos Cloudflare (butuh proxy STICKY)")
    else:
        print("  🔓 CapSolver: tidak aktif (set CAPSOLVER_API_KEY untuk aktifkan)")
    return session


async def fetch_api_data_async(session, api_url, extra_cookies=None, max_retries=3):
    """
    Fetch satu endpoint JKT48 API pakai curl_cffi (impersonasi Chrome).
    Rotasi proxy per attempt kalau JKT48_PROXY_LIST di-set (kalau tidak: langsung).
    """
    for attempt in range(1, max_retries + 1):
        try:
            kw = {}
            proxies = proxy_pool.requests_proxies()  # rotasi; None kalau tak ada proxy
            if proxies:
                kw["proxies"] = proxies
            if extra_cookies:
                kw["cookies"] = extra_cookies

            resp = await session.get(api_url, **kw)
            content_type = resp.headers.get("Content-Type", "")

            if resp.status_code == 200 and "text/html" not in content_type:
                data = resp.json()
                # /bonus: data["data"] array (bisa [] utk event selesai).
                if data.get("status") and data.get("data") is not None:
                    return data["data"]
                print(f"     ⚠️  Status OK tapi struktur data tidak valid")
                return None

            elif "text/html" in content_type or resp.status_code in (403, 429, 503):
                print(f"     ⚠️  Cloudflare challenge (status={resp.status_code}, "
                      f"attempt {attempt}/{max_retries})")
                # Paksa solve ulang cf_clearance (IP sticky mungkin rotasi / cookie kadaluarsa)
                await _apply_clearance(session, force=True)
                await asyncio.sleep(5 * attempt)
                continue

            else:
                print(f"     ⚠️  HTTP {resp.status_code} untuk {api_url}")
                await asyncio.sleep(3 * attempt)
                continue

        except Exception as e:
            print(f"     ❌ Error attempt {attempt}/{max_retries}: {e}")
            await asyncio.sleep(5 * attempt)

    print(f"     ❌ Semua {max_retries} attempt gagal untuk {api_url}")
    return None

def detect_changes(new_data, prev_data, event_name, config):
    """
    Deteksi perubahan stok — AVAILABLE-ONLY.
    API /bonus tidak menyediakan tickets_sold, jadi hanya dua transisi dilacak:
      - sold_out    : available > 0  -> 0   (member jadi habis)
      - stock_return: available == 0 -> > 0 (stok balik/tersedia lagi)
    """
    if not prev_data:
        return []

    changes = []

    for new_session in new_data.get('session', []):
        prev_session = next(
            (s for s in prev_data.get('session', [])
             if s['label'] == new_session['label']),
            None
        )
        if not prev_session:
            continue

        # Get session date with +1 day offset for consistency
        adjusted_date = get_adjusted_event_date(new_session.get('date', ''))

        for new_detail in new_session['session_detail']:
            prev_detail = next(
                (d for d in prev_session['session_detail']
                 if d['jkt48_member_name'] == new_detail['jkt48_member_name']),
                None
            )
            if not prev_detail:
                continue

            new_available = new_detail.get('available_quota', 0)
            prev_available = prev_detail.get('available_quota', 0)
            member = new_detail['jkt48_member_name']

            # Stok kembali: habis -> tersedia lagi
            if prev_available == 0 and new_available > 0:
                changes.append({
                    'type': 'stock_return',
                    'event': event_name,
                    'member': member,
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'returned_quota': new_available,
                    'timestamp': now_wib().isoformat(),
                })
                send_telegram_notification(
                    f"♻️ *STOCK KEMBALI!*\n[{event_name}]\n{member} ({new_session['label']})\n"
                    f"Sold Out → {new_available} tersedia"
                )

            # Sold out: tersedia -> habis
            elif prev_available > 0 and new_available == 0:
                changes.append({
                    'type': 'sold_out',
                    'event': event_name,
                    'member': member,
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'last_available': prev_available,
                    'timestamp': now_wib().isoformat(),
                })
                send_telegram_notification(
                    f"🔴 *SOLD OUT!*\n[{event_name}]\n{member} ({new_session['label']})\n"
                    f"Habis dari {prev_available} tersedia!"
                )

    return changes

async def monitor_loop():
    """Main monitoring loop — async dengan aiohttp session persistent."""
    print("=" * 60)
    print("🚀 JKT48 Background Monitor Started")
    print(f"🔄 Refresh interval: {REFRESH_INTERVAL}s")
    print(f"🔍 Discovery interval: every {DISCOVERY_INTERVAL} iterations")
    print(f"📁 Change log: {CHANGE_LOG_FILE}")
    print(f"💾 Config file: {CONFIG_FILE}")
    print(f"🌐 Transport: aiohttp persistent session (CF-resistant)")
    print("=" * 60)

    # Satu session untuk seluruh lifetime proses
    session = await create_session()

    iteration = 0
    consecutive_errors = 0
    max_consecutive_errors = 10
    inactive_events = set()   # event dengan /bonus kosong (selesai) — di-skip sampai discovery berikutnya

    try:
        while True:
            try:
                iteration += 1
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"\n[{timestamp}] ⚡ Iteration #{iteration}")

                # Safety check
                if consecutive_errors >= max_consecutive_errors:
                    error_sleep = REFRESH_INTERVAL * 5
                    print(f"  ⚠️  {consecutive_errors} consecutive errors — sleeping {error_sleep}s")
                    await asyncio.sleep(error_sleep)
                    consecutive_errors = 0
                    continue

                # Load config dan previous data
                config       = load_config()
                previous_data = load_previous_data()
                change_log   = load_change_log()

                # Manual CF cookie (fallback kalau session belum punya cookie)
                cf_cookies = load_cf_cookie()
                if cf_cookies:
                    print(f"  🍪 Manual CF cookie loaded (fallback)")

                # Cloudflare clearance via CapSolver (dipakai semua request iterasi ini)
                await _apply_clearance(session)

                # ── Auto-discover exclusive baru ──────────────────────────
                if iteration % DISCOVERY_INTERVAL == 1:
                    try:
                        new_exclusives = await discover_new_exclusives_async(session)
                        if new_exclusives:
                            print(f"  🆕 {len(new_exclusives)} exclusive baru ditemukan!")
                        else:
                            print(f"  🔍 Discovery: tidak ada exclusive baru")
                    except Exception as e:
                        print(f"  ⚠️  Discovery error (non-fatal): {e}")

                # Load known_exclusives untuk metadata kategori
                known_raw = {}
                try:
                    known_path = "/mnt/user-data/outputs/known_exclusives.json"
                    if os.path.exists(known_path):
                        with open(known_path, 'r') as f:
                            raw = json.load(f)
                        for v in raw.values():
                            if v.get("event_name"):
                                known_raw[v["event_name"]] = v
                except Exception as e:
                    print(f"  ⚠️  Could not load known_exclusives: {e}")

                print(f"  📋 Current log has {len(change_log)} entries")

                all_changes      = []
                all_fetched_data = {}
                all_endpoints    = get_all_monitored_endpoints({})
                monitored_events = list(all_endpoints.keys())

                # Tiap siklus discovery, re-cek semua event (event inaktif bisa jadi aktif lagi / baru ditemukan)
                recheck_all = (iteration % DISCOVERY_INTERVAL == 1)
                if recheck_all:
                    inactive_events.clear()

                active_count = len(monitored_events) - len([e for e in monitored_events if e in inactive_events])
                print(f"  🎯 Monitoring {len(monitored_events)} events ({active_count} aktif, {len(inactive_events)} di-skip):")

                event_status = {}

                for event_name in monitored_events:
                    api_url = all_endpoints.get(event_name)
                    if not api_url:
                        event_status[event_name] = "SKIPPED"
                        continue

                    # Skip event yang /bonus-nya kosong (event selesai) — hemat request/bandwidth.
                    # Tetap di-cek ulang tiap siklus discovery (recheck_all).
                    if event_name in inactive_events and not recheck_all:
                        event_status[event_name] = "INACTIVE (skip)"
                        continue

                    # Pastikan pakai endpoint /bonus (punya angka available_quota)
                    api_url = to_bonus_url(api_url)

                    print(f"\n  📡 [{event_name}]")

                    # Fetch pakai curl_cffi (impersonasi Chrome, lolos Cloudflare)
                    raw = await fetch_api_data_async(
                        session, api_url, extra_cookies=cf_cookies
                    )

                    if raw is None:
                        print(f"     ❌ FETCH FAILED")
                        event_status[event_name] = "FETCH FAILED"
                        consecutive_errors += 1
                        continue

                    # /bonus (array sesi) -> bentuk internal seragam (available_quota, tanpa tickets_sold)
                    new_data = normalize_bonus(raw)
                    session_count = len(new_data.get('session', []))
                    consecutive_errors = 0

                    # /bonus kosong = event sudah selesai. Tandai inaktif & skip (jangan cemari summary/log).
                    if session_count == 0:
                        inactive_events.add(event_name)
                        print(f"     ⏭️  /bonus kosong — event selesai, di-skip sampai discovery berikutnya")
                        event_status[event_name] = "INACTIVE (bonus kosong)"
                        continue

                    inactive_events.discard(event_name)
                    print(f"     ✅ Fetched {session_count} sessions")
                    all_fetched_data[event_name] = new_data

                    prev_data = previous_data.get(event_name)
                    if prev_data is None:
                        print(f"     ℹ️  First time — establishing baseline")
                        event_status[event_name] = f"BASELINE ({session_count} sessions)"

                    try:
                        changes = detect_changes(new_data, prev_data, event_name, config)
                        if changes:
                            print(f"     🔔 {len(changes)} CHANGE(S) DETECTED!")
                            for c in changes:
                                print(f"        - {c['type']}: {c['member']} ({c.get('session','?')})")
                            all_changes.extend(changes)
                            event_status[event_name] = f"{len(changes)} CHANGES"
                        else:
                            if prev_data is not None:
                                print(f"     ✓ No changes")
                                event_status[event_name] = "NO CHANGES"
                    except Exception as e:
                        print(f"     ❌ Error detecting changes: {e}")
                        event_status[event_name] = f"ERROR: {e}"
                        import traceback; traceback.print_exc()

                    previous_data[event_name] = new_data

                # Summary
                print(f"\n  📊 ITERATION SUMMARY:")
                for ev, status in event_status.items():
                    print(f"     {ev}: {status}")

                # Build summary cache
                if all_fetched_data:
                    try:
                        build_and_save_summary_cache(all_fetched_data, known_raw)
                    except Exception as e:
                        print(f"  ❌ Error building summary cache: {e}")

                # Save
                try:
                    if all_changes:
                        change_log.extend(all_changes)
                        save_change_log(change_log)
                        print(f"\n  💾 Saved {len(all_changes)} change(s) | Total: {len(change_log)}")
                    save_previous_data(previous_data)
                except Exception as e:
                    print(f"  ❌ Error saving data: {e}")
                    import traceback; traceback.print_exc()

                print(f"  ✅ Iteration #{iteration} complete")
                print(f"  😴 Sleeping {REFRESH_INTERVAL}s...")
                await asyncio.sleep(REFRESH_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                consecutive_errors += 1
                print(f"  ❌ Critical error iteration #{iteration}: {e}")
                import traceback; traceback.print_exc()
                print(f"  🔄 Continuing... (consecutive errors: {consecutive_errors})")
                await asyncio.sleep(REFRESH_INTERVAL)

    finally:
        await session.close()
        print("\n👋 Background monitor stopped. Session closed.")

if __name__ == "__main__":
    Path("/mnt/user-data/outputs").mkdir(parents=True, exist_ok=True)
    asyncio.run(monitor_loop())
