"""
JKT48 Stock Monitor - Streamlit App
Auto-monitor stock changes with Telegram notifications
Optimized: WIB timezone only, +1 day event date offset
"""

import streamlit as st
import requests
import pandas as pd
import time
from datetime import datetime, timedelta
import json
import plotly.express as px
import plotly.graph_objects as go
from streamlit_autorefresh import st_autorefresh
import pytz
import locale
import os

# Constants
WIB = pytz.timezone('Asia/Jakarta')
TELEGRAM_BOT_TOKEN = "8541605155:AAFlFyF1g2DkW-ZonmX2H_7S-k67n3JKjWE"
TELEGRAM_CHAT_ID = "824000905"

# Date formatting functions
def format_event_date(api_date_str):
    """Convert API date (YYYY-MM-DD) to display format with +1 day offset
    Returns: 'Jumat, 09 Mei 2026' format
    """
    try:
        # Parse API date and add 1 day
        date_obj = datetime.strptime(api_date_str, '%Y-%m-%d') + timedelta(days=1)
        
        # Try to set Indonesian locale
        try:
            locale.setlocale(locale.LC_TIME, 'id_ID.UTF-8')
        except:
            try:
                locale.setlocale(locale.LC_TIME, 'id_ID')
            except:
                pass  # Fallback to English if Indonesian locale not available
        
        # Format: "Jumat, 09 Mei 2026"
        return date_obj.strftime("%A, %d %B %Y")
    except:
        return api_date_str

def get_adjusted_event_date(api_date_str):
    """Get event date with +1 day offset in YYYY-MM-DD format for filtering"""
    try:
        date_obj = datetime.strptime(api_date_str, '%Y-%m-%d') + timedelta(days=1)
        return date_obj.strftime('%Y-%m-%d')
    except:
        return api_date_str

def now_wib():
    """Get current time in WIB"""
    return datetime.now(WIB)

def format_timestamp_wib(timestamp_str):
    """Convert any timestamp to WIB format"""
    try:
        if isinstance(timestamp_str, str):
            if 'T' in timestamp_str:
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                try:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                except:
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            
            wib_dt = dt.astimezone(WIB)
            return wib_dt.strftime("%d/%m/%Y %H:%M:%S")
        return str(timestamp_str)
    except:
        return str(timestamp_str)

