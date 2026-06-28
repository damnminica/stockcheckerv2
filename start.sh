#!/bin/bash
# Railway Startup Script
# Runs both Streamlit dashboard and background worker

set -e  # Exit on error

echo "============================================"
echo "🚀 JKT48 Monitor Startup Script"
echo "============================================"
echo "📍 Current directory: $(pwd)"
echo "📁 Files in directory:"
ls -la
echo "============================================"

# Install missing dependencies (backup)
echo "📦 Checking dependencies..."
pip install pytz 2>&1 | grep -i "already\|successfully" || echo "✅ pytz installed"

# Create output directory if not exists
echo "📂 Creating output directory..."
mkdir -p /mnt/user-data/outputs
echo "✅ Output directory ready"

# CRITICAL: Delete old monitor_config.json to force using new event names
# Old config may have outdated event names that won't match current API_ENDPOINTS
if [ -f "/mnt/user-data/outputs/monitor_config.json" ]; then
    echo "🔄 Removing old monitor_config.json to refresh event names..."
    rm /mnt/user-data/outputs/monitor_config.json
    echo "✅ Old config removed"
fi

# Also reset previous_data.json if event names changed (will rebuild baseline)
# Only if it has old event names
if [ -f "/mnt/user-data/outputs/previous_data.json" ]; then
    python3 -c "
import json
try:
    with open('/mnt/user-data/outputs/previous_data.json', 'r') as f:
        data = json.load(f)
    
    # Check if it has old event names
    old_names = ['Event EXE588', 'Event EX579E', 'Love Dream Passion BTS', 'We Are Love, Dream, Passion on Fire']
    has_old = any(name in data for name in old_names)
    
    if has_old:
        print('🔄 Old event names detected in previous_data.json - resetting...')
        with open('/mnt/user-data/outputs/previous_data.json', 'w') as f:
            json.dump({}, f)
        print('✅ Previous data reset (will rebuild baseline)')
    else:
        print('✅ Previous data is using current event names')
except Exception as e:
    print(f'⚠️ Could not check previous_data.json: {e}')
"
fi

# Restore change log from backup if exists (one-time restore)
if [ -f "change_log_backup.json" ] && [ ! -s "/mnt/user-data/outputs/change_log.json" ]; then
    echo "📥 Restoring change log from backup..."
    cp change_log_backup.json /mnt/user-data/outputs/change_log.json
    echo "✅ Change log restored ($(wc -l < /mnt/user-data/outputs/change_log.json) lines)"
elif [ -f "change_log_backup.json" ] && [ -s "/mnt/user-data/outputs/change_log.json" ]; then
    echo "ℹ️ Change log already exists, merging backup..."
    python3 -c "
import json
try:
    with open('/mnt/user-data/outputs/change_log.json', 'r') as f:
        existing = json.load(f)
    with open('change_log_backup.json', 'r') as f:
        backup = json.load(f)
    
    # Merge - avoid duplicates by timestamp
    existing_timestamps = {entry.get('timestamp') for entry in existing}
    new_entries = [entry for entry in backup if entry.get('timestamp') not in existing_timestamps]
    
    merged = existing + new_entries
    # Sort by timestamp (newest first)
    merged.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    
    with open('/mnt/user-data/outputs/change_log.json', 'w') as f:
        json.dump(merged, f, indent=2)
    
    print(f'✅ Merged: {len(existing)} existing + {len(new_entries)} new = {len(merged)} total')
except Exception as e:
    print(f'⚠️ Merge failed: {e}')
"
fi

# Check if background_monitor.py exists
if [ ! -f "background_monitor.py" ]; then
    echo "❌ ERROR: background_monitor.py not found!"
    exit 1
fi

# Start background worker in background
echo "============================================"
echo "📊 Starting background worker..."
python background_monitor.py &
WORKER_PID=$!
echo "✅ Worker started with PID: $WORKER_PID"

# Wait a moment for worker to initialize
echo "⏳ Waiting 2 seconds for worker to initialize..."
sleep 2

# Check if worker is still running
if ps -p $WORKER_PID > /dev/null; then
    echo "✅ Background worker is running!"
else
    echo "❌ ERROR: Background worker failed to start!"
    exit 1
fi

# Start Streamlit (foreground process)
echo "============================================"
echo "🌐 Starting Streamlit dashboard..."
streamlit run jkt48_stock_monitor.py --server.port=$PORT --server.address=0.0.0.0
