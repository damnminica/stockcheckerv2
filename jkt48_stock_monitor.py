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
import proxy_pool
from jkt48_schema import to_bonus_url, normalize_bonus

# Constants
WIB = pytz.timezone('Asia/Jakarta')
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Date formatting functions
# Static Indonesian names — no locale.setlocale() (global + slow, dulu dipanggil per-baris)
_HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_BULAN_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]

from functools import lru_cache

@lru_cache(maxsize=512)
def format_event_date(api_date_str):
    """Convert API date (YYYY-MM-DD) to 'Jumat, 09 Mei 2026' with +1 day offset."""
    try:
        date_obj = datetime.strptime(api_date_str, '%Y-%m-%d') + timedelta(days=1)
        return f"{_HARI_ID[date_obj.weekday()]}, {date_obj.day:02d} {_BULAN_ID[date_obj.month]} {date_obj.year}"
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

# Semua event dikelola otomatis oleh exclusive_discovery — tidak ada hardcode

# File paths for background worker
CHANGE_LOG_FILE = "/mnt/user-data/outputs/change_log.json"
CONFIG_FILE = "/mnt/user-data/outputs/monitor_config.json"
DYNAMIC_ENDPOINTS_FILE = "/mnt/user-data/outputs/dynamic_endpoints.json"
KNOWN_EXCLUSIVES_FILE = "/mnt/user-data/outputs/known_exclusives.json"

@st.cache_data(ttl=30)
def load_change_log_from_file():
    """Load change log from file (written by background worker). Cached 30s (sinkron worker)."""
    try:
        if os.path.exists(CHANGE_LOG_FILE):
            with open(CHANGE_LOG_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
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
        "monitored_events": []
    }

