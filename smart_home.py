"""
smart_home.py — İTO için akıllı ev simülasyonu (Adım 1)

Gerçek cihaz yok; evin durumu bir sözlükte tutulur.
LLM ileride bu fonksiyonları "araç" (tool) olarak çağıracak.
"""

import json
from copy import deepcopy

# ---------------------------------------------------------------
# 1) Evin başlangıç durumu
# ---------------------------------------------------------------
ODALAR = ["salon", "yatak_odasi", "mutfak", "banyo"]

_ev = {
    "isiklar": {
        oda: {"acik": False, "parlaklik": 100, "renk": "beyaz"} for oda in ODALAR
    },
    # Her odada ayrı klima. mod: otomatik / isitma / sogutma
    "klimalar": {
        oda: {"acik": False, "mod": "otomatik", "hedef_sicaklik": 22} for oda in ODALAR
    },
    "perdeler": {"salon": 0, "yatak_odasi": 0},  # 0 = kapalı, 100 = tam açık
    "kapi_kilidi": True,  # True = kilitli
    "tv": {"acik": False, "ses": 20},
}

RENKLER = ["beyaz", "sari", "kirmizi", "mavi", "yesil", "mor", "turuncu"]

# ---------------------------------------------------------------
# Gerçek cihazlar (Adım 8): ayar yoksa ya da bağlanamazsa sadece simülasyon çalışır
# ---------------------------------------------------------------
_ampul = None  # None = henüz denenmedi, False = kullanılamıyor


def gercek_ampul():
    global _ampul
    if _ampul is None:
        try:
            from ampul import GERCEK_ODA, Ampul
            _ampul = Ampul()
            # Simülasyonu ampulün GERÇEK durumuyla başlat (telefondan/elle değiştirilmiş olabilir)
            gercek = _ampul.durum_oku() if _ampul.bagli else None
            if gercek:
                _ev["isiklar"][GERCEK_ODA].update(gercek)
                print(f"[ampul] gerçek durum okundu: {gercek}")
        except Exception as e:
            print(f"[ampul] gerçek ampul kullanılmıyor, sadece simülasyon: {e}")
            _ampul = False
    return _ampul or None


def gercek_oda() -> str | None:
    """Gerçek ampule bağlı oda (ampul ayarlı değilse None)."""
    if _ampul:
        from ampul import GERCEK_ODA
        return GERCEK_ODA
    return None


def ampul_bagli() -> bool:
    """Gerçek ampule şu an ulaşılabiliyor mu?"""
    return bool(_ampul and _ampul.bagli)


# ---------------------------------------------------------------
# 2) Cihaz fonksiyonları (her biri kısa bir sonuç metni döndürür)
# ---------------------------------------------------------------
def ev_durumu() -> dict:
    """Evin tüm durumunu döndürür."""
    return deepcopy(_ev)


def isik_ayarla(oda: str, acik: bool | None = None,
                parlaklik: int | None = None, renk: str | None = None) -> dict:
    hedefler = ODALAR if oda == "hepsi" else [oda]
    for o in hedefler:
        if o not in ODALAR:
            return {"hata": f"'{o}' diye bir oda yok. Odalar: {ODALAR}"}
    if renk is not None and renk not in RENKLER:
        return {"hata": f"Desteklenen renkler: {RENKLER}"}
    if parlaklik is not None and not 0 <= parlaklik <= 100:
        return {"hata": "Parlaklık 0-100 arası olmalı."}

    once = deepcopy({o: _ev["isiklar"][o] for o in hedefler})
    for o in hedefler:
        isik = _ev["isiklar"][o]
        if acik is not None:
            isik["acik"] = acik
            if acik and isik["parlaklik"] == 0:
                isik["parlaklik"] = 100   # "aç" denince karanlık kalmasın
        if parlaklik is not None:
            isik["parlaklik"] = parlaklik
            isik["acik"] = parlaklik > 0
        if renk is not None:
            isik["renk"] = renk
            isik["acik"] = True
    sonra = {o: _ev["isiklar"][o] for o in hedefler}

    # Canlı simülasyon: bu oda gerçek bir ampule bağlıysa komutu ona da gönder.
    # Değişiklik olmasa da gönderiyoruz: gerçek ampul elle değiştirilmiş olabilir.
    ampul = gercek_ampul()
    if ampul is not None:
        from ampul import GERCEK_ODA
        if GERCEK_ODA in hedefler:
            ampul.uygula(_ev["isiklar"][GERCEK_ODA])

    return {"tamam": True, "zaten": once == sonra, "isiklar": deepcopy(sonra)}


