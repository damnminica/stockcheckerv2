"""
CapSolver — penyelesai Cloudflare challenge ("Just a moment") untuk IP proxy sticky.

Cloudflare mem-flag IP proxy -> 403. CapSolver menjalankan browser sungguhan
LEWAT proxy yang sama, menyelesaikan challenge, dan mengembalikan cookie
`cf_clearance` + `user-agent`. Cookie ini TERIKAT ke (IP proxy + user-agent +
fingerprint TLS), jadi worker HARUS request lewat:
  - proxy STICKY yang sama (bukan rotating!),
  - user-agent yang sama,
  - klien TLS-Chrome (curl_cffi impersonate=chrome).

Env var:
  CAPSOLVER_API_KEY   (wajib untuk aktif)
  JKT48_PROXY_LIST    (satu proxy STICKY — lihat proxy_pool)

cf_clearance dipakai ulang (~30 mnt / selama sticky IP bertahan). Di-refresh
saat kadaluarsa (TTL) atau saat request kena 403 (invalidate()).
Fail-safe: kalau CAPSOLVER_API_KEY kosong / solve gagal -> return None,
worker tetap jalan (hanya tetap kena 403 seperti tanpa solver).
"""
import os
import json
import time
import threading
from urllib.parse import urlsplit

import requests  # untuk memanggil API CapSolver (bukan ke jkt48)

CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "")

_CREATE_URL = "https://api.capsolver.com/createTask"
_RESULT_URL = "https://api.capsolver.com/getTaskResult"
# URL 'seed' di belakang Cloudflare untuk di-solve (endpoint list JKT48)
_SEED_URL = "https://jkt48.com/api/v1/exclusives?lang=id"

_TTL_SECONDS = 25 * 60          # umur cache clearance (naik dari 12m -> hemat solve;
                                # rotasi IP sticky di ~15m tetap memicu re-solve via 403)
_MIN_RESOLVE_SECONDS = 90       # cooldown: jangan solve ulang kalau baru solve < ini (anti solve-storm saat IP rotasi)
_POLL_TRIES = 40
_POLL_DELAY = 3

# Cache bersama antar-proses (worker & dashboard pakai proxy sticky yang SAMA):
# worker solve -> tulis file; dashboard baca file -> tak perlu solve sendiri.
CF_CLEARANCE_FILE = "/mnt/user-data/outputs/cf_clearance.json"

_lock = threading.Lock()
_cache = {}          # proxy_url -> {"cf_clearance", "user_agent", "ts"}
_last_solve_ts = 0.0 # waktu solve terakhir (untuk cooldown)


