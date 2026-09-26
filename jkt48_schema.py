"""
Adapter schema API JKT48 exclusive.

Per September 2026 endpoint detail lama (/exclusives/{code}) hanya mengembalikan
`quota_available` (boolean), tanpa angka. Endpoint /bonus mengembalikan angka
`available_quota` (sisa stok) — TAPI tidak ada `tickets_sold`.

Modul ini:
1. to_bonus_url()   : ubah URL detail apa pun menjadi varian /bonus.
2. normalize_bonus(): ubah respons /bonus (array sesi) ke bentuk internal seragam
   {session:[{label,date,start_time,end_time,session_detail:[{label,
   jkt48_member_name,available_quota}]}]} — TANPA tickets_sold (tidak tersedia).
"""
from urllib.parse import urlsplit, urlunsplit


def to_bonus_url(url: str) -> str:
    """https://.../exclusives/EX123?lang=id -> https://.../exclusives/EX123/bonus?lang=id"""
    if not url:
        return url
    p = urlsplit(url)
    path = p.path.rstrip('/')
    if not path.endswith('/bonus'):
        path = path + '/bonus'
    return urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))


def normalize_bonus(raw) -> dict:
    """
    Respons /bonus -> bentuk internal seragam.
    `raw` = list sesi (data dari API), atau None/[] -> {session: []}.
    Toleran juga terhadap bentuk lama ({session:[...]}) demi kompatibilitas.
    """
    if not raw:
        return {"session": []}

    sessions = raw if isinstance(raw, list) else raw.get("session", [])
    out = []
    for s in sessions:
        members = s.get("session_members") or s.get("session_detail") or []
        details = []
        for m in members:
            details.append({
                "label": m.get("label", ""),
                "jkt48_member_name": m.get("member_name") or m.get("jkt48_member_name", ""),
                "available_quota": m.get("available_quota", 0),
            })
        out.append({
            "label": s.get("label", ""),
            "date": s.get("date", ""),
            "start_time": s.get("start_time", ""),
            "end_time": s.get("end_time", ""),
            # kode sesi unik — WAJIB untuk cocokkan sesi (label bisa DUPLIKAT antar-tanggal!)
            "session_code": s.get("exclusive_session_code", ""),
            "session_detail": details,
        })
    return {"session": out}