# Page config
st.set_page_config(
    page_title="JKT48 Stock Monitor",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main {
        padding: 0rem 1rem;
    }
    .stAlert {
        padding: 1rem;
        border-radius: 0.5rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 1rem;
        color: white;
        text-align: center;
    }
    .stock-increase {
        background: #4caf50;
        color: white;
        padding: 0.5rem 1rem;
        border-radius: 0.5rem;
        font-weight: bold;
    }
    .sold-out {
        background: #f44336;
        color: white;
        padding: 0.5rem 1rem;
        border-radius: 0.5rem;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# API Configuration - TAMBAH EVENT DI SINI!
API_ENDPOINTS = {
    "MnG Love Dream Passion": "https://jkt48.com/api/v1/exclusives/EXE588?lang=id",
    "2shot Love Dream Passion": "https://jkt48.com/api/v1/exclusives/EX579E?lang=id",
    "Love Dream Passion - Music Video Behind The Scenes": "https://jkt48.com/api/v1/exclusives/EXBE10?lang=id",
    "We Are Love, Dream Team, Passion On Fire!": "https://jkt48.com/api/v1/exclusives/EX3725?lang=id",
}

# File paths for background worker
CHANGE_LOG_FILE = "/mnt/user-data/outputs/change_log.json"
CONFIG_FILE = "/mnt/user-data/outputs/monitor_config.json"
DYNAMIC_ENDPOINTS_FILE = "/mnt/user-data/outputs/dynamic_endpoints.json"
KNOWN_EXCLUSIVES_FILE = "/mnt/user-data/outputs/known_exclusives.json"

def load_change_log_from_file():
    """Load change log from file (written by background worker)"""
    try:
        if os.path.exists(CHANGE_LOG_FILE):
            with open(CHANGE_LOG_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        st.error(f"Error loading change log: {e}")
    return []

def save_config_to_file(config):
    """Save config for background worker"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        st.error(f"Error saving config: {e}")
        return False

def load_config_from_file():
    """Load config from file"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    
    return {
        "telegram": {"token": "", "chat_id": "", "enabled": False},
        "monitored_events": list(API_ENDPOINTS.keys())
    }

def load_dynamic_endpoints() -> dict:
    """Load auto-discovered exclusive endpoints dari background worker"""
    try:
        if os.path.exists(DYNAMIC_ENDPOINTS_FILE):
            with open(DYNAMIC_ENDPOINTS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def get_all_event_options() -> dict:
    """
    Gabungkan API_ENDPOINTS (static hardcoded) + dynamic endpoints dari discovery.
    Static menang kalau ada konflik nama.
    """
    dynamic = load_dynamic_endpoints()
    merged = dict(dynamic)
    merged.update(API_ENDPOINTS)  # static override
    return merged

def load_discovery_summary() -> dict:
    """Load summary exclusive yang sudah pernah ditemukan oleh background worker"""
    try:
        if os.path.exists(KNOWN_EXCLUSIVES_FILE):
            with open(KNOWN_EXCLUSIVES_FILE, 'r') as f:
                known = json.load(f)
            return {
                "total_known": len(known),
                "items": [
                    {
                        "title": v.get("title", "?"),
                        "code": v.get("code", ""),
                        "category": v.get("category_label", v.get("category", "")),
                        "discovered_at": v.get("discovered_at", ""),
                        "monitored": v.get("monitored", False),
                        "event_name": v.get("event_name", ""),
                    }
                    for v in known.values()
                ]
            }
    except Exception:
        pass
    return {"total_known": 0, "items": []}

# Initialize session state
if 'selected_event' not in st.session_state:
    _initial_events = list(get_all_event_options().keys())
    st.session_state.selected_event = _initial_events[0] if _initial_events else list(API_ENDPOINTS.keys())[0]
if 'previous_data' not in st.session_state:
    st.session_state.previous_data = None
if 'change_log' not in st.session_state:
    st.session_state.change_log = []
if 'telegram_token' not in st.session_state:
    st.session_state.telegram_token = ""
if 'telegram_chat_id' not in st.session_state:
    st.session_state.telegram_chat_id = ""
if 'notifications_enabled' not in st.session_state:
    st.session_state.notifications_enabled = False

# Member to Team mapping
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
    "Jemima Evodie": "TRAINEE", "Nur Intan": "TRAINEE", "Jacqueline Immanuela": "TRAINEE", "Afera Thalia": "TRAINEE", 
    "Astrella Virgiananda": "TRAINEE", "Aulia Riza": "TRAINEE", "Bong Aprilli": "TRAINEE", "Carissa Dini": "TRAINEE",
    "Christabella Bonita": "TRAINEE", "Fahira Putri": "TRAINEE", "Fatimah Azzahra": "TRAINEE", "Hagia Sopia": "TRAINEE",
    "Heidi Suyangga": "TRAINEE", "Humaira Ramadhani": "TRAINEE", "Maxine Faye": "TRAINEE", "Mikaela Kusjanto": "TRAINEE",
    "Putry Jazyta": "TRAINEE", "Ralyne Van Irwan": "TRAINEE", "Sona Kalyana": "TRAINEE"
}

TEAM_COLORS = {
    'LOVE': '#ff1744',
    'PASSION': '#2979ff',
    'DREAM': '#00e676',
    'TRAINEE': '#9c27b0'
}

def fetch_api_data():
    """Fetch data from JKT48 API"""
    try:
        # Priority: custom (session) → dynamic (discovered) → static (hardcoded)
        all_endpoints = get_all_event_options()
        if 'custom_events' in st.session_state and st.session_state.selected_event in st.session_state.custom_events:
            api_url = st.session_state.custom_events[st.session_state.selected_event]
        elif st.session_state.selected_event in all_endpoints:
            api_url = all_endpoints[st.session_state.selected_event]
        else:
            st.error(f"Event tidak ditemukan: {st.session_state.selected_event}")
            return None
        
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('status') and data.get('data'):
            return data['data']
        return None
    except Exception as e:
        st.error(f"Error fetching API: {str(e)}")
        return None

def send_telegram_notification(message):
    """Send notification via Telegram with hardcoded credentials"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.json().get('ok', False)
    except Exception as e:
        st.error(f"Telegram error: {str(e)}")
        return False

def detect_changes(new_data):
    """Detect stock changes and send notifications"""
    if not st.session_state.previous_data:
        st.session_state.previous_data = new_data
        return []
    
    changes = []
    prev_data = st.session_state.previous_data
    
    for new_session in new_data.get('session', []):
        prev_session = next(
            (s for s in prev_data.get('session', []) 
             if s['label'] == new_session['label'] and s['date'] == new_session['date']),
            None
        )
        
        if not prev_session:
            continue
        
        for new_detail in new_session['session_detail']:
            prev_detail = next(
                (d for d in prev_session['session_detail']
                 if d['label'] == new_detail['label'] and d['jkt48_member_name'] == new_detail['jkt48_member_name']),
                None
            )
            
            if not prev_detail:
                continue
            
            new_available = new_detail['available_quota']
            prev_available = prev_detail['available_quota']
            new_sold = new_detail['tickets_sold']
            prev_sold = prev_detail['tickets_sold']
            
            # 1. Stock tersedia kembali setelah sold out (REFUND/CANCELLATION)
            if prev_available == 0 and new_available > 0:
                change = {
                    'type': 'stock_return',
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': new_session.get('date', ''),  # Event date
                    'returned_quota': new_available,
                    'old_sold': prev_sold,
                    'new_sold': new_sold,
                    'refunded_tickets': prev_sold - new_sold if new_sold < prev_sold else 0,
                    'timestamp': datetime.now()
                }
                changes.append(change)
                
                if st.session_state.notifications_enabled:
                    if change['refunded_tickets'] > 0:
                        msg = f"♻️ *STOCK KEMBALI!* (Refund/Cancel)\n{change['member']} ({change['session']})\nSold Out → {new_available} tiket tersedia\n💳 {change['refunded_tickets']} transaksi dibatalkan"
                    else:
                        msg = f"♻️ *STOCK KEMBALI!*\n{change['member']} ({change['session']})\nSold Out → {new_available} tiket tersedia\n(Kemungkinan tambahan quota)"
                    send_telegram_notification(msg)
            
            # 2. Stock increase (quota naik tapi belum pernah sold out)
            elif new_available > prev_available and prev_available > 0:
                change = {
                    'type': 'stock_increase',
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': new_session.get('date', ''),
                    'old_quota': prev_available,
                    'new_quota': new_available,
                    'difference': new_available - prev_available,
                    'timestamp': datetime.now()
                }
                changes.append(change)
                
                if st.session_state.notifications_enabled:
                    msg = f"📈 *STOCK NAIK!*\n{change['member']} ({change['session']})\n{change['old_quota']} → {change['new_quota']} (+{change['difference']})"
                    send_telegram_notification(msg)
            
            # 3. New transaction (tickets_sold bertambah)
            elif new_sold > prev_sold:
                sold_diff = new_sold - prev_sold
                change = {
                    'type': 'new_transaction',
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': new_session.get('date', ''),
                    'old_sold': prev_sold,
                    'new_sold': new_sold,
                    'tickets_bought': sold_diff,
                    'remaining': new_available,
                    'timestamp': datetime.now()
                }
                changes.append(change)
                
                if st.session_state.notifications_enabled:
                    # Only notify if significant purchase (>= 5 tickets or sold out)
                    if sold_diff >= 5 or new_available == 0:
                        msg = f"🎫 *TRANSAKSI BARU!*\n{change['member']} ({change['session']})\n{sold_diff} tiket terjual\nSisa: {new_available}"
                        send_telegram_notification(msg)
            
            # 4. Refund/Cancellation (tickets_sold berkurang)
            elif new_sold < prev_sold and prev_available > 0:
                refund_diff = prev_sold - new_sold
                change = {
                    'type': 'refund',
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': new_session.get('date', ''),
                    'old_sold': prev_sold,
                    'new_sold': new_sold,
                    'refunded_tickets': refund_diff,
                    'new_available': new_available,
                    'timestamp': datetime.now()
                }
                changes.append(change)
                
                if st.session_state.notifications_enabled:
                    # Notify for any refund
                    msg = f"💳 *REFUND/CANCEL!*\n{change['member']} ({change['session']})\n{refund_diff} transaksi dibatalkan\nStock kembali: {new_available}"
                    send_telegram_notification(msg)
            
            # 5. Sold out (available jadi 0)
            if new_available == 0 and prev_available > 0:
                change = {
                    'type': 'sold_out',
                    'member': new_detail['jkt48_member_name'],
                    'session': new_session['label'],
                    'session_date': new_session.get('date', ''),
                    'last_available': prev_available,
                    'timestamp': datetime.now()
                }
                changes.append(change)
                
                if st.session_state.notifications_enabled:
                    msg = f"🔴 *SOLD OUT!*\n{change['member']} ({change['session']})\nHabis dari {change['last_available']} tiket!"
                    send_telegram_notification(msg)
    
    if changes:
        st.session_state.change_log.extend(changes)
        st.session_state.previous_data = new_data
    
    return changes
    
    if changes:
        st.session_state.change_log.extend(changes)
        st.session_state.previous_data = new_data
    
    return changes

def create_dataframe(data):
    """Convert API data to DataFrame with +1 day event date offset"""
    rows = []
    for session in data.get('session', []):
        # Get original date and apply +1 day offset
        original_date = session['date']
        adjusted_date = get_adjusted_event_date(original_date)
        formatted_date = format_event_date(original_date)
        
        for detail in session['session_detail']:
            team = MEMBER_TEAM_MAP.get(detail['jkt48_member_name'], 'Unknown')
            total_quota = detail['tickets_sold'] + detail['available_quota']
            percentage = (detail['tickets_sold'] / total_quota * 100) if total_quota > 0 else 0
            
            rows.append({
                'Session': session['label'],
                'Date': adjusted_date,  # YYYY-MM-DD format for filtering
                'Date_Display': formatted_date,  # "Jumat, 09 Mei 2026" for display
                'Time': f"{session['start_time']} - {session['end_time']}",
                'Lane': detail['label'],
                'Member': detail['jkt48_member_name'],
                'Team': team,
                'Tickets Sold': detail['tickets_sold'],
                'Available': detail['available_quota'],
                'Total': total_quota,
                'Sold %': round(percentage, 1),
                'Status': 'Sold Out' if detail['available_quota'] == 0 else 
                         'Low Stock' if detail['available_quota'] < 20 else 'Available'
            })
    
    return pd.DataFrame(rows)

# Sidebar - Settings
with st.sidebar:
    st.header("⚙️ Settings")
    
    # Show current WIB time (no selector)
    st.subheader("🕐 Timezone")
    st.info(f"**WIB (Jakarta)** - Fixed timezone")
    current_time_wib = now_wib().strftime("%H:%M:%S")
    st.caption(f"Current time: {current_time_wib}")
    
    st.divider()
    
    # Event Selector
    st.subheader("📅 Select Event")
    
    # Merge static + dynamic (discovered) + custom (session-based)
    all_endpoints_merged = get_all_event_options()
    all_events = list(all_endpoints_merged.keys())
    if 'custom_events' in st.session_state:
        for name in st.session_state.custom_events.keys():
            if name not in all_events:
                all_events.append(name)
    
    # Make sure selected event exists in options
    if st.session_state.selected_event not in all_events:
        st.session_state.selected_event = all_events[0]
    
    selected_event = st.selectbox(
        "Event",
        options=all_events,
        index=all_events.index(st.session_state.selected_event),
        key="event_selector"
    )
    
    # Update selected event
    if selected_event != st.session_state.selected_event:
        st.session_state.selected_event = selected_event
        st.session_state.previous_data = None  # Reset previous data when changing events
        st.rerun()

    # ── Auto-Discovered Exclusives ──────────────────────────────────────────
    with st.expander("🔍 Auto-Discovered Exclusives", expanded=False):
        discovery = load_discovery_summary()
        dynamic_ep = load_dynamic_endpoints()

        if discovery["total_known"] == 0:
            st.info("Background worker belum pernah jalan atau belum ada exclusive yang ditemukan.")
        else:
            st.markdown(f"**{discovery['total_known']} exclusive** pernah ditemukan")
            st.markdown(f"**{len(dynamic_ep)} exclusive** aktif di-monitor otomatis")
            st.divider()
            for item in discovery["items"]:
                icon = "📊" if item["monitored"] else "⚠️"
                code_str = f"`{item['code']}`" if item["code"] else "–"
                col_a, col_b = st.columns([3, 1])
                with col_a:
                    st.markdown(f"{icon} **{item['title']}**")
                    st.caption(f"Kode: {code_str} • {item['category']}")
                with col_b:
                    if item["monitored"] and item["event_name"] and item["event_name"] in all_events:
                        if st.button("Lihat →", key=f"quick_{item['code']}"):
                            st.session_state.selected_event = item["event_name"]
                            st.session_state.previous_data = None
                            st.rerun()
                    else:
                        st.caption("No code" if not item["code"] else "")

        if st.button("🔄 Refresh list", key="refresh_discovery"):
            st.rerun()
    
    # Custom API Input
    with st.expander("➕ Add Custom Event", expanded=False):
        st.markdown("""
        **Cara dapat API URL:**
        1. Buka [jkt48.com/exclusive](https://jkt48.com/exclusive)
        2. Klik event yang diinginkan
        3. Lihat URL: `jkt48.com/exclusive/EXXX`
        4. API URL: `https://jkt48.com/api/v1/exclusives/EXXX?lang=id`
        """)
        
        custom_event_name = st.text_input(
            "Event Name",
            placeholder="e.g., Ramadan Photobook 2026",
            key="custom_name_input"
        )
        
        custom_event_url = st.text_input(
            "API URL",
            placeholder="https://jkt48.com/api/v1/exclusives/EXXX?lang=id",
            key="custom_url_input"
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("➕ Add Event", use_container_width=True):
                if custom_event_name and custom_event_url:
                    # Validate URL format
                    if "jkt48.com/api/v1/exclusives" in custom_event_url:
                        # Add to session state (temporary)
                        if 'custom_events' not in st.session_state:
                            st.session_state.custom_events = {}
                        
                        st.session_state.custom_events[custom_event_name] = custom_event_url
                        st.session_state.selected_event = custom_event_name
                        st.session_state.previous_data = None
                        st.success(f"✅ Added: {custom_event_name}")
                        st.rerun()
                    else:
                        st.error("❌ Invalid URL format!")
                else:
                    st.warning("⚠️ Fill both fields!")
        
        with col2:
            if st.button("🗑️ Clear Custom", use_container_width=True):
                if 'custom_events' in st.session_state:
                    st.session_state.custom_events = {}
                    st.success("Cleared!")
                    st.rerun()
        
        # Show active custom events
        if 'custom_events' in st.session_state and st.session_state.custom_events:
            st.markdown("**Active Custom Events:**")
            for name in st.session_state.custom_events.keys():
                st.text(f"• {name}")
    
    st.divider()
    
    # Cloudflare Cookie (for waiting room bypass)
    st.subheader("🍪 Cloudflare Cookie")
    
    with st.expander("🔓 Waiting Room Bypass", expanded=False):
        st.markdown("""
        **Cara dapat cookie CF:**
        1. Buka browser → jkt48.com/exclusive
        2. Tunggu waiting room selesai
        3. Press F12 → Application tab (Chrome) atau Storage (Firefox)
        4. Cookies → jkt48.com
        5. Cari cookie yang diawali: `__cfwaitingroom`
        6. Copy **NAME** dan **VALUE** nya
        
        **Contoh:**
        - Name: `__cfwaitingroom_j4K9vP2xL7mN1R8cT5bY0zW6hQ3sD9gM8wT2vX1nF`
        - Value: `abc123def456...`
        
        ⚠️ Cookie name bisa random/berbeda setiap kali!
        """)
        
        # Load existing cookie from config
        config = load_config_from_file()
        current_cookie_name = config.get("cf_cookie_name", "")
        current_cookie_value = config.get("cf_cookie_value", "")
        
        # Cookie Name input
        cf_cookie_name = st.text_input(
            "Cookie Name (mulai dengan __cfwaitingroom)",
            value=current_cookie_name,
            placeholder="__cfwaitingroom_j4K9vP2xL7mN1R8cT5bY0zW6hQ3sD9gM8wT2vX1nF",
            key="cf_cookie_name_input"
        )
        
        # Cookie Value input
        cf_cookie_value = st.text_area(
            "Cookie Value",
            value=current_cookie_value,
            height=80,
            placeholder="abc123def456ghi789...",
            key="cf_cookie_value_input"
        )
        
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("💾 Save Cookie", key="save_cf_cookie", use_container_width=True):
                if cf_cookie_name.strip() and cf_cookie_value.strip():
                    # Validate cookie name starts with __cfwaitingroom
                    if cf_cookie_name.startswith("__cfwaitingroom"):
                        config["cf_cookie_name"] = cf_cookie_name.strip()
                        config["cf_cookie_value"] = cf_cookie_value.strip()
                        if save_config_to_file(config):
                            st.success("✅ Cookie saved! Worker will use it on next iteration (~30s)")
                    else:
                        st.error("❌ Cookie name must start with __cfwaitingroom")
                else:
                    st.warning("⚠️ Fill both cookie name and value!")
        
        with col2:
            if st.button("🗑️ Clear Cookie", key="clear_cf_cookie", use_container_width=True):
                config["cf_cookie_name"] = ""
                config["cf_cookie_value"] = ""
                if save_config_to_file(config):
                    st.success("✅ Cookie cleared!")
        
        # Show cookie status
        if current_cookie_name and current_cookie_value:
            st.info(f"""
            🍪 **Cookie active:**
            - Name: `{current_cookie_name[:30]}...`
            - Value: `{current_cookie_value[:20]}...` ({len(current_cookie_value)} chars)
            """)
        else:
            st.warning("⚠️ No cookie set - may not work during waiting room")
    
    st.divider()
    
    # Notification Settings
    st.subheader("🔔 Notifications")
    
    with st.expander("📱 Telegram Bot", expanded=False):
        st.info(f"""
        **Status:** ✅ Configured
        
        Notifications will be sent to:
        - Bot Token: `...{TELEGRAM_BOT_TOKEN[-10:]}`
        - Chat ID: `{TELEGRAM_CHAT_ID}`
        """)
        
        # Test notification button
        if st.button("🧪 Test Notification", use_container_width=True, key="test_telegram"):
            test_msg = f"🧪 *Test Notification*\n\nJKT48 Monitor aktif!\n\nTime: {now_wib().strftime('%H:%M:%S WIB')}"
            if send_telegram_notification(test_msg):
                st.success("✅ Test notification sent!")
            else:
                st.error("❌ Failed to send test notification")
        
        # Enable/disable toggle
        st.session_state.notifications_enabled = st.checkbox(
            "Enable Notifications",
            value=st.session_state.get('notifications_enabled', False),
            help="Receive Telegram alerts for stock changes"
        )
    
    st.divider()
    
    # Background Worker Control
    st.subheader("🤖 Background Monitor (24/7)")
    
    # Check worker status (check multiple files to determine if worker is running)
    
    worker_running = False
    last_update = "Never"
    last_update_seconds = None
    
    # Check if any worker files exist and when they were last modified
    for filepath in ["/mnt/user-data/outputs/previous_data.json", CHANGE_LOG_FILE, CONFIG_FILE]:
        if os.path.exists(filepath):
            try:
                mtime = os.path.getmtime(filepath)
                current_time = time.time()
                last_update_seconds = current_time - mtime
                
                # Format as "X seconds/minutes/hours ago"
                if last_update_seconds < 60:
                    last_update = f"{int(last_update_seconds)}s ago"
                elif last_update_seconds < 3600:
                    last_update = f"{int(last_update_seconds / 60)}m ago"
                else:
                    hours = int(last_update_seconds / 3600)
                    minutes = int((last_update_seconds % 3600) / 60)
                    last_update = f"{hours}h {minutes}m ago"
                
                # Worker is considered running if file updated within last 2 minutes
                if last_update_seconds < 120:
                    worker_running = True
                
                break  # Use first found file
            except:
                pass
    
    worker_status = "🟢 Running" if worker_running else "🔴 Stopped"
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Worker Status", worker_status)
    with col2:
        st.metric("Last Update", last_update)
    
    with st.expander("ℹ️ Info & Control", expanded=False):
        st.markdown("""
        **Background worker jalan 24/7** di server:
        - ✅ Monitor stock setiap 30 detik
        - ✅ Detect changes otomatis  
        - ✅ Send notifications
        - ✅ Log persistent (semua user lihat)
        
        **Browser TIDAK perlu terbuka!**
        """)
        
        # Load config
        bg_config = load_config_from_file()
        
        # Monitored events
        monitored_events = st.multiselect(
            "Events to Monitor",
            options=list(API_ENDPOINTS.keys()),
            default=bg_config.get("monitored_events", list(API_ENDPOINTS.keys())),
            key="bg_events"
        )
        
        if st.button("💾 Update"):
            bg_config["monitored_events"] = monitored_events
            if save_config_to_file(bg_config):
                st.success("Updated! Takes effect in ~30s")
    
    # Monitor settings
    st.subheader("🔄 Dashboard Refresh")
    auto_refresh = st.checkbox("Enable Auto-Refresh", value=False)
    
    if auto_refresh:
        refresh_interval = st.slider("Interval (seconds)", 10, 300, 30)
        st.session_state.notifications_enabled = st.checkbox(
            "Enable Notifications",
            value=st.session_state.notifications_enabled
        )
        
        # Auto refresh
        st_autorefresh(interval=refresh_interval * 1000, key="data_refresh")
        
        st.info(f"🔄 Refreshing every {refresh_interval}s")
        if st.session_state.notifications_enabled:
            st.success("🔔 Notifications ON")
    else:
        st.session_state.notifications_enabled = False

# Main content
st.title("🎵 JKT48 Stock Monitor")
st.markdown(f"**{st.session_state.selected_event}**")

# Fetch data
data = fetch_api_data()

if data:
    # Detect changes
    changes = detect_changes(data)
    
    # Show alerts for recent changes
    if changes:
        for change in changes[-3:]:  # Show last 3 changes
            if change['type'] == 'stock_increase':
                st.success(
                    f"📈 **STOCK NAIK!** {change['member']} ({change['session']}): "
                    f"{change['old_quota']} → {change['new_quota']} (+{change['difference']})"
                )
            else:
                st.error(
                    f"🔴 **SOLD OUT!** {change['member']} ({change['session']})"
                )
    
    # Create DataFrame
    df = create_dataframe(data)
    
    # Statistics
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        st.metric(
            "Total Sold",
            f"{df['Tickets Sold'].sum():,}",
            f"{df['Sold %'].mean():.1f}%"
        )
    
    with col2:
        st.metric(
            "Available",
            f"{df['Available'].sum():,}"
        )
    
    with col3:
        sold_out_count = len(df[df['Status'] == 'Sold Out'])
        st.metric(
            "Sold Out",
            sold_out_count
        )
    
    with col4:
        st.metric(
            "Changes",
            len(st.session_state.change_log)
        )
    
    with col5:
        wib = pytz.timezone('Asia/Jakarta')
        current_time_wib = datetime.now(pytz.UTC).astimezone(wib)
        st.metric(
            "Last Update (WIB)",
            current_time_wib.strftime("%H:%M:%S")
)
    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "👥 Per Team", "📋 Data Table", "📜 Change Log"])
    
    with tab1:
        col1, col2 = st.columns(2)
        
        with col1:
            # Top Members
            top_members = df.groupby('Member')['Tickets Sold'].sum().sort_values(ascending=False).head(10)
            fig = px.bar(
                x=top_members.values,
                y=top_members.index,
                orientation='h',
                title="Top 10 Members - Tickets Sold",
                labels={'x': 'Tickets Sold', 'y': 'Member'},
                color=top_members.values,
                color_continuous_scale='viridis'
            )
            fig.update_layout(showlegend=False, height=400)
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            # Status distribution
            status_counts = df['Status'].value_counts()
            fig = px.pie(
                values=status_counts.values,
                names=status_counts.index,
                title="Status Distribution",
                color=status_counts.index,
                color_discrete_map={
                    'Available': '#4caf50',
                    'Low Stock': '#ffd93d',
                    'Sold Out': '#f44336'
                }
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        # Team analysis
        team_stats = df.groupby('Team').agg({
            'Tickets Sold': 'sum',
            'Available': 'sum',
            'Member': 'nunique'
        }).reset_index()
        team_stats.columns = ['Team', 'Total Sold', 'Available', 'Member Count']
        team_stats['Avg per Member'] = (team_stats['Total Sold'] / team_stats['Member Count']).round(0)
        
        # Team sales chart
        fig = px.bar(
            team_stats,
            x='Team',
            y='Total Sold',
            title="Sales per Team",
            color='Team',
            color_discrete_map=TEAM_COLORS,
            text='Total Sold'
        )
        fig.update_traces(textposition='outside')
        fig.update_layout(showlegend=False, height=400)
        st.plotly_chart(fig, use_container_width=True)
        
        # Team cards
        cols = st.columns(len(team_stats))
        for idx, (_, team) in enumerate(team_stats.iterrows()):
            with cols[idx]:
                st.markdown(f"""
                <div style="background: {TEAM_COLORS.get(team['Team'], '#667eea')}; 
                            padding: 1rem; border-radius: 0.5rem; color: white;">
                    <h3 style="margin: 0;">Team {team['Team']}</h3>
                    <p style="font-size: 2em; margin: 0.5rem 0; font-weight: bold;">{int(team['Total Sold'])}</p>
                    <p style="margin: 0; opacity: 0.9;">{int(team['Member Count'])} members</p>
                    <p style="margin: 0; opacity: 0.9;">Avg: {int(team['Avg per Member'])}/member</p>
                </div>
                """, unsafe_allow_html=True)
        
        # Members by team
        st.subheader("Members by Team")
        for team in team_stats['Team'].unique():
            with st.expander(f"Team {team} ({len(df[df['Team'] == team]['Member'].unique())} members)"):
                team_df = df[df['Team'] == team].groupby('Member')['Tickets Sold'].sum().sort_values(ascending=False)
                st.dataframe(
                    team_df.reset_index(),
                    use_container_width=True,
                    hide_index=True
                )
    
    with tab3:
        # Filters - 4 columns (removed Session filter)
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            # Date filter - show Indonesian format
            unique_dates_raw = sorted(df['Date'].unique(), reverse=True)
            # Create mapping of display dates to raw dates
            date_display_map = {}
            for raw_date in unique_dates_raw:
                # Find first matching row to get display format
                matching_row = df[df['Date'] == raw_date].iloc[0]
                display_date = matching_row['Date_Display']
                date_display_map[display_date] = raw_date
            
            date_filter_options = ['All Dates'] + list(date_display_map.keys())
            selected_date_display = st.selectbox(
                "Event Date",
                options=date_filter_options,
                index=0
            )
        
        with col2:
            # Member filter
            unique_members = sorted(df['Member'].unique())
            member_filter_options = ['All Members'] + list(unique_members)
            selected_member_filter = st.selectbox(
                "Member",
                options=member_filter_options,
                index=0
            )
        
        with col3:
            team_filter = st.multiselect(
                "Team",
                options=df['Team'].unique(),
                default=df['Team'].unique()
            )
        
        with col4:
            status_filter = st.multiselect(
                "Status",
                options=df['Status'].unique(),
                default=df['Status'].unique()
            )
        
        # Filtered data - apply all filters
        filtered_df = df.copy()
        
        # Date filter
        if selected_date_display != 'All Dates':
            selected_date_raw = date_display_map[selected_date_display]
            filtered_df = filtered_df[filtered_df['Date'] == selected_date_raw]
        
        # Member filter
        if selected_member_filter != 'All Members':
            filtered_df = filtered_df[filtered_df['Member'] == selected_member_filter]
        
        # Other filters (no Session filter!)
        filtered_df = filtered_df[
            (filtered_df['Team'].isin(team_filter)) &
            (filtered_df['Status'].isin(status_filter))
        ]
        
        # Prepare display dataframe with Indonesian date format
        display_df = filtered_df.copy()
        display_df['Date'] = display_df['Date_Display']  # Replace with Indonesian format
        display_df = display_df.drop(columns=['Date_Display'])  # Remove duplicate column
        
        # Display table
        st.dataframe(
            display_df.style.map(
                lambda x: 'background-color: #ffebee' if x == 'Sold Out' else
                          'background-color: #fff9c4' if x == 'Low Stock' else
                          'background-color: #e8f5e9' if x == 'Available' else '',
                subset=['Status']
            ),
            use_container_width=True,
            hide_index=True
        )
        
        st.info(f"Showing {len(filtered_df)} of {len(df)} rows")
        
        # Download button
        csv = filtered_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Download CSV",
            csv,
            f"jkt48_stock_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "text/csv",
            key='download-csv'
        )
    
    with tab4:
        st.subheader("📜 Change Log (24/7 Background Monitor)")
        
        # Check if worker is having issues (last update > 5 minutes ago)
        import time
        worker_issue = False
        if os.path.exists("/mnt/user-data/outputs/previous_data.json"):
            try:
                mtime = os.path.getmtime("/mnt/user-data/outputs/previous_data.json")
                seconds_ago = time.time() - mtime
                if seconds_ago > 300:  # 5 minutes
                    worker_issue = True
                    st.warning(f"⚠️ Background worker belum update selama {int(seconds_ago/60)} menit. Kemungkinan API JKT48 sedang down atau ada waiting room aktif. Worker akan retry otomatis.")
            except:
                pass
        
        if not worker_issue:
            col_info1, col_info2 = st.columns([3, 1])
            with col_info1:
                st.info("💡 Change log diupdate oleh background worker yang jalan 24/7 di server. Semua user melihat log yang sama!")
            with col_info2:
                if st.button("🗑️ Clear Old Log", help="Clear old log entries to show only new entries with correct date format"):
                    try:
                        # Save empty log
                        with open("/mnt/user-data/outputs/change_log.json", 'w') as f:
                            json.dump([], f)
                        st.success("✅ Old log cleared! New changes will appear with correct format.")
                        st.rerun()
                    except:
                        st.error("❌ Failed to clear log (file permission issue)")
        
        st.caption("ℹ️ Old log entries may show '(Event date not available)' - clear log to see new entries with correct Indonesian date format.")
        
        # Load change log from file
        file_change_log = load_change_log_from_file()
        
        # Merge with session changes (if any from manual refresh)
        all_changes = file_change_log + st.session_state.get('change_log', [])
        
        # Sort by timestamp (newest first) - handle both string and datetime
        def get_sort_key(change):
            ts = change.get('timestamp', '')
            if isinstance(ts, str):
                try:
                    # ISO format from background worker (already has timezone)
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    # Ensure it's timezone-aware
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=pytz.UTC)
                    return dt
                except:
                    return datetime.min.replace(tzinfo=pytz.UTC)
            elif isinstance(ts, datetime):
                # datetime object from session - make timezone-aware if not
                if ts.tzinfo is None:
                    return ts.replace(tzinfo=pytz.UTC)
                return ts
            else:
                return datetime.min.replace(tzinfo=pytz.UTC)
        
        all_changes.sort(key=get_sort_key, reverse=True)
        
        # Filter controls
        col1, col2, col3 = st.columns([2, 2, 1])
        
        with col1:
            change_filter = st.multiselect(
                "Filter by Type",
                options=['stock_return', 'refund', 'stock_increase', 'new_transaction', 'sold_out'],
                default=['stock_return', 'refund', 'stock_increase', 'new_transaction', 'sold_out'],
                format_func=lambda x: {
                    'stock_return': '♻️ Stock Kembali',
                    'refund': '💳 Refund',
                    'stock_increase': '📈 Stock Naik',
                    'new_transaction': '🎫 Transaksi',
                    'sold_out': '🔴 Sold Out'
                }.get(x, x)
            )
        
        with col2:
            # Date filter by EVENT DATE (not transaction date) - show Indonesian format
            if all_changes:
                # Get unique event dates from changes
                event_dates_raw = set()
                for change in all_changes:
                    session_date = change.get('session_date', '')
                    if session_date:
                        event_dates_raw.add(session_date)
                
                # Create display mapping
                date_display_map_log = {}
                for raw_date in sorted(list(event_dates_raw), reverse=True):
                    display_date = format_event_date(raw_date)
                    date_display_map_log[display_date] = raw_date
                
                date_options = ['All Dates'] + list(date_display_map_log.keys())
                selected_date_display_log = st.selectbox("Filter by Event Date", date_options, index=0)
                
                # Convert back to raw date for filtering
                if selected_date_display_log != 'All Dates':
                    selected_date = date_display_map_log[selected_date_display_log]
                else:
                    selected_date = "All Dates"
            else:
                selected_date = "All Dates"
        
        with col3:
            max_display = st.selectbox("Show", [10, 25, 50, 100, "All"], index=2)
        
        # Filter changes by type
        filtered_changes = [c for c in all_changes if c.get('type') in change_filter]
        
        # Filter by event date
        if selected_date != "All Dates":
            filtered_changes = [c for c in filtered_changes if c.get('session_date') == selected_date]
        
        # Limit display
        if max_display != "All":
            filtered_changes = filtered_changes[:max_display]
        
        if filtered_changes:
            st.markdown(f"**Showing {len(filtered_changes)} of {len(all_changes)} changes**")
            
            for change in filtered_changes:
                # Format timestamp with user's timezone
                timestamp_str = change.get('timestamp', '')
                ts = change.get('timestamp', '')
                
                # Parse and format transaction timestamp in WIB
                # Handle multiple formats:
                # 1. "2026-05-09T07:33:45.013670+00:00" (ISO with timezone)
                # 2. "2026-05-09T07:33:45.013670+07:00" (ISO with WIB)
                # 3. "2026-05-09 07:33:45.013670" (UTC without timezone - old format)
                # 4. "2026-05-09T07:33:45.013670" (ISO without timezone)
                if isinstance(ts, str) and ts:
                    try:
                        # Replace space with T for ISO parsing
                        ts_clean = ts.replace(' ', 'T').replace('Z', '+00:00')
                        dt = datetime.fromisoformat(ts_clean)
                        
                        # If no timezone info, assume it's UTC (old format)
                        if dt.tzinfo is None:
                            dt = pytz.UTC.localize(dt)
                        
                        # Convert to WIB
                        wib_dt = dt.astimezone(WIB)
                        # Format: "DD/MM/YYYY HH:MM:SS"
                        timestamp = wib_dt.strftime("%d/%m/%Y %H:%M:%S")
                    except Exception as e:
                        # Fallback: try basic parsing
                        try:
                            # Format: "2026-05-09 07:33:45.013670"
                            dt = datetime.strptime(ts.split('.')[0], "%Y-%m-%d %H:%M:%S")
                            dt = pytz.UTC.localize(dt)  # Assume UTC for old entries
                            wib_dt = dt.astimezone(WIB)
                            timestamp = wib_dt.strftime("%d/%m/%Y %H:%M:%S")
                        except:
                            timestamp = timestamp_str
                else:
                    timestamp = str(ts) if ts else ""
                
                # Get event date from session_date and apply +1 day offset
                event_date_str = change.get('session_date', '')
                if event_date_str:
                    # Apply +1 day offset and format to Indonesian
                    date_display = format_event_date(event_date_str)
                else:
                    # Fallback for old entries without session_date
                    # Try to get from 'date' or other fields
                    old_date = change.get('date', '') or change.get('event_date', '')
                    if old_date:
                        try:
                            # Parse various formats
                            if 'T' in old_date:
                                # ISO format: "2026-05-12T17:00:00.000Z"
                                dt = datetime.fromisoformat(old_date.replace('Z', '+00:00'))
                                date_str = dt.strftime('%Y-%m-%d')
                            else:
                                date_str = old_date
                            
                            # Apply +1 day offset
                            date_display = format_event_date(date_str)
                        except:
                            date_display = old_date
                    else:
                        date_display = ""
                
                # Fix Unknown Event issue
                event_name = change.get('event', '')
                if not event_name or event_name == 'Unknown Event':
                    # Default to current monitored event
                    event_name = "We Are Love, Dream, Passion on Fire"
                session_info = change.get('session', 'N/A')
                
                # Stock Return (dari sold out ke available)
                if change['type'] == 'stock_return':
                    refund_text = ""
                    if change.get('refunded_tickets', 0) > 0:
                        refund_text = f"<br>💳 {change['refunded_tickets']} transaksi dibatalkan"
                    
                    st.markdown(f"""
                    <div style="background: #ff9800; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                        ♻️ <strong>Transaksi: {timestamp}</strong><br>
                        📅 <strong>Event: {date_display}</strong><br>
                        <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                        🎭 Sesi: {session_info}<br>
                        Sold Out → {change.get('returned_quota', 0)} tiket tersedia{refund_text}
                    </div>
                    """, unsafe_allow_html=True)
                
                # Stock Increase (normal)
                elif change['type'] == 'stock_increase':
                    st.markdown(f"""
                    <div class="stock-increase" style="margin-bottom: 0.5rem;">
                        📈 <strong>Transaksi: {timestamp}</strong><br>
                        📅 <strong>Event: {date_display}</strong><br>
                        <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                        🎭 Sesi: {session_info}<br>
                        Stock: {change.get('old_quota', 0)} → {change.get('new_quota', 0)} (+{change.get('difference', 0)})
                    </div>
                    """, unsafe_allow_html=True)
                
                # New Transaction
                elif change['type'] == 'new_transaction':
                    st.markdown(f"""
                    <div style="background: #2196f3; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                        🎫 <strong>Transaksi: {timestamp}</strong><br>
                        📅 <strong>Event: {date_display}</strong><br>
                        <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                        🎭 Sesi: {session_info}<br>
                        {change.get('tickets_bought', 0)} tiket terjual ({change.get('old_sold', 0)} → {change.get('new_sold', 0)})<br>
                        Sisa stock: {change.get('remaining', 0)}
                    </div>
                    """, unsafe_allow_html=True)
                
                # Refund/Cancellation (belum sold out)
                elif change['type'] == 'refund':
                    st.markdown(f"""
                    <div style="background: #9c27b0; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                        💳 <strong>Transaksi: {timestamp}</strong><br>
                        📅 <strong>Event: {date_display}</strong><br>
                        <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                        🎭 Sesi: {session_info}<br>
                        {change.get('refunded_tickets', 0)} transaksi dibatalkan<br>
                        Stock kembali: {change.get('new_available', 0)}
                    </div>
                    """, unsafe_allow_html=True)
                
                # Sold Out
                elif change['type'] == 'sold_out':
                    st.markdown(f"""
                    <div class="sold-out" style="margin-bottom: 0.5rem;">
                        🔴 <strong>Transaksi: {timestamp}</strong><br>
                        📅 <strong>Event: {date_display}</strong><br>
                        <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                        🎭 Sesi: {session_info}<br>
                        SOLD OUT dari {change.get('last_available', 'N/A')} tiket!
                    </div>
                    """, unsafe_allow_html=True)
            
            # Export and Clear buttons
            st.divider()
            col1, col2, col3 = st.columns([2, 1, 1])
            with col2:
                if st.button("🗑️ Clear Old Logs", help="Remove old log entries with incompatible date format"):
                    # Keep only entries with session_date field (new format)
                    new_format_changes = [c for c in all_changes if c.get('session_date')]
                    
                    # Save cleaned log
                    try:
                        with open("/mnt/user-data/outputs/change_log.json", 'w') as f:
                            json.dump(new_format_changes, f, indent=2)
                        st.success(f"✅ Cleared {len(all_changes) - len(new_format_changes)} old entries!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            
            with col3:
                if st.button("📥 Export CSV"):
                    df_changes = pd.DataFrame(filtered_changes)
                    csv = df_changes.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "Download CSV",
                        csv,
                        f"change_log_{now_wib().strftime('%Y%m%d_%H%M')}.csv",
                        "text/csv"
                    )
        else:
            st.info("No changes detected yet. Background worker is monitoring 24/7!")

else:
    st.error("❌ Failed to fetch data from API")
    st.info("The app will retry automatically if auto-refresh is enabled.")

# Footer
st.divider()
st.markdown("""
<div style="text-align: center; color: #666; padding: 1rem;">
    Made with ❤️ for JKT48 fans | Auto-refresh to monitor stock changes
</div>
""", unsafe_allow_html=True)