def _read_shared():
    try:
        if os.path.exists(CF_CLEARANCE_FILE):
            with open(CF_CLEARANCE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _write_shared(proxy_url, sol):
    try:
        os.makedirs(os.path.dirname(CF_CLEARANCE_FILE), exist_ok=True)
        with open(CF_CLEARANCE_FILE, "w") as f:
            json.dump({"proxy_url": proxy_url, **sol}, f)
    except Exception:
        pass


def enabled() -> bool:
    return bool(CAPSOLVER_API_KEY)


def _proxy_to_capsolver(proxy_url: str) -> str:
    """http://user:pass@host:port -> 'host:port:user:pass' (format CapSolver)."""
    p = urlsplit(proxy_url)
    return f"{p.hostname}:{p.port}:{p.username or ''}:{p.password or ''}"


def _extract_solution(solution: dict):
    """Ambil cf_clearance + user_agent dari berbagai bentuk response CapSolver."""
    ua = solution.get("userAgent") or solution.get("user_agent")
    cf = None
    cookies = solution.get("cookies")
    if isinstance(cookies, dict):
        cf = cookies.get("cf_clearance")
    elif isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and c.get("name") == "cf_clearance":
                cf = c.get("value")
                break
    cf = cf or solution.get("cf_clearance") or solution.get("token")
    return cf, ua


def _solve(proxy_url: str):
    """createTask -> poll getTaskResult. Return dict solusi atau None."""
    try:
        payload = {
            "clientKey": CAPSOLVER_API_KEY,
            "task": {
                "type": "AntiCloudflareTask",
                "websiteURL": _SEED_URL,
                "proxy": _proxy_to_capsolver(proxy_url),
            },
        }
        r = requests.post(_CREATE_URL, json=payload, timeout=30).json()
        if r.get("errorId"):
            print(f"  [CapSolver] createTask error: {r.get('errorCode')} {r.get('errorDescription')}")
            return None
        task_id = r.get("taskId")
        if not task_id:
            print(f"  [CapSolver] tidak ada taskId: {r}")
            return None

        for _ in range(_POLL_TRIES):
            time.sleep(_POLL_DELAY)
            res = requests.post(
                _RESULT_URL,
                json={"clientKey": CAPSOLVER_API_KEY, "taskId": task_id},
                timeout=30,
            ).json()
            status = res.get("status")
            if res.get("errorId"):
                print(f"  [CapSolver] error: {res.get('errorDescription')}")
                return None
            if status == "ready":
                cf, ua = _extract_solution(res.get("solution", {}))
                if cf:
                    print("  🔓 [CapSolver] cf_clearance diperoleh")
                    return {"cf_clearance": cf, "user_agent": ua, "ts": time.time()}
                print("  [CapSolver] solusi tanpa cf_clearance")
                return None
            # status == "processing" -> lanjut poll
        print("  [CapSolver] timeout menunggu hasil solve")
        return None
    except Exception as e:
        print(f"  [CapSolver] exception: {e}")
        return None


def get_clearance(proxy_url: str, force: bool = False):
    """
    Return (cf_clearance, user_agent) untuk proxy. Hemat solve:
      1) cache in-memory (fresh, non-force) -> pakai
      2) cache file bersama (proses lain baru solve) -> pakai, hindari solve
      3) cooldown: walau force, jangan solve kalau baru solve < _MIN_RESOLVE_SECONDS
      4) baru solve ke CapSolver
    (None, None) kalau CapSolver tidak aktif / tidak ada proxy / gagal.
    """
    global _last_solve_ts
    if not CAPSOLVER_API_KEY or not proxy_url:
        return None, None
    with _lock:
        now = time.time()

        # 1) in-memory fresh
        c = _cache.get(proxy_url)
        if c and not force and (now - c["ts"] < _TTL_SECONDS):
            return c["cf_clearance"], c["user_agent"]

        # 2) cache file bersama (mis. worker sudah solve; dashboard tinggal pakai)
        shared = _read_shared()
        if shared and shared.get("proxy_url") == proxy_url and shared.get("cf_clearance"):
            age = now - shared.get("ts", 0)
            # non-force: pakai kalau masih dalam TTL.
            # force: pakai hanya kalau SANGAT baru (< cooldown) -> berarti proses lain
            #        baru saja solve untuk rotasi IP yang sama; tak perlu solve lagi.
            if age < _TTL_SECONDS and (not force or age < _MIN_RESOLVE_SECONDS):
                _cache[proxy_url] = shared
                return shared["cf_clearance"], shared["user_agent"]

        # 3) cooldown anti solve-storm (mis. banyak event 403 di iterasi yang sama)
        if force and c and (now - _last_solve_ts) < _MIN_RESOLVE_SECONDS:
            return c["cf_clearance"], c["user_agent"]

        # 4) solve
        sol = _solve(proxy_url)
        if sol:
            _last_solve_ts = time.time()
            _cache[proxy_url] = sol
            _write_shared(proxy_url, sol)
            return sol["cf_clearance"], sol["user_agent"]
        return None, None


def invalidate(proxy_url: str):
    """Hapus cache clearance untuk proxy (paksa solve ulang di panggilan berikut)."""
    if not proxy_url:
        return
    with _lock:
        _cache.pop(proxy_url, None)
