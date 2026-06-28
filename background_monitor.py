"""
JKT48 Background Stock Monitor
Runs 24/7 on Railway server - monitors stock changes and logs to file
Optimized: WIB timezone only, Telegram only, +1 day event date offset
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import pytz
import locale
from exclusive_discovery import discover_new_exclusives, get_all_monitored_endpoints

# Constants
WIB = pytz.timezone('Asia/Jakarta')
TELEGRAM_BOT_TOKEN = "8541605155:AAFlFyF1g2DkW-ZonmX2H_7S-k67n3JKjWE"
TELEGRAM_CHAT_ID = "824000905"

# Configuration
# IMPORTANT: Must match dashboard's API_ENDPOINTS to ensure all events are monitored
API_ENDPOINTS = {
    "MnG Love Dream Passion": "https://jkt48.com/api/v1/exclusives/EXE588?lang=id",
    "2shot Love Dream Passion": "https://jkt48.com/api/v1/exclusives/EX579E?lang=id",
    "Love Dream Passion - Music Video Behind The Scenes": "https://jkt48.com/api/v1/exclusives/EXBE10?lang=id",
    "We Are Love, Dream Team, Passion On Fire!": "https://jkt48.com/api/v1/exclusives/EX3725?lang=id",
}

REFRESH_INTERVAL = 30  # seconds
DISCOVERY_INTERVAL = 10  # Check for new exclusives every N iterations (~5 menit)
CHANGE_LOG_FILE = "/mnt/user-data/outputs/change_log.json"
PREVIOUS_DATA_FILE = "/mnt/user-data/outputs/previous_data.json"
CONFIG_FILE = "/mnt/user-data/outputs/monitor_config.json"
COOKIE_FILE = "/mnt/user-data/outputs/cf_cookie.json"

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
        "monitored_events": list(API_ENDPOINTS.keys()),
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

        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except Exception as e:
        return False

def fetch_api_data(api_url, cookies=None):
    """Fetch data from JKT48 API with retry logic and cookie support"""
    max_retries = 3
    retry_delay = 5  # seconds
    
    # Headers to appear as legitimate API client
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7',
        'Referer': 'https://jkt48.com/exclusive'
    }
    
    for attempt in range(max_retries):
        try:
            response = requests.get(api_url, headers=headers, cookies=cookies, timeout=15)
            response.raise_for_status()
            
            # Check if response is JSON (not HTML waiting room)
            content_type = response.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                print(f"  ⚠️  Received HTML instead of JSON - possible waiting room")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * 2)  # Wait longer
                    continue
                return None
            
            data = response.json()
            
            if data.get('status') and data.get('data'):
                return data['data']
            
            print(f"  ⚠️  Invalid data structure from API")
            return None
            
        except requests.exceptions.Timeout:
            print(f"  ⏱️  Timeout on attempt {attempt + 1}/{max_retries}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            return None
            
        except requests.exceptions.JSONDecodeError:
            print(f"  ⚠️  JSON decode error - API might be blocked by waiting room")
            if attempt < max_retries - 1:
                time.sleep(retry_delay * 3)  # Wait even longer
                continue
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Request error on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            return None
            
        except Exception as e:
            print(f"  ❌ Unexpected error fetching {api_url}: {e}")
            return None
    
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

def monitor_loop():
    """Main monitoring loop with crash protection"""
    print("=" * 60)
    print("🚀 JKT48 Background Monitor Started")
    print(f"📊 Monitoring {len(API_ENDPOINTS)} static events")
    print(f"🔄 Refresh interval: {REFRESH_INTERVAL}s")
    print(f"🔍 Discovery interval: every {DISCOVERY_INTERVAL} iterations")
    print(f"📁 Change log: {CHANGE_LOG_FILE}")
    print(f"💾 Config file: {CONFIG_FILE}")
    print("=" * 60)
    
    iteration = 0
    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while True:
        try:
            iteration += 1
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n[{timestamp}] ⚡ Iteration #{iteration}")
            
            # Safety check - if too many consecutive errors, increase sleep time
            if consecutive_errors >= max_consecutive_errors:
                error_sleep = REFRESH_INTERVAL * 5  # 2.5 minutes
                print(f"  ⚠️  {consecutive_errors} consecutive errors - sleeping {error_sleep}s")
                time.sleep(error_sleep)
                consecutive_errors = 0  # Reset counter
                continue
            
            # Load config and previous data
            config = load_config()
            previous_data = load_previous_data()
            change_log = load_change_log()
            
            # Load Cloudflare waiting room cookie
            cf_cookies = load_cf_cookie()
            if cf_cookies:
                print(f"  🍪 Using Cloudflare cookie")

            # ── Auto-discover exclusive baru setiap DISCOVERY_INTERVAL iterasi ──
            if iteration % DISCOVERY_INTERVAL == 1:
                try:
                    new_exclusives = discover_new_exclusives(cookies=cf_cookies)
                    if new_exclusives:
                        print(f"  🆕 {len(new_exclusives)} exclusive baru ditemukan & ditambahkan ke monitoring!")
                    else:
                        print(f"  🔍 Discovery: tidak ada exclusive baru")
                except Exception as e:
                    print(f"  ⚠️  Discovery error (non-fatal): {e}")
            
            print(f"  📋 Current log has {len(change_log)} entries")
            
            all_changes = []
            # Merge static API_ENDPOINTS + dynamic endpoints hasil discovery
            all_endpoints = get_all_monitored_endpoints(API_ENDPOINTS)
            monitored_events = list(all_endpoints.keys())
            
            print(f"  🎯 Monitoring {len(monitored_events)} events ({len(API_ENDPOINTS)} static + {len(all_endpoints) - len(API_ENDPOINTS)} dynamic):")
            for ev in monitored_events:
                print(f"     - {ev}")
            
            # Monitor each event - track status per event
            event_status = {}
            
            for event_name in monitored_events:
                if event_name not in all_endpoints:
                    print(f"  ⚠️  Skipping unknown event: {event_name}")
                    event_status[event_name] = "SKIPPED (not in endpoints)"
                    continue
                
                api_url = all_endpoints[event_name]
                print(f"\n  📡 [{event_name}]")
                print(f"     URL: {api_url}")
                
                # Fetch new data with cookie
                new_data = fetch_api_data(api_url, cookies=cf_cookies)
                
                if not new_data:
                    print(f"     ❌ FETCH FAILED")
                    event_status[event_name] = "FETCH FAILED"
                    consecutive_errors += 1
                    continue
                
                # Log session count
                session_count = len(new_data.get('session', []))
                print(f"     ✅ Fetched {session_count} sessions")
                consecutive_errors = 0  # Reset on success
                
                # Get previous data for this event
                prev_data = previous_data.get(event_name)
                
                if prev_data is None:
                    print(f"     ℹ️  First time fetching - establishing baseline")
                    event_status[event_name] = f"BASELINE ({session_count} sessions)"
                
                # Detect changes
                try:
                    changes = detect_changes(new_data, prev_data, event_name, config)
                    
                    if changes:
                        print(f"     🔔 {len(changes)} CHANGE(S) DETECTED!")
                        for change in changes:
                            print(f"        - {change['type']}: {change['member']} ({change.get('session', '?')})")
                        all_changes.extend(changes)
                        event_status[event_name] = f"{len(changes)} CHANGES"
                    else:
                        if prev_data is not None:
                            print(f"     ✓ No changes")
                            event_status[event_name] = "NO CHANGES"
                except Exception as e:
                    print(f"     ❌ Error detecting changes: {e}")
                    event_status[event_name] = f"ERROR: {e}"
                    import traceback
                    traceback.print_exc()
                
                # Update previous data
                previous_data[event_name] = new_data
            
            # Print summary
            print(f"\n  📊 ITERATION SUMMARY:")
            for ev, status in event_status.items():
                print(f"     {ev}: {status}")
            
            # Save updates
            try:
                if all_changes:
                    change_log.extend(all_changes)
                    save_change_log(change_log)
                    print(f"\n  💾 Saved {len(all_changes)} change(s) to log")
                    print(f"  📊 Total log entries: {len(change_log)}")
                
                save_previous_data(previous_data)
            except Exception as e:
                print(f"  ❌ Error saving data: {e}")
                import traceback
                traceback.print_exc()
            
            print(f"  ✅ Iteration #{iteration} complete")
            print(f"  😴 Sleeping for {REFRESH_INTERVAL}s...")
            
        except KeyboardInterrupt:
            print("\n⚠️  Received interrupt signal - shutting down gracefully...")
            break
            
        except Exception as e:
            consecutive_errors += 1
            print(f"  ❌ Critical error in iteration #{iteration}: {e}")
            import traceback
            traceback.print_exc()
            print(f"  🔄 Continuing despite error (consecutive errors: {consecutive_errors})...")
        
        # Sleep before next iteration
        time.sleep(REFRESH_INTERVAL)
    
    print("\n👋 Background monitor stopped.")

if __name__ == "__main__":
    # Create data directory if not exists
    Path("/mnt/user-data/outputs").mkdir(parents=True, exist_ok=True)
    
    # Start monitoring
    monitor_loop()
