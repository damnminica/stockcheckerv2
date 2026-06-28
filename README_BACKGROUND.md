# 🚀 JKT48 Stock Monitor - Background Worker Setup

## 📋 **ARSITEKTUR BARU:**

```
Railway Server (Always On)
│
├── background_monitor.py (Worker Process)
│   ├── Runs 24/7 non-stop
│   ├── Monitors API every 30s
│   ├── Detects changes
│   ├── Sends Telegram/WhatsApp notifications
│   └── Writes to change_log.json
│
└── jkt48_stock_monitor.py (Web Dashboard)
    ├── Displays real-time data
    ├── Reads change_log.json
    ├── Control panel for worker
    └── Shared log for all users
```

---

## ✅ **BENEFITS:**

### **24/7 Monitoring:**
- ✅ Browser TIDAK perlu terbuka
- ✅ Computer bisa dimatikan
- ✅ Worker jalan non-stop di server

### **Shared Change Log:**
- ✅ Semua user lihat log yang sama
- ✅ Historical data persistent
- ✅ Export to CSV

### **No Manual Refresh:**
- ✅ Worker auto-detect changes
- ✅ Instant notifications
- ✅ Dashboard bisa dibuka kapan aja

---

## 🛠️ **SETUP - Railway Deployment:**

### **Step 1: Files Needed**

```
jkt48-monitor/
├── background_monitor.py      ← NEW! Background worker
├── jkt48_stock_monitor.py     ← Updated dashboard
├── requirements.txt
├── Procfile                    ← Updated untuk 2 processes
└── README_BACKGROUND.md        ← This file
```

### **Step 2: Update Procfile**

Railway **hanya jalankan `web` process** secara default. Untuk run background worker, kita perlu config tambahan.

**Option A - Via Railway Dashboard (Recommended):**

1. Railway Dashboard → Select project
2. Settings → Deploy
3. **Custom Start Command:** 
   ```
   streamlit run jkt48_stock_monitor.py --server.port=$PORT --server.address=0.0.0.0 & python background_monitor.py
   ```

**Option B - Via Procfile (Need Railway config):**

Procfile sudah ada 2 processes:
```
web: streamlit run jkt48_stock_monitor.py --server.port=$PORT --server.address=0.0.0.0
worker: python background_monitor.py
```

Tapi Railway free tier **hanya jalankan `web`**. Untuk jalankan `worker`, butuh custom command di Option A.

---

## 🚀 **QUICK DEPLOY:**

```bash
cd ~/Desktop/jkt48-monitor

# Add new files
# (background_monitor.py sudah di folder)

# Update Procfile
cat > Procfile << 'EOF'
web: streamlit run jkt48_stock_monitor.py --server.port=$PORT --server.address=0.0.0.0 & python background_monitor.py
EOF

# Commit & Push
git add .
git commit -m "Add 24/7 background worker"
git push

# Railway auto-deploy!
```

---

## ⚙️ **CONFIGURATION:**

### **File: monitor_config.json**

Background worker baca config dari file ini:

```json
{
  "telegram": {
    "token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "enabled": true
  },
  "whatsapp": {
    "phone": "+6281234567890",
    "apikey": "YOUR_API_KEY",
    "enabled": false
  },
  "monitored_events": [
    "Event EXE588",
    "Event EX579E"
  ]
}
```

**Update via Dashboard:**
1. Buka app
2. Sidebar → "🤖 Background Monitor"
3. Expand "Info & Control"
4. Select events
5. Click "Update"

---

## 📊 **CARA PAKAI:**

### **Setup Awal:**

1. **Deploy app** ke Railway
2. **Buka dashboard** di browser
3. **Setup Telegram:**
   - Sidebar → Telegram Bot
   - Input token & chat ID
   - Test → Save
4. **Configure background worker:**
   - Sidebar → Background Monitor
   - Select events to monitor
   - Update

### **Monitoring:**

1. **Background worker** langsung jalan 24/7
2. **Notifikasi** otomatis ke Telegram/WhatsApp
3. **Change log** terupdate real-time
4. **Dashboard** bisa dibuka kapan aja untuk lihat log

### **View Change Log:**

1. Buka dashboard
2. Tab "📜 Change Log"
3. Filter by type (stock return, refund, dll)
4. Export to CSV

---

## 🔍 **TROUBLESHOOTING:**

### **Background worker tidak jalan:**

**Check logs:**
```bash
# Railway Dashboard → Deployments → View Logs
# Search for: "JKT48 Background Monitor Started"
```

**If not found:**
```bash
# Update start command di Railway Settings
streamlit run jkt48_stock_monitor.py --server.port=$PORT --server.address=0.0.0.0 & python background_monitor.py
```

### **Change log kosong:**

**Possible causes:**
1. Worker belum start
2. Belum ada changes
3. File path issue

**Solutions:**
1. Check Railway logs
2. Wait 30-60 seconds untuk first check
3. Trigger manual change (test buy ticket)

### **Notifications tidak terkirim:**

1. **Check config:**
   - Sidebar → Telegram Bot
   - Test notification
   
2. **Check background worker:**
   - Railway logs → search "Telegram" or "notification"

---

## 📁 **FILE LOCATIONS (Railway Server):**

```
/mnt/user-data/outputs/
├── change_log.json          ← Change history (shared)
├── previous_data.json       ← Last API state
└── monitor_config.json      ← Worker configuration
```

**Important:** 
- Files persist across deploys on Railway
- Shared between all users
- Backup via dashboard export

---

## 🎯 **MONITORING FLOW:**

```
Background Worker (30s loop):
│
├── Fetch API for all monitored events
├── Compare with previous_data.json
├── Detect changes (5 types)
├── Send notifications (if enabled)
├── Append to change_log.json
└── Save current state to previous_data.json

Dashboard (when user opens):
│
├── Load change_log.json
├── Display in UI with filters
└── Allow export to CSV
```

---

## 💡 **TIPS:**

### **Optimize Notifications:**

Edit `background_monitor.py` line ~180:
```python
# Only notify for significant purchases
if sold_diff >= 5 or new_available == 0:
    # Send notification
```

Change `5` to your preference (e.g., `10` for less spam).

### **Change Refresh Interval:**

Edit `background_monitor.py` line ~17:
```python
REFRESH_INTERVAL = 30  # seconds
```

Change to `60` for less frequent checks (save resources).

### **Monitor More Events:**

Add to `API_ENDPOINTS` in both files:
```python
API_ENDPOINTS = {
    "New Event": "https://jkt48.com/api/v1/exclusives/EXXX?lang=id",
    # ... existing events
}
```

---

## 🎊 **ADVANTAGES vs Browser-Based:**

| Feature | Browser | Background Worker |
|---------|---------|-------------------|
| 24/7 Monitoring | ❌ Need browser open | ✅ Always running |
| Change Log | ⚠️ Session only | ✅ Persistent |
| Multi-User | ❌ Separate logs | ✅ Shared log |
| Resource Usage | ⚠️ Client RAM | ✅ Server-side |
| Reliability | ⚠️ Tab close = stop | ✅ Non-stop |

---

## 🚀 **DEPLOYMENT CHECKLIST:**

- [ ] background_monitor.py added to project
- [ ] jkt48_stock_monitor.py updated
- [ ] Procfile updated
- [ ] Files pushed to GitHub
- [ ] Railway redeployed
- [ ] Background worker running (check logs)
- [ ] Telegram configured & tested
- [ ] Events selected in dashboard
- [ ] First change detected & logged

---

**Happy monitoring! 🎉**

For issues, check Railway logs or dashboard console.
