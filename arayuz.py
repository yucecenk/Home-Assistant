"""
arayuz.py — İTO kontrol paneli için küçük web sunucusu (Adım 9)

main.py çalışırken arka planda açılır: http://127.0.0.1:8765
Ek paket gerekmez (Python'un kendi http.server modülü).

API:
  GET  /               -> arayuz.html
  GET  /api/durum?son=N -> evin durumu + İTO'nun durumu + N'den sonraki olaylar
  POST /api/arac       -> {"isim": "isik_ayarla", "arg": {...}}  (panelden cihaz kontrolü)
  POST /api/soyle      -> {"metin": "..."}  (İTO'ya yazıyla komut)
"""

import collections
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import smart_home

PORT = 8765
HTML = Path(__file__).with_name("arayuz.html")

# main.py bunları kendi fonksiyonlarıyla değiştirir
durum_saglayici = lambda: "hazir"   # "dinliyor" | "dusunuyor" | "konusuyor"
metin_komutu = None                 # fonksiyon(metin) -> İTO'ya yazılı komut

_olaylar: collections.deque = collections.deque(maxlen=300)
_sayac = 0
_kilit = threading.Lock()


def olay(tur: str, metin: str, **ek):
    """Panelin konuşma akışına bir satır ekler (kullanici / ito / arac / bilgi)."""
    global _sayac
    with _kilit:
        _sayac += 1
        _olaylar.append({"id": _sayac, "tur": tur, "metin": metin,
                         "zaman": time.strftime("%H:%M"), **ek})


ODA_ADI = {"salon": "Salon", "yatak_odasi": "Yatak odası", "mutfak": "Mutfak", "banyo": "Banyo"}
RENK_ADI = {"beyaz": "beyaz", "sari": "sarı", "kirmizi": "kırmızı", "mavi": "mavi",
            "yesil": "yeşil", "mor": "mor", "turuncu": "turuncu"}
MOD_ADI = {"otomatik": "otomatik", "isitma": "ısıtma", "sogutma": "soğutma"}


def degisiklik_ozeti(sonuc: dict) -> str:
    """Araç sonucundan tarafsız bir durum satırı: 'Salon ışığı: mor, %80'."""
    satirlar = []
    for oda, d in sonuc.get("isiklar", {}).items():
        durum = f"{RENK_ADI.get(d['renk'], d['renk'])}, %{d['parlaklik']}" if d["acik"] else "kapalı"
        satirlar.append(f"{ODA_ADI[oda]} ışığı: {durum}")
    for oda, d in sonuc.get("klimalar", {}).items():
        durum = f"{MOD_ADI[d['mod']]}, {d['hedef_sicaklik']}°" if d["acik"] else "kapalı"
        satirlar.append(f"{ODA_ADI[oda]} kliması: {durum}")
    if "aciklik" in sonuc:
        a = sonuc["aciklik"]
        satirlar.append(f"{ODA_ADI[sonuc['oda']]} perdesi: " + ("kapalı" if a == 0 else f"%{a} açık"))
    if "kapi_kilitli" in sonuc:
        satirlar.append("Dış kapı: " + ("kilitli" if sonuc["kapi_kilitli"] else "kilidi açık"))
    if "tv" in sonuc:
        tv = sonuc["tv"]
        satirlar.append("Televizyon: " + (f"açık, ses {tv['ses']}" if tv["acik"] else "kapalı"))
    return "; ".join(satirlar)


def _arac_kancasi(isim: str, arg: dict, sonuc: dict, kaynak: str):
    """Her cihaz değişikliğini (İTO'dan ya da panelden) akışa yazar."""
    if isim == "ev_durumu":
        return
    if "hata" in sonuc:
        olay("arac", sonuc["hata"], kaynak=kaynak, hata=True)
        return
    metin = degisiklik_ozeti(sonuc) or isim
    if sonuc.get("zaten"):
        metin += " (zaten böyleydi)"
    olay("arac", metin, kaynak=kaynak)


smart_home.ARAC_KANCALARI.append(_arac_kancasi)


class _Isleyici(BaseHTTPRequestHandler):
    def log_message(self, *args):  # terminali kirletmesin
        pass

    def _gonder(self, kod: int, govde: bytes, tur: str):
        self.send_response(kod)
        self.send_header("Content-Type", tur)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def _json(self, veri, kod: int = 200):
        self._gonder(kod, json.dumps(veri, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._gonder(200, HTML.read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/api/durum":
            try:
                son = int(parse_qs(u.query).get("son", ["0"])[0])
            except ValueError:
                son = 0
            with _kilit:
                yeni = [o for o in _olaylar if o["id"] > son]
                sayac = _sayac
            self._json({
                "ev": smart_home.ev_durumu(),
                "durum": durum_saglayici(),
                "gercek_oda": smart_home.gercek_oda(),
                "ampul_bagli": smart_home.ampul_bagli(),
                "olaylar": yeni,
                "son": sayac,
            })
        else:
            self._json({"hata": "Bulunamadı"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            uzunluk = int(self.headers.get("Content-Length", 0))
            veri = json.loads(self.rfile.read(uzunluk) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"hata": "Geçersiz istek"}, 400)
            return

        if u.path == "/api/arac":
            sonuc = smart_home.araci_calistir(veri.get("isim", ""), veri.get("arg", {}),
                                              kaynak="panel")
            self._json(json.loads(sonuc))
        elif u.path == "/api/soyle":
            metin = str(veri.get("metin", "")).strip()
            if metin and metin_komutu is not None:
                metin_komutu(metin)
                self._json({"tamam": True})
            else:
                self._json({"hata": "Komut boş ya da İTO hazır değil"}, 400)
        else:
            self._json({"hata": "Bulunamadı"}, 404)


def baslat() -> str:
    """Sunucuyu arka planda başlatır, adresini döndürür."""
    # Normalde sadece bu bilgisayardan erişilir. Docker'da konteyner dışından
    # ulaşılabilsin diye ITO_HOST=0.0.0.0 verilir (compose bunu yine yerel ağa kapatır).
    host = os.getenv("ITO_HOST", "127.0.0.1")
    sunucu = ThreadingHTTPServer((host, PORT), _Isleyici)
    threading.Thread(target=sunucu.serve_forever, daemon=True).start()
    return f"http://localhost:{PORT}"
