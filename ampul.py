"""
ampul.py — Gerçek Tuya ampulü (Adım 8: canlı simülasyon)

Simülasyondaki bir odanın ışığı değişince aynı komut Wi-Fi üzerinden
gerçek ampule gönderilir (Tuya bulutu kullanılmaz, yerel ağ üzerinden).

Ayarlar şu sırayla aranır:
  1) .env:  TUYA_ID, TUYA_KEY, (isteğe bağlı) TUYA_IP, TUYA_VERSION, TUYA_ODA
  2) tinytuya wizard'ın oluşturduğu devices.json / snapshot.json

Test:  python ampul.py   (ampul sırayla kırmızı, yeşil, mavi, beyaz olur, sonra söner)
"""

import colorsys
import json
import os
import threading
import time
from pathlib import Path

import tinytuya
from dotenv import load_dotenv

load_dotenv(override=True)

GERCEK_ODA = os.getenv("TUYA_ODA", "salon")  # simülasyonda hangi odanın ışığı

# smart_home.RENKLER ile aynı isimler
RENK_RGB = {
    "kirmizi": (255, 0, 0),
    "turuncu": (255, 100, 0),
    "sari": (255, 200, 0),
    "yesil": (0, 255, 0),
    "mavi": (0, 0, 255),
    "mor": (160, 0, 255),
}


def en_yakin_renk(ton: float, doygunluk: float) -> str:
    """Ampulün renk tonunu (0-360) bizim renk adlarımızdan en yakınına çevirir."""
    if doygunluk < 0.25:
        return "beyaz"
    def fark(renk):
        r, g, b = RENK_RGB[renk]
        h = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
        d = abs(h - ton) % 360
        return min(d, 360 - d)
    return min(RENK_RGB, key=fark)


def ayarlari_bul():
    """Ampulün ID, IP, anahtar ve protokol sürümünü bulur."""
    cihaz_id, ip = os.getenv("TUYA_ID"), os.getenv("TUYA_IP")
    anahtar, surum = os.getenv("TUYA_KEY"), os.getenv("TUYA_VERSION")

    if not (cihaz_id and anahtar) and Path("devices.json").exists():
        cihazlar = json.loads(Path("devices.json").read_text(encoding="utf-8"))
        # "dj" = Tuya'da ışık kategorisi; yoksa ilk cihazı al
        c = next((d for d in cihazlar if d.get("category") == "dj"), cihazlar[0] if cihazlar else None)
        if c:
            cihaz_id, anahtar = c.get("id"), c.get("key")
            ip = ip or c.get("ip") or None
            surum = surum or c.get("version") or None

    if cihaz_id and not ip and Path("snapshot.json").exists():
        for d in json.loads(Path("snapshot.json").read_text(encoding="utf-8")).get("devices", []):
            if d.get("id") == cihaz_id:
                ip = d.get("ip") or None
                surum = surum or d.get("ver") or None

    return cihaz_id, ip, anahtar, surum


def _kontrol(sonuc):
    """tinytuya hata durumunda istisna fırlatmaz, {"Error": ...} döndürür: yakala."""
    if isinstance(sonuc, dict) and "Error" in sonuc:
        raise RuntimeError(f"{sonuc['Error']} (kod {sonuc.get('Err')})")
    return sonuc


