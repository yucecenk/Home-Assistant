"""
uyandirma.py — "Hey İTO" uyandırma sözü

Whisper her cümleyi zaten yazıya çeviriyor; ayrı bir ses modeli yerine
yazıda İTO adının geçip geçmediğine bakıyoruz (Türkçe isim için en pratik yol).

    ayikla("Hey İTO, salonun ışığını aç.")  -> ("salonun ışığını aç.", True)
    ayikla("Hey İTO!")                      -> ("", True)      # sadece uyandırma
    ayikla("Bugün hava güzel.")             -> ("Bugün hava güzel.", False)
"""

import re

# "İTO" ve Whisper'ın onu yanlış yazdığı görülen biçimler: ito, i to, itto, itu
_AD = r"[iİIı]\s?[tT]{1,2}\s?[oOuU]"
_ON_EK = r"(?:(?:hey|hei|hej|hay|ey|hi)[\s,!.]*)"
_UYANDIRMA = re.compile(
    rf"\b{_ON_EK}?{_AD}\b"
    r"|\bhey\s+yoga\b|\bneyi\s?tu\b|\beyito\b",   # loglarda görülen yanlış duyumlar
    re.IGNORECASE,
)


def ayikla(metin: str) -> tuple[str, bool]:
    """Metinden uyandırma sözünü çıkarır. (kalan_komut, ad_geçti_mi) döndürür."""
    m = _UYANDIRMA.search(metin)
    if not m:
        return metin.strip(), False
    kalan = (metin[:m.start()] + " " + metin[m.end():]).strip(" ,.!?;:-\t\n")
    kalan = re.sub(r"\s+", " ", kalan)
    if not re.search(r"\w", kalan):
        kalan = ""
    return kalan, True


if __name__ == "__main__":
    for c in ["Hey İTO! Salonun ışığını kapat.", "Hej İTO!", "Ey İTO, mutfak ışığını aç",
              "İTO, klimayı aç.", "Salonun ışığını aç İTO.", "Neyi tu?", "Hey yoga",
              "Bugün hava güzel.", "İtalya'ya gittim.", "Itır geldi.", "Hey ito"]:
        print(f"{c!r:38} -> {ayikla(c)}")
