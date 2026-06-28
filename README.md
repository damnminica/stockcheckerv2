# 🎵 JKT48 Stock Monitor - Streamlit App

Auto-monitor JKT48 ticket stock changes with real-time notifications to Telegram & WhatsApp.

## ✨ Features

- 📊 **Real-time Dashboard** - Live stock monitoring
- 🔔 **Smart Notifications** - Alert via Telegram/WhatsApp saat stock naik
- 👥 **Team Analysis** - Analisis per tim (LOVE, PASSION, DREAM, TRAINEE)
- 📈 **Charts & Visualizations** - Interactive charts dengan Plotly
- 🔄 **Auto-Refresh** - Customizable refresh interval (10-300 detik)
- 📋 **Change Log** - History semua perubahan stock
- 💾 **Export CSV** - Download data untuk analisis

## 🚀 Quick Start - Local

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run App

```bash
streamlit run jkt48_stock_monitor.py
```

App akan buka di: `http://localhost:8501`

## ☁️ Deploy to Streamlit Cloud (FREE!)

### Step 1: Prepare Repository

```bash
# Create new GitHub repo
git init
git add jkt48_stock_monitor.py requirements.txt README.md
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/jkt48-stock-monitor.git
git push -u origin main
```

### Step 2: Deploy

1. Buka [share.streamlit.io](https://share.streamlit.io)
2. Login dengan GitHub
3. Klik "New app"
4. Pilih repository: `jkt48-stock-monitor`
5. Main file path: `jkt48_stock_monitor.py`
6. Klik "Deploy"!

**Boom!** App live dalam 2 menit! 🎉

URL: `https://YOUR_USERNAME-jkt48-stock-monitor.streamlit.app`

## 📱 Setup Notifications

### Telegram Bot

1. Chat [@BotFather](https://t.me/BotFather)
2. Buat bot: `/newbot`
3. Copy **Bot Token**
4. Chat [@userinfobot](https://t.me/userinfobot) untuk dapat **Chat ID**
5. Paste di sidebar app → Test → Save

### WhatsApp

1. Save nomor: `+34 644 44 80 10`
2. Kirim: `I allow callmebot to send me messages`
3. Copy **API Key** dari balasan
4. Paste di sidebar app → Test → Save

## 🔄 Auto-Monitoring

1. Enable "Auto-Refresh" di sidebar
2. Set interval (30 detik recommended)
3. Enable "Notifications"
4. Biarkan tab terbuka!

**App akan:**
- Auto-check API setiap interval
- Detect perubahan stock
- Kirim notif ke Telegram/WhatsApp
- Log semua changes

## 📊 Dashboard Features

### Tab 1: Dashboard
- Top 10 Members chart
- Status distribution pie chart
- Real-time statistics

### Tab 2: Per Team
- Sales per team bar chart
- Team cards dengan stats
- Member list per team

### Tab 3: Data Table
- Filter by session, team, status
- Color-coded status
- Export to CSV

### Tab 4: Change Log
- History semua perubahan
- Timestamp setiap change
- Clear log button

## 🎯 Use Cases

### Use Case 1: Personal Monitoring
```
1. Setup notifications
2. Enable auto-refresh (30s)
3. Leave tab open
4. Get alerts on phone!
```

### Use Case 2: Community Dashboard
```
1. Deploy to Streamlit Cloud
2. Share link ke komunitas
3. Everyone can monitor real-time
4. Setup notifications for group
```

### Use Case 3: Data Analysis
```
1. Enable auto-refresh
2. Let it run for hours
3. Download CSV dari Change Log
4. Analyze patterns
```

## 💡 Pro Tips

### Tip 1: Multiple Devices
- Deploy ke Streamlit Cloud
- Access dari laptop, HP, tablet
- Notifications sync ke semua device

### Tip 2: Team Monitoring
- Setup Telegram group
- Add bot ke group
- Get group Chat ID
- Everyone gets alerts!

### Tip 3: Data Collection
- Run 24/7 di Streamlit Cloud (FREE!)
- Collect change log for days
- Export CSV for analysis
- Find patterns (best time to buy)

## 🛠️ Customization

### Change Refresh Interval
```python
# In sidebar
refresh_interval = st.slider("Interval (seconds)", 10, 300, 30)
```

### Add More Teams
```python
# Update MEMBER_TEAM_MAP
MEMBER_TEAM_MAP = {
    "New Member": "NEW_TEAM",
    # ... more members
}

# Update TEAM_COLORS
TEAM_COLORS = {
    'NEW_TEAM': '#your_color',
}
```

### Custom Notifications
```python
def send_custom_notification(change):
    # Your custom logic
    if change['difference'] > 10:
        send_telegram_notification("BIG STOCK INCREASE!")
```

## 🐛 Troubleshooting

### API Fetch Failed
- ✅ Normal! Streamlit Cloud has no CORS issues
- ✅ App auto-retry setiap refresh interval
- ✅ Check API status: https://jkt48.com/api/v1/exclusives/EXBE10?lang=id

### Telegram Not Sending
- ✅ Check bot token benar
- ✅ Chat /start ke bot dulu
- ✅ Test di sidebar

### WhatsApp Not Sending
- ✅ Phone format: +6281234567890 (with +)
- ✅ API key exact match
- ✅ Rate limit: max ~30 pesan/jam

### App Sleeping (Streamlit Cloud)
- ✅ Free tier sleep after inactivity
- ✅ Wakes up saat ada visitor
- ✅ Upgrade untuk 24/7 uptime

## 📈 Advanced: Run 24/7 Background

### Option 1: GitHub Actions (FREE)

```yaml
# .github/workflows/monitor.yml
name: Monitor Stock
on:
  schedule:
    - cron: '*/5 * * * *'  # Every 5 minutes
jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - run: pip install -r requirements.txt
      - run: python monitor_script.py
```

### Option 2: Replit (Always On)
- Deploy ke Replit
- Enable "Always On" ($5/month)
- Runs 24/7 with notifications

### Option 3: Railway/Render
- Free tier with 24/7 uptime
- Auto-deploy from GitHub
- Better than Streamlit Cloud for background tasks

## 🔐 Security

**Never commit secrets!**

Use `.streamlit/secrets.toml` for production:

```toml
[telegram]
token = "your_bot_token"
chat_id = "your_chat_id"

[whatsapp]
phone = "+6281234567890"
apikey = "your_api_key"
```

Then access in code:
```python
import streamlit as st
telegram_token = st.secrets["telegram"]["token"]
```

## 📝 License

MIT License - Free to use and modify!

## 🙏 Credits

- JKT48 Official API
- Streamlit for amazing framework
- Telegram Bot API
- CallMeBot for WhatsApp API

## 🔗 Links

- [Streamlit Docs](https://docs.streamlit.io)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/)

---

Made with ❤️ for JKT48 fans

**Star ⭐ this repo if useful!**
# stockcheckerv2
# stockcheckerv2