KLIMA_MODLARI = ["otomatik", "isitma", "sogutma"]


def klima_ayarla(oda: str, acik: bool | None = None,
                 mod: str | None = None, sicaklik: int | None = None) -> dict:
    hedefler = ODALAR if oda == "hepsi" else [oda]
    for o in hedefler:
        if o not in ODALAR:
            return {"hata": f"'{o}' diye bir oda yok. Odalar: {ODALAR}"}
    if mod is not None and mod not in KLIMA_MODLARI:
        return {"hata": f"Mod şunlardan biri olmalı: {KLIMA_MODLARI}"}
    if sicaklik is not None and not 16 <= sicaklik <= 30:
        return {"hata": "Sıcaklık 16-30 °C arası olmalı."}

    once = deepcopy({o: _ev["klimalar"][o] for o in hedefler})
    for o in hedefler:
        klima = _ev["klimalar"][o]
        if acik is not None:
            klima["acik"] = acik
        if mod is not None:
            klima["mod"] = mod
            klima["acik"] = True       # mod seçmek klimayı açar
        if sicaklik is not None:
            klima["hedef_sicaklik"] = sicaklik
            klima["acik"] = True       # sıcaklık ayarlamak klimayı açar
    sonra = {o: _ev["klimalar"][o] for o in hedefler}
    return {"tamam": True, "zaten": once == sonra, "klimalar": deepcopy(sonra)}


def perde_ayarla(oda: str, aciklik: int) -> dict:
    if oda not in _ev["perdeler"]:
        return {"hata": f"Perde olan odalar: {list(_ev['perdeler'])}"}
    if not 0 <= aciklik <= 100:
        return {"hata": "Açıklık 0-100 arası olmalı."}
    zaten = _ev["perdeler"][oda] == aciklik
    _ev["perdeler"][oda] = aciklik
    return {"tamam": True, "zaten": zaten, "oda": oda, "aciklik": aciklik}


def kapi_kilidi(kilitli: bool) -> dict:
    zaten = _ev["kapi_kilidi"] == kilitli
    _ev["kapi_kilidi"] = kilitli
    return {"tamam": True, "zaten": zaten, "kapi_kilitli": kilitli}


def tv_ayarla(acik: bool | None = None, ses: int | None = None) -> dict:
    if ses is not None and not 0 <= ses <= 100:
        return {"hata": "Ses 0-100 arası olmalı."}
    once = deepcopy(_ev["tv"])
    if acik is not None:
        _ev["tv"]["acik"] = acik
    if ses is not None:
        _ev["tv"]["ses"] = ses
    return {"tamam": True, "zaten": once == _ev["tv"], "tv": deepcopy(_ev["tv"])}


