"""
JKT48 Background Stock Monitor
Runs 24/7 on Railway server - monitors stock changes and logs to file
Optimized: WIB timezone only, Telegram only, +1 day event date offset
Transport: aiohttp persistent session (bypass Cloudflare waiting room)
"""

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

# Constants
WIB = pytz.timezone('Asia/Jakarta')
TELEGRAM_BOT_TOKEN = "8541605155:AAFlFyF1g2DkW-ZonmX2H_7S-k67n3JKjWE"
TELEGRAM_CHAT_ID = "824000905"

# Semua event dikelola otomatis oleh exclusive_discovery.py
# Tidak ada hardcode — dynamic_endpoints.json dikelola background worker

REFRESH_INTERVAL = 30  # seconds
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
    """Save change log to file"""
    try:
        # Keep only last 500 changes to prevent file from growing too large
        if len(changes) > 500:
            changes = changes[-500:]
        
        with open(CHANGE_LOG_FILE, 'w') as f:
            json.dump(changes, f, indent=2)
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
    # Flat list of rows: satu row per member per event
    rows = []
    for event_name, event_data in all_event_data.items():
        if not event_data:
            continue

        meta = known_raw.get(event_name, {})
        category_raw   = meta.get("category", "")
        category_label = CATEGORY_DISPLAY.get(category_raw, category_raw.replace("_", " ").title())
        event_title    = meta.get("title", event_name)

        # Agregasi per member (sum semua session dalam satu event)
        member_agg = {}
        for session in event_data.get('session', []):
            for detail in session.get('session_detail', []):
                name  = detail['jkt48_member_name']
                sold  = detail['tickets_sold']
                avail = detail['available_quota']
                total = sold + avail
                is_so = avail == 0

                if name not in member_agg:
                    member_agg[name] = {'sold': 0, 'avail': 0, 'total': 0, 'sessions': 0, 'so_sessions': 0}
                member_agg[name]['sold']       += sold
                member_agg[name]['avail']      += avail
                member_agg[name]['total']      += total
                member_agg[name]['sessions']   += 1
                member_agg[name]['so_sessions'] += int(is_so)

        for member, agg in member_agg.items():
            team    = MEMBER_TEAM_MAP.get(member, 'Unknown')
            pct     = round(agg['sold'] / agg['total'] * 100, 1) if agg['total'] > 0 else 0.0
            all_so  = agg['sessions'] > 0 and agg['so_sessions'] == agg['sessions']
            rows.append({
                'event_name':     event_name,
                'event_title':    event_title,
                'category_raw':   category_raw,
                'category_label': category_label,
                'member':         member,
                'team':           team,
                'tickets_sold':   agg['sold'],
                'available':      agg['avail'],
                'total':          agg['total'],
                'sold_pct':       pct,
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
                'tickets_sold':   0,
                'available':      0,
                'total':          0,
                'all_rows_so':    True,   # akan di-AND
            }
        g = grouped[key]
        if r['event_title'] not in g['event_titles']:
            g['event_titles'].append(r['event_title'])
        g['tickets_sold'] += r['tickets_sold']
        g['available']    += r['available']
        g['total']        += r['total']
        g['all_rows_so']   = g['all_rows_so'] and r['all_sold_out']

    # Finalisasi
    summary_rows = []
    for g in grouped.values():
        pct = round(g['tickets_sold'] / g['total'] * 100, 1) if g['total'] > 0 else 0.0
        summary_rows.append({
            'member':         g['member'],
            'team':           g['team'],
            'category_raw':   g['category_raw'],
            'category_label': g['category_label'],
            'event_titles':   g['event_titles'],
            'tickets_sold':   g['tickets_sold'],
            'available':      g['available'],
            'total':          g['total'],
            'sold_pct':       pct,
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


async def create_session() -> aiohttp.ClientSession:
    """
    Buat satu aiohttp.ClientSession yang hidup sepanjang proses.
    Cookie jar-nya persistent — Cloudflare challenge cookie otomatis
    disimpan dan dibawa ke setiap request berikutnya, persis seperti bot Discord.
    """
    connector = aiohttp.TCPConnector(
        limit=10,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(total=20, connect=10)
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers=_make_session_headers(),
        cookie_jar=aiohttp.CookieJar(),   # persistent across requests
    )
    print("  🌐 aiohttp session created (persistent cookie jar)")
    return session


async def fetch_api_data_async(
    session: aiohttp.ClientSession,
    api_url: str,
    extra_cookies: dict = None,
    max_retries: int = 3,
) -> dict | None:
    """
    Fetch satu endpoint JKT48 API pakai session persistent.
    Cookie Cloudflare otomatis dihandle oleh cookie jar session.
    extra_cookies: dari config (manual CF cookie, fallback kalau session belum punya cookie).
    """
    for attempt in range(1, max_retries + 1):
        try:
            # Inject manual cookie hanya kalau ada (fallback)
            kwargs = {}
            if extra_cookies:
                kwargs['cookies'] = extra_cookies

            async with session.get(api_url, allow_redirects=True, **kwargs) as resp:
                content_type = resp.headers.get("Content-Type", "")

                if resp.status == 200 and "text/html" not in content_type:
                    data = await resp.json(content_type=None)
                    if data.get("status") and data.get("data"):
                        return data["data"]
                    print(f"     ⚠️  Status OK tapi struktur data tidak valid")
                    return None

                elif "text/html" in content_type or resp.status in (403, 429, 503):
                    print(f"     ⚠️  Kemungkinan Cloudflare waiting room "
                          f"(status={resp.status}, attempt {attempt}/{max_retries})")
                    await asyncio.sleep(5 * attempt)
                    continue

                else:
                    print(f"     ⚠️  HTTP {resp.status} untuk {api_url}")
                    await asyncio.sleep(3 * attempt)
                    continue

        except asyncio.TimeoutError:
            print(f"     ⏱️  Timeout attempt {attempt}/{max_retries}")
            await asyncio.sleep(5 * attempt)
        except aiohttp.ClientError as e:
            print(f"     ❌ Client error attempt {attempt}/{max_retries}: {e}")
            await asyncio.sleep(5 * attempt)
        except Exception as e:
            print(f"     ❌ Unexpected error: {e}")
            return None

    print(f"     ❌ Semua {max_retries} attempt gagal untuk {api_url}")
    return None

def detect_changes(new_data, prev_data, event_name, config):
    """Detect stock changes"""
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
        original_date = new_session.get('date', '')
        adjusted_date = get_adjusted_event_date(original_date)
        
        for new_detail in new_session['session_detail']:
            prev_detail = next(
                (d for d in prev_session['session_detail']
                 if d['jkt48_member_name'] == new_detail['jkt48_member_name']),
                None
            )
            
            if not prev_detail:
                continue
            
            new_available = new_detail['available_quota']
            prev_available = prev_detail['available_quota']
            new_sold = new_detail['tickets_sold']
            prev_sold = prev_detail['tickets_sold']
            
            # 1. Stock return from sold out
            if prev_available == 0 and new_available > 0:
                change = {
                    'type': 'stock_return',
                    'event': event_name,
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': adjusted_date,  # YYYY-MM-DD format (+1 day)
                    'returned_quota': new_available,
                    'refunded_tickets': prev_sold - new_sold if new_sold < prev_sold else 0,
                    'timestamp': now_wib().isoformat()  # WIB timezone
                }
                changes.append(change)
                
                if change['refunded_tickets'] > 0:
                    msg = f"♻️ *STOCK KEMBALI!* (Refund)\n[{event_name}]\n{change['member']} ({change['session']})\nSold Out → {new_available} tiket\n💳 {change['refunded_tickets']} dibatalkan"
                else:
                    msg = f"♻️ *STOCK KEMBALI!*\n[{event_name}]\n{change['member']} ({change['session']})\nSold Out → {new_available} tiket"
                
                send_telegram_notification(msg)
            
            # 2. Stock increase (not from sold out)
            elif new_available > prev_available and prev_available > 0:
                change = {
                    'type': 'stock_increase',
                    'event': event_name,
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'old_quota': prev_available,
                    'new_quota': new_available,
                    'difference': new_available - prev_available,
                    'timestamp': now_wib().isoformat()
                }
                changes.append(change)
                
                msg = f"📈 *STOCK NAIK!*\n[{event_name}]\n{change['member']} ({change['session']})\n{change['old_quota']} → {change['new_quota']} (+{change['difference']})"
                send_telegram_notification(msg)
            
            # 3. New transaction
            elif new_sold > prev_sold:
                sold_diff = new_sold - prev_sold
                change = {
                    'type': 'new_transaction',
                    'event': event_name,
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'tickets_bought': sold_diff,
                    'old_sold': prev_sold,
                    'new_sold': new_sold,
                    'remaining': new_available,
                    'timestamp': now_wib().isoformat()
                }
                changes.append(change)
                
                # Only notify for significant purchases
                if sold_diff >= 5 or new_available == 0:
                    msg = f"🎫 *TRANSAKSI BARU!*\n[{event_name}]\n{change['member']} ({change['session']})\n{sold_diff} tiket terjual\nSisa: {new_available}"
                    send_telegram_notification(msg)
            
            # 4. Refund (tickets_sold decreased)
            elif new_sold < prev_sold and prev_available > 0:
                refund_diff = prev_sold - new_sold
                change = {
                    'type': 'refund',
                    'event': event_name,
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'refunded_tickets': refund_diff,
                    'old_sold': prev_sold,
                    'new_sold': new_sold,
                    'new_available': new_available,
                    'timestamp': now_wib().isoformat()
                }
                changes.append(change)
                
                msg = f"💳 *REFUND/CANCEL!*\n[{event_name}]\n{change['member']} ({change['session']})\n{refund_diff} dibatalkan\nStock: {new_available}"
                send_telegram_notification(msg)
            
            # 5. Sold out
            if new_available == 0 and prev_available > 0:
                change = {
                    'type': 'sold_out',
                    'event': event_name,
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': adjusted_date,
                    'last_available': prev_available,
                    'timestamp': now_wib().isoformat()
                }
                changes.append(change)
                
                msg = f"🔴 *SOLD OUT!*\n[{event_name}]\n{change['member']} ({change['session']})\nHabis dari {change['last_available']} tiket!"
                send_telegram_notification(msg)
    
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

                print(f"  🎯 Monitoring {len(monitored_events)} events:")
                for ev in monitored_events:
                    print(f"     - {ev}")

                event_status = {}

                for event_name in monitored_events:
                    api_url = all_endpoints.get(event_name)
                    if not api_url:
                        event_status[event_name] = "SKIPPED"
                        continue

                    print(f"\n  📡 [{event_name}]")

                    # Fetch pakai aiohttp session persistent
                    new_data = await fetch_api_data_async(
                        session, api_url, extra_cookies=cf_cookies
                    )

                    if not new_data:
                        print(f"     ❌ FETCH FAILED")
                        event_status[event_name] = "FETCH FAILED"
                        consecutive_errors += 1
                        continue

                    session_count = len(new_data.get('session', []))
                    print(f"     ✅ Fetched {session_count} sessions")
                    consecutive_errors = 0

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