@st.cache_data(ttl=30)
def load_dynamic_endpoints() -> dict:
    """Load auto-discovered exclusive endpoints dari background worker. Cached 30s."""
    try:
        if os.path.exists(DYNAMIC_ENDPOINTS_FILE):
            with open(DYNAMIC_ENDPOINTS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def get_all_event_options() -> dict:
    """
    Ambil semua event dari dynamic endpoints (hasil discovery).
    Custom session-based events ditangani terpisah di sidebar.
    """
    return load_dynamic_endpoints()

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
    st.session_state.selected_event = _initial_events[0] if _initial_events else None
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
        
        # Pakai endpoint /bonus (punya angka available_quota)
        api_url = to_bonus_url(api_url)
        response = requests.get(api_url, timeout=10, proxies=proxy_pool.requests_proxies())
        response.raise_for_status()
        data = response.json()

        if data.get('status') and data.get('data') is not None:
            # /bonus: array sesi -> bentuk internal seragam (available_quota, tanpa tickets_sold)
            return normalize_bonus(data['data'])
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
    """Deteksi perubahan stok (AVAILABLE-ONLY): sold_out & stock_return.
    API /bonus tidak punya tickets_sold, jadi transaksi/refund tidak dilacak."""
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

            new_available = new_detail.get('available_quota', 0)
            prev_available = prev_detail.get('available_quota', 0)
            member = new_detail['jkt48_member_name']

            # Stok kembali: habis -> tersedia lagi
            if prev_available == 0 and new_available > 0:
                changes.append({
                    'type': 'stock_return', 'member': member, 'session': new_session['label'],
                    'session_date': new_session.get('date', ''), 'returned_quota': new_available,
                    'timestamp': datetime.now(),
                })
                if st.session_state.notifications_enabled:
                    send_telegram_notification(
                        f"♻️ *STOCK KEMBALI!*\n{member} ({new_session['label']})\nSold Out → {new_available} tersedia")

            # Sold out: tersedia -> habis
            elif prev_available > 0 and new_available == 0:
                changes.append({
                    'type': 'sold_out', 'member': member, 'session': new_session['label'],
                    'session_date': new_session.get('date', ''), 'last_available': prev_available,
                    'timestamp': datetime.now(),
                })
                if st.session_state.notifications_enabled:
                    send_telegram_notification(
                        f"🔴 *SOLD OUT!*\n{member} ({new_session['label']})\nHabis dari {prev_available} tersedia!")

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
            avail = detail.get('available_quota', 0)

            rows.append({
                'Session': session['label'],
                'Date': adjusted_date,  # YYYY-MM-DD format for filtering
                'Date_Display': formatted_date,  # "Jumat, 09 Mei 2026" for display
                'Time': f"{session['start_time']} - {session['end_time']}",
                'Lane': detail['label'],
                'Member': detail['jkt48_member_name'],
                'Team': team,
                'Available': avail,
                'Status': 'Sold Out' if avail == 0 else
                         'Low Stock' if avail < 20 else 'Available'
            })

    return pd.DataFrame(rows)

# Mapping kategori API → label ringkas untuk summary
CATEGORY_DISPLAY = {
    "TWO_SHOT":          "2-Shot",
    "PHOTOCARD":         "Photocard",
    "DIGITAL_PHOTOBOOK": "Digital Photobook",
    "VIDEO_CALL":        "Video Call",
    "MEET_AND_GREET":    "Meet & Greet",
    "HANDSHAKE":         "Handshake",
}

# Urutan tampilan di summary (3 kategori utama dulu)
CATEGORY_ORDER = ["TWO_SHOT", "PHOTOCARD", "DIGITAL_PHOTOBOOK", "VIDEO_CALL", "MEET_AND_GREET", "HANDSHAKE"]

SUMMARY_CACHE_FILE = "/mnt/user-data/outputs/summary_cache.json"

@st.cache_data(ttl=30)
def load_summary_cache():
    """
    Baca summary_cache.json yang ditulis background_monitor setiap 30 detik.
    Cached 30s — dashboard tidak hit API JKT48, semua data dari worker.
    """
    try:
        if os.path.exists(SUMMARY_CACHE_FILE):
            with open(SUMMARY_CACHE_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def render_summary_page():
    """Landing page: summary semua exclusive per kategori × tim × member"""
    st.title("📊 JKT48 Exclusive — Summary")

    cache = load_summary_cache()

    col_h1, col_h2 = st.columns([3, 1])
    with col_h1:
        if cache:
            st.caption(f"Data dari background worker • Last update: **{cache.get('updated_at_wib', '–')}** • {cache.get('total_events', 0)} event aktif")
        else:
            st.caption("Menunggu data dari background worker...")
    with col_h2:
        if st.button("🔄 Refresh", key="summary_refresh"):
            st.rerun()

    if not cache or not cache.get('rows'):
        st.warning("Background worker belum menulis data. Tunggu ~30 detik setelah deploy, lalu refresh.")
        st.info("Pastikan `background_monitor.py` sudah jalan di Railway.")
        return

    rows = cache['rows']
    df_all = pd.DataFrame(rows)

    # Guard: cache format lama (punya tickets_sold, belum ada slots/so_slots) —
    # bisa muncul beberapa detik setelah deploy sebelum worker menimpanya.
    required_cols = {'available', 'slots', 'so_slots', 'all_sold_out', 'category_raw', 'team', 'member'}
    if not required_cols.issubset(df_all.columns):
        st.info("Menyinkronkan format data baru dari worker… tunggu ~30 detik lalu refresh.")
        return

    # ── Tabs per kategori ─────────────────────────────────────────────────
    # Urutkan kategori yang tersedia sesuai CATEGORY_ORDER
    cats_available = df_all['category_raw'].unique().tolist()
    cats_ordered = [c for c in CATEGORY_ORDER if c in cats_available]
    cats_ordered += [c for c in cats_available if c not in cats_ordered]  # kategori lain di belakang

    cat_icons  = {"TWO_SHOT": "📸", "PHOTOCARD": "🃏", "DIGITAL_PHOTOBOOK": "📖",
                  "VIDEO_CALL": "📹", "MEET_AND_GREET": "🤝", "HANDSHAKE": "🤝"}
    tab_labels = [f"{cat_icons.get(c, '💎')} {CATEGORY_DISPLAY.get(c, c)}" for c in cats_ordered]

    # ── Top-level metrics (cross-kategori) ───────────────────────────────
    total_avail    = df_all['available'].sum()
    total_slots    = df_all['slots'].sum()
    total_so_slots = df_all['so_slots'].sum()
    total_so       = df_all[df_all['all_sold_out']]['member'].nunique()
    n_events       = cache.get('total_events', 0)
    n_members      = df_all['member'].nunique()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Tersedia",  f"{total_avail:,}")
    c2.metric("Slot Sold Out",   f"{total_so_slots:,}/{total_slots:,}")
    c3.metric("Member Sold Out", f"{total_so}")
    c4.metric("Exclusive Aktif", f"{n_events}")
    c5.metric("Member Terlibat", f"{n_members}")

    st.divider()

    if not tab_labels:
        st.info("Belum ada kategori yang terdeteksi.")
        return

    tabs = st.tabs(tab_labels)

    for tab, cat_raw in zip(tabs, cats_ordered):
        with tab:
            df_cat = df_all[df_all['category_raw'] == cat_raw].copy()

            # Kumpulkan semua event titles dalam kategori ini (field event_titles adalah list)
            all_titles = set()
            for titles in df_cat['event_titles']:
                if isinstance(titles, list):
                    all_titles.update(titles)
                elif isinstance(titles, str):
                    all_titles.add(titles)
            if len(all_titles) > 1:
                st.caption(f"Event: {' • '.join(sorted(all_titles))}")

            # ── Metric ringkas per kategori ───────────────────────────────
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tersedia",       f"{df_cat['available'].sum():,}")
            m2.metric("Total Slot",     f"{df_cat['slots'].sum():,}")
            m3.metric("Slot Sold Out",  f"{df_cat['so_slots'].sum():,}")
            so_count = df_cat[df_cat['all_sold_out']].shape[0]
            m4.metric("Member Sold Out", f"{so_count}")

            st.divider()

            # ── Per Tim ───────────────────────────────────────────────────
            team_order = ['LOVE', 'PASSION', 'DREAM', 'TRAINEE', 'Unknown']
            teams_present = [t for t in team_order if t in df_cat['team'].unique()]

            for team in teams_present:
                df_team = df_cat[df_cat['team'] == team].copy()
                df_team = df_team.sort_values('available', ascending=False)

                color = TEAM_COLORS.get(team, '#888888')
                team_avail    = df_team['available'].sum()
                team_slots    = df_team['slots'].sum()
                team_so_slots = df_team['so_slots'].sum()
                team_so       = df_team[df_team['all_sold_out']].shape[0]

                # Header tim + grid kartu digabung jadi SATU st.markdown per tim (grid CSS responsif).
                cards = []
                for row in df_team.to_dict('records'):
                    avail = row['available']
                    is_so = row['all_sold_out']

                    if is_so:
                        card_bg = "#ffebee"
                        badge = "<span style='background:#f44336;color:white;padding:2px 7px;border-radius:10px;font-size:0.72em;font-weight:700;'>SOLD OUT</span>"
                    elif avail <= 5:
                        card_bg = "#fff8e1"
                        badge = f"<span style='background:#ff9800;color:white;padding:2px 7px;border-radius:10px;font-size:0.72em;font-weight:700;'>SISA {avail}</span>"
                    else:
                        card_bg = "#f5f5f5"
                        badge = "<span style='background:#4caf50;color:white;padding:2px 7px;border-radius:10px;font-size:0.72em;'>TERSEDIA</span>"

                    cards.append(
                        f"<div style='background:{card_bg};border-radius:8px;padding:10px 12px;min-height:78px;'>"
                        f"<div style='font-weight:600;font-size:0.88em;margin-bottom:4px;color:#222;'>{row['member']}</div>"
                        f"{badge}"
                        f"<div style='margin-top:6px;font-size:0.82em;color:#555;'>Sisa stok: {avail:,}</div>"
                        f"</div>"
                    )

                header = (
                    f"<div style='background:{color}22;border-left:4px solid {color};"
                    f"padding:10px 14px;border-radius:6px;margin-bottom:8px;'>"
                    f"<span style='color:{color};font-weight:700;font-size:1.05em;'>Team {team}</span>"
                    f"&nbsp;&nbsp;<span style='color:#888;font-size:0.9em;'>"
                    f"{len(df_team)} member · {team_avail:,} tersisa · {team_so_slots:,}/{team_slots:,} slot habis · {team_so} member habis"
                    f"</span></div>"
                )
                grid = (
                    "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));"
                    "gap:8px;margin-bottom:16px;'>" + "".join(cards) + "</div>"
                )
                st.markdown(header + grid, unsafe_allow_html=True)

# Sidebar - Settings
with st.sidebar:
    st.header("⚙️ Settings")

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
    # Monitor settings
    st.subheader("🔄 Dashboard Refresh")
    auto_refresh = st.checkbox("Enable Auto-Refresh", value=False)
    
    if auto_refresh:
        refresh_interval = st.slider("Interval (seconds)", 10, 300, 30)
        st.session_state.notifications_enabled = st.checkbox(
            "Enable Notifications",
            value=st.session_state.notifications_enabled
        )
        # NB: st_autorefresh dipanggil di branch Detail (bukan di sini) supaya
        # tidak dobel dengan summary_autorefresh saat mode Summary aktif.
        st.info(f"🔄 Refreshing every {refresh_interval}s")
        if st.session_state.notifications_enabled:
            st.success("🔔 Notifications ON")
    else:
        st.session_state.notifications_enabled = False

# Main content
st.title("🎵 JKT48 Stock Monitor")

# ── Mode selector ─────────────────────────────────────────────────────────
view_mode = st.radio(
    "Tampilan",
    ["📊 Summary Semua Exclusive", "🔍 Detail per Event"],
    horizontal=True,
    label_visibility="collapsed"
)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════
# LANDING PAGE — Summary semua exclusive per kategori
# ═══════════════════════════════════════════════════════════════════════════
if view_mode == "📊 Summary Semua Exclusive":
    # Auto-refresh setiap 30 detik — sinkron dengan interval background worker
    st_autorefresh(interval=30_000, key="summary_autorefresh")
    render_summary_page()

# ═══════════════════════════════════════════════════════════════════════════
# DETAIL PAGE — per event seperti semula
# ═══════════════════════════════════════════════════════════════════════════
else:
    # Auto-refresh khusus mode Detail (satu-satunya timer di mode ini)
    if auto_refresh:
        st_autorefresh(interval=refresh_interval * 1000, key="data_refresh")

    if st.session_state.selected_event is None:
        st.info("Belum ada exclusive yang dimonitor. Tunggu background worker jalan pertama kali.")
        st.stop()

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
                "Total Tersedia",
                f"{df['Available'].sum():,}"
            )

        with col2:
            st.metric(
                "Member / Slot",
                f"{df['Member'].nunique()} / {len(df)}"
            )

        with col3:
            sold_out_count = len(df[df['Status'] == 'Sold Out'])
            st.metric(
                "Slot Sold Out",
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
                # Top Members by sisa stok
                top_members = df.groupby('Member')['Available'].sum().sort_values(ascending=False).head(10)
                fig = px.bar(
                    x=top_members.values,
                    y=top_members.index,
                    orientation='h',
                    title="Top 10 Members - Sisa Stok Terbanyak",
                    labels={'x': 'Sisa Stok', 'y': 'Member'},
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
            # Team analysis (available-only)
            team_stats = df.groupby('Team').agg({
                'Available': 'sum',
                'Member': 'nunique'
            }).reset_index()
            team_stats.columns = ['Team', 'Available', 'Member Count']
            team_stats['Avg per Member'] = (team_stats['Available'] / team_stats['Member Count']).round(0)

            # Available per team chart
            fig = px.bar(
                team_stats,
                x='Team',
                y='Available',
                title="Sisa Stok per Team",
                color='Team',
                color_discrete_map=TEAM_COLORS,
                text='Available'
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
                        <p style="font-size: 2em; margin: 0.5rem 0; font-weight: bold;">{int(team['Available'])}</p>
                        <p style="margin: 0; opacity: 0.9;">{int(team['Member Count'])} members</p>
                        <p style="margin: 0; opacity: 0.9;">Avg sisa: {int(team['Avg per Member'])}/member</p>
                    </div>
                    """, unsafe_allow_html=True)

            # Members by team
            st.subheader("Members by Team")
            for team in team_stats['Team'].unique():
                with st.expander(f"Team {team} ({len(df[df['Team'] == team]['Member'].unique())} members)"):
                    team_df = df[df['Team'] == team].groupby('Member')['Available'].sum().sort_values(ascending=False)
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

                # Kumpulkan semua kartu ke satu string → 1 st.markdown (bukan N).
                blocks = []
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
                        
                        blocks.append(f"""
                        <div style="background: #ff9800; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                            ♻️ <strong>Transaksi: {timestamp}</strong><br>
                            📅 <strong>Event: {date_display}</strong><br>
                            <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                            🎭 Sesi: {session_info}<br>
                            Sold Out → {change.get('returned_quota', 0)} tiket tersedia{refund_text}
                        </div>
                        """)
                    
                    # Stock Increase (normal)
                    elif change['type'] == 'stock_increase':
                        blocks.append(f"""
                        <div class="stock-increase" style="margin-bottom: 0.5rem;">
                            📈 <strong>Transaksi: {timestamp}</strong><br>
                            📅 <strong>Event: {date_display}</strong><br>
                            <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                            🎭 Sesi: {session_info}<br>
                            Stock: {change.get('old_quota', 0)} → {change.get('new_quota', 0)} (+{change.get('difference', 0)})
                        </div>
                        """)
                    
                    # New Transaction
                    elif change['type'] == 'new_transaction':
                        blocks.append(f"""
                        <div style="background: #2196f3; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                            🎫 <strong>Transaksi: {timestamp}</strong><br>
                            📅 <strong>Event: {date_display}</strong><br>
                            <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                            🎭 Sesi: {session_info}<br>
                            {change.get('tickets_bought', 0)} tiket terjual ({change.get('old_sold', 0)} → {change.get('new_sold', 0)})<br>
                            Sisa stock: {change.get('remaining', 0)}
                        </div>
                        """)
                    
                    # Refund/Cancellation (belum sold out)
                    elif change['type'] == 'refund':
                        blocks.append(f"""
                        <div style="background: #9c27b0; color: white; padding: 0.5rem 1rem; border-radius: 0.5rem; font-weight: bold; margin-bottom: 0.5rem;">
                            💳 <strong>Transaksi: {timestamp}</strong><br>
                            📅 <strong>Event: {date_display}</strong><br>
                            <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                            🎭 Sesi: {session_info}<br>
                            {change.get('refunded_tickets', 0)} transaksi dibatalkan<br>
                            Stock kembali: {change.get('new_available', 0)}
                        </div>
                        """)
                    
                    # Sold Out
                    elif change['type'] == 'sold_out':
                        blocks.append(f"""
                        <div class="sold-out" style="margin-bottom: 0.5rem;">
                            🔴 <strong>Transaksi: {timestamp}</strong><br>
                            📅 <strong>Event: {date_display}</strong><br>
                            <strong>[{event_name}] {change.get('member', 'N/A')}</strong><br>
                            🎭 Sesi: {session_info}<br>
                            SOLD OUT dari {change.get('last_available', 'N/A')} tiket!
                        </div>
                        """)

                # Render semua kartu change log sekaligus (1 elemen, bukan ratusan)
                st.markdown("".join(blocks), unsafe_allow_html=True)

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
    