# ---------------------------------------------------------------
# 3) LLM'e tanıtılacak araç şemaları (OpenAI/Groq formatı)
#    Adım 2'de doğrudan kullanacağız.
# ---------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ev_durumu",
            "description": "Evdeki tüm cihazların güncel durumunu getirir.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "isik_ayarla",
            "description": "Bir odanın (veya 'hepsi') ışığını açar/kapatır, parlaklık veya renk ayarlar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "oda": {"type": "string", "enum": ODALAR + ["hepsi"]},
                    "acik": {"type": "boolean"},
                    "parlaklik": {"type": "integer", "minimum": 0, "maximum": 100},
                    "renk": {"type": "string", "enum": RENKLER},
                },
                "required": ["oda"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "klima_ayarla",
            "description": (
                "Bir odanın (veya 'hepsi') klimasını açar/kapatır, modunu ve hedef "
                "sıcaklığını ayarlar. Her odanın kliması ayrıdır. Kapatmak için acik=false."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "oda": {"type": "string", "enum": ODALAR + ["hepsi"]},
                    "acik": {"type": "boolean"},
                    "mod": {"type": "string", "enum": KLIMA_MODLARI},
                    "sicaklik": {"type": "integer", "minimum": 16, "maximum": 30},
                },
                "required": ["oda"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "perde_ayarla",
            "description": "Perdeyi yüzde olarak açar (0 = kapalı, 100 = tam açık).",
            "parameters": {
                "type": "object",
                "properties": {
                    "oda": {"type": "string", "enum": ["salon", "yatak_odasi"]},
                    "aciklik": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["oda", "aciklik"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kapi_kilidi",
            "description": "Dış kapıyı kilitler veya kilidini açar.",
            "parameters": {
                "type": "object",
                "properties": {"kilitli": {"type": "boolean"}},
                "required": ["kilitli"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_ayarla",
            "description": (
                "Evdeki tek televizyonu (salonda) açar/kapatır veya ses seviyesini ayarlar."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "acik": {"type": "boolean"},
                    "ses": {"type": "integer", "minimum": 0, "maximum": 100},
                },
            },
        },
    },
]

_FONKSIYONLAR = {
    "ev_durumu": ev_durumu,
    "isik_ayarla": isik_ayarla,
    "klima_ayarla": klima_ayarla,
    "perde_ayarla": perde_ayarla,
    "kapi_kilidi": kapi_kilidi,
    "tv_ayarla": tv_ayarla,
}


# Her araç çalıştıktan sonra çağrılır: fonksiyon(isim, arg, sonuc, kaynak).
# Kontrol paneli (arayuz.py) değişiklikleri buradan duyar.
ARAC_KANCALARI: list = []


def araci_calistir(isim: str, argumanlar: str | dict, kaynak: str = "ito") -> str:
    """Araç çağrısını (LLM'den ya da panelden) çalıştırır, sonucu JSON metni olarak döndürür."""
    if isim not in _FONKSIYONLAR:
        return json.dumps({"hata": f"Bilinmeyen araç: {isim}"}, ensure_ascii=False)
    try:
        if isinstance(argumanlar, str):
            argumanlar = json.loads(argumanlar or "{}")
        sonuc = _FONKSIYONLAR[isim](**argumanlar)
    except Exception as e:  # LLM yanlış parametre gönderirse çökmesin
        argumanlar = argumanlar if isinstance(argumanlar, dict) else {}
        sonuc = {"hata": str(e)}
    for kanca in ARAC_KANCALARI:
        try:
            kanca(isim, argumanlar, sonuc, kaynak)
        except Exception as e:
            print(f"\n[kanca hatası] {e}")
    return json.dumps(sonuc, ensure_ascii=False)


# ---------------------------------------------------------------
# 4) Hızlı test: python smart_home.py
# ---------------------------------------------------------------
if __name__ == "__main__":
    print(araci_calistir("isik_ayarla", {"oda": "salon", "renk": "mavi", "parlaklik": 60}))
    print(araci_calistir("klima_ayarla", '{"oda": "mutfak", "sicaklik": 24, "mod": "isitma"}'))
    print(araci_calistir("perde_ayarla", {"oda": "mutfak", "aciklik": 50}))  # hata beklenir
    print(araci_calistir("isik_ayarla", {"oda": "hepsi", "acik": False}))
    print(json.dumps(ev_durumu(), ensure_ascii=False, indent=2))
