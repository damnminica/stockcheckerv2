"""
Rotating HTTP/HTTPS proxy pool untuk request ke API JKT48.

Sumber: environment variable `JKT48_PROXY_LIST` (atau `JKT48_PROXY`).
Format: daftar proxy dipisah koma / titik-koma / baris baru, contoh:

    JKT48_PROXY_LIST=http://user:pass@host1:8000,http://user:pass@host2:8000

- Skema boleh dihilangkan (default http://).
- Kalau env var kosong / tidak di-set  ->  semua fungsi mengembalikan None
  (koneksi langsung tanpa proxy), jadi aplikasi tetap jalan tanpa proxy.

Rotasi round-robin per pemanggilan. Proxy yang gagal otomatis "terlewati"
karena retry pada pemanggil akan mengambil proxy berikutnya.

Telegram TIDAK memakai modul ini (tetap direct).
"""
import os
import threading
from urllib.parse import urlsplit, urlunsplit


def _load():
    raw = os.environ.get("JKT48_PROXY_LIST") or os.environ.get("JKT48_PROXY") or ""
    out = []
    for part in raw.replace("\n", ",").replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        if "://" not in p:
            p = "http://" + p
        out.append(p)
    return out


_PROXIES = _load()
_idx = 0
_lock = threading.Lock()


def has_proxies() -> bool:
    return bool(_PROXIES)


def count() -> int:
    return len(_PROXIES)


def next_proxy():
    """URL proxy berikutnya (round-robin), atau None kalau pool kosong."""
    global _idx
    if not _PROXIES:
        return None
    with _lock:
        p = _PROXIES[_idx % len(_PROXIES)]
        _idx += 1
    return p


def requests_proxies():
    """Dict {'http':.., 'https':..} untuk library `requests`, atau None."""
    url = next_proxy()
    if not url:
        return None
    return {"http": url, "https": url}


def aiohttp_kwargs():
    """kwargs untuk aiohttp `session.get()` (proxy + proxy_auth), atau {} kalau tanpa proxy."""
    url = next_proxy()
    if not url:
        return {}
    import aiohttp  # lazy: hanya proses async (worker/discovery) yang butuh
    clean, user, pwd = split_auth(url)
    kw = {"proxy": clean}
    if user is not None:
        kw["proxy_auth"] = aiohttp.BasicAuth(user, pwd or "")
    return kw


def split_auth(proxy_url):
    """
    Pisahkan kredensial dari URL untuk aiohttp:
        -> (clean_url_tanpa_userinfo, username, password)
    aiohttp butuh proxy_auth terpisah (BasicAuth) di sebagian versi.
    proxy_url None -> (None, None, None).
    """
    if not proxy_url:
        return None, None, None
    parts = urlsplit(proxy_url)
    hostport = parts.hostname or ""
    if parts.port:
        hostport += f":{parts.port}"
    clean = urlunsplit((parts.scheme, hostport, parts.path, parts.query, parts.fragment))
    return clean, parts.username, parts.password