class Ampul:
    def __init__(self):
        self.cihaz_id, ip, self.anahtar, surum = ayarlari_bul()
        if not (self.cihaz_id and self.anahtar):
            raise RuntimeError("Ampul ayarları bulunamadı (.env veya devices.json).")
        self.surum = float(surum) if surum else None
        self.bagli = False
        self.son_hata = ""

        self._cihaz_kur(ip or "Auto")  # IP bilinmiyorsa tinytuya ağı tarar
        self.baglantiyi_dene()

        # Komutlar arka planda gönderilir; İTO ampulü beklemeden konuşmaya devam eder.
        # Art arda komut gelirse sadece en sonuncusu önemlidir.
        self._son_durum = None
        self._kilit = threading.Lock()
        self._yeni = threading.Event()
        threading.Thread(target=self._calisan, daemon=True).start()

    def _cihaz_kur(self, ip: str):
        self.cihaz = tinytuya.BulbDevice(self.cihaz_id, ip, self.anahtar)
        if self.surum:
            self.cihaz.set_version(self.surum)
        self.cihaz.set_socketPersistent(True)  # her komutta yeniden bağlanma -> hızlı
        self.cihaz.set_socketTimeout(3)        # ulaşılamıyorsa uzun bekleme
        self.cihaz.set_socketRetryLimit(1)

    def _durum_degisti(self, bagli: bool, hata: str = ""):
        if bagli and not self.bagli:
            print(f"\n[ampul] bağlandı: {self.cihaz.address} (v{self.cihaz.version}) -> oda: {GERCEK_ODA}")
        elif not bagli and (self.bagli or hata != self.son_hata):
            print(f"\n[ampul] ULAŞILAMIYOR ({self.cihaz.address}): {hata}")
        self.bagli, self.son_hata = bagli, hata

    def baglantiyi_dene(self) -> bool:
        """Ampule gerçekten ulaşılabiliyor mu? Ulaşılamazsa IP değişmiş olabilir: ağda ara."""
        try:
            _kontrol(self.cihaz.status())
            self._durum_degisti(True)
            return True
        except Exception as e:
            hata = str(e)

        # Modem ampule yeni bir IP vermiş olabilir (DHCP): ağda kimliğiyle ara
        try:
            bulunan = tinytuya.find_device(self.cihaz_id) or {}
        except Exception:
            bulunan = {}
        yeni_ip = bulunan.get("ip")
        if yeni_ip and yeni_ip != self.cihaz.address:
            print(f"\n[ampul] IP adresi değişmiş: {self.cihaz.address} -> {yeni_ip} "
                  f"(.env'deki TUYA_IP satırını güncelle)")
            self._cihaz_kur(yeni_ip)
            try:
                _kontrol(self.cihaz.status())
                self._durum_degisti(True)
                return True
            except Exception as e:
                hata = str(e)

        self._durum_degisti(False, hata)
        return False

    def durum_oku(self) -> dict | None:
        """Ampulün gerçek durumunu simülasyon biçiminde okur: {acik, parlaklik, renk}."""
        try:
            dps = _kontrol(self.cihaz.status()).get("dps", {})
        except Exception:
            return None
        if "20" in dps:     # Type B ampul (bu ampul): parlaklık 10-1000
            acik, mod = dps.get("20"), dps.get("21")
            if mod == "colour" and isinstance(dps.get("24"), str) and len(dps["24"]) == 12:
                h, s, v = (int(dps["24"][i:i + 4], 16) for i in (0, 4, 8))
                renk = en_yakin_renk(h, s / 1000)
                parlaklik = round(v / 10)
            else:
                renk, parlaklik = "beyaz", round(dps.get("22", 1000) / 10)
        elif "1" in dps:    # eski Type A ampuller: sadece aç/kapa
            acik, renk, parlaklik = dps.get("1"), "beyaz", 100
        else:
            return None
        return {"acik": bool(acik), "parlaklik": max(1, min(100, parlaklik)), "renk": renk}

    def uygula(self, durum: dict):
        """Simülasyondaki ışık durumunu ({acik, parlaklik, renk}) ampule gönderir."""
        with self._kilit:
            self._son_durum = dict(durum)
        self._yeni.set()

    def _calisan(self):
        while True:
            self._yeni.wait()
            self._yeni.clear()
            with self._kilit:
                durum = self._son_durum
            try:
                self._gonder(durum)
                self._durum_degisti(True)
            except Exception as e:
                # Bir kez yeniden bağlanmayı dene (ampul elektrikten çekilip takılmış olabilir)
                if self.baglantiyi_dene():
                    try:
                        self._gonder(durum)
                    except Exception as e2:
                        self._durum_degisti(False, str(e2))
                else:
                    self._durum_degisti(False, str(e))

    def _gonder(self, durum: dict):
        if not durum["acik"] or durum["parlaklik"] <= 0:
            _kontrol(self.cihaz.turn_off())
            return
        _kontrol(self.cihaz.turn_on())
        parlaklik = max(1, min(100, durum["parlaklik"]))
        if durum["renk"] == "beyaz":
            _kontrol(self.cihaz.set_white_percentage(parlaklik))
        else:
            # Renkli modda parlaklığı RGB değerlerini ölçekleyerek veriyoruz
            r, g, b = RENK_RGB.get(durum["renk"], (255, 255, 255))
            k = parlaklik / 100
            _kontrol(self.cihaz.set_colour(int(r * k), int(g * k), int(b * k)))


# ---------------------------------------------------------------
# Test: python ampul.py
# ---------------------------------------------------------------
if __name__ == "__main__":
    a = Ampul()
    if not a.bagli:
        print("\nAmpule ulaşılamadı. Kontrol et:")
        print(" - Ampul elektriğe bağlı mı, duvar anahtarı açık mı?")
        print(" - Telefonda Tuya Smart uygulaması kapalı mı? (aynı anda tek bağlantı)")
        print(" - Bilgisayar ve ampul aynı Wi-Fi ağında mı?")
        raise SystemExit(1)
    print("Durum:", a.cihaz.status())
    for renk, parlaklik in [("kirmizi", 100), ("yesil", 100), ("mavi", 100),
                            ("beyaz", 100), ("beyaz", 20)]:
        print(f"-> {renk} %{parlaklik}")
        a._gonder({"acik": True, "parlaklik": parlaklik, "renk": renk})
        time.sleep(1.5)
    print("-> kapat")
    a._gonder({"acik": False, "parlaklik": 100, "renk": "beyaz"})
