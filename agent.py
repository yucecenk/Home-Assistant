"""
agent.py — İTO'nun beyni (Adım 2)

Groq üzerindeki LLM'e bağlanır, smart_home.py'deki araçları kullanır.
Cevabı parça parça (streaming) üretir: ilk kelime gelir gelmez ekrana
basılır. İleride bu parçaları doğrudan TTS'e vereceğiz -> düşük gecikme.

Çalıştırma:  python agent.py
"""

import json
import os
import re
import time

import openai
from dotenv import load_dotenv
from openai import OpenAI

from smart_home import TOOLS, araci_calistir

load_dotenv(override=True)  # .env dosyasındaki GROQ_API_KEY her zaman öncelikli

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
    # Sesli asistanda uzun beklemek hiç cevap vermemekten kötü:
    # bağlantı takılırsa en fazla 10 sn bekle, bir kez tekrar dene.
    timeout=10.0,
    max_retries=1,
)

MAKS_DENEME = 3  # model geçersiz araç çağırırsa aynı isteği en fazla bu kadar dene

SISTEM_MESAJI = """Sen İTO adında, Türkçe konuşan bir akıllı ev asistanısın.
Kurallar:
- Cevapların sesli okunacak. Kısa konuş: genelde 1-2 cümle, doğal günlük dil.
- Madde işareti, başlık, emoji, markdown veya kod kullanma.
- Evle ilgili her istekte araçları kullan. Cihaz durumunu tahmin etme;
  gerekirse önce ev_durumu aracıyla kontrol et.
- ÇOK ÖNEMLİ: Bir cihazı açtığını, kapattığını veya değiştirdiğini ASLA araç
  çağırmadan söyleme. Önce aracı çağır, araç "tamam" döndürünce geçmiş zamanla
  söyle ("açtım", "kapattım"). "Açıyorum" gibi gelecek zamanlı vaatler verme.
- Her odanın kendi kliması var. Kullanıcı oda söylemeden "klimayı aç" derse
  hangi oda olduğunu sor; "bütün klimalar" derse oda="hepsi" kullan.
- Bir araç hata döndürürse kullanıcıya nedenini kısaca söyle.
- İstek belirsizse (örneğin hangi oda olduğu belli değilse) kısa bir soru sor.
- Odalar: salon, yatak odası, mutfak, banyo. Kullanıcının söylediği oda adı bunlardan
  biriyle açıkça eşleşmiyorsa (örneğin "bak ışığı") TAHMİN ETME; "Hangi odanın
  ışığını açayım?" diye sor.
- Ev dışındaki sorulara da kısa ve yardımsever cevap verebilirsin.
- Kullanıcının söyledikleri konuşmadan yazıya çevrilir; ufak yazım hataları olabilir.
  "ito", "yoga", "neyi tu" gibi kelimeler büyük ihtimalle senin adındır (İTO)."""

MAKS_ARAC_TURU = 5  # modelin sonsuz araç döngüsüne girmesini engeller

DUZELTME = (
    "Önceki denemende hiçbir aracı çağırmadan işlem yaptığını söyledin; bu yanlış. "
    "Kullanıcının isteğini yerine getirmek için şimdi uygun aracı çağır."
)

# Araç çağrılmadan söylenirse yalan olan işlem ifadeleri
_EYLEM = re.compile(
    r"\b(açtım|kapattım|kapadım|yaptım|ayarladım|değiştirdim|düşürdüm|yükselttim|"
    r"artırdım|azalttım|kilitledim|söndürdüm|yaktım|çevirdim|indirdim|"
    r"açıldı|kapatıldı|kapandı|ayarlandı|değiştirildi|kilitlendi|"
    r"açıyorum|kapatıyorum|yapıyorum|ayarlıyorum|değiştiriyorum)\b",
    re.IGNORECASE,
)


def eylem_iddiasi(metin: str) -> bool:
    """Metin bir cihaz üzerinde işlem yapıldığını iddia ediyor mu?"""
    return bool(_EYLEM.search(metin))


def model_ayarlari() -> dict:
    """Her model ailesi için en uygun ayarlar (.env'deki GROQ_MODEL'e göre)."""
    if "qwen" in MODEL:
        # Sesli asistanda "düşünme" gecikme demek: kapat. Araç kullanırken
        # Groq düşünce çıktısının gizli/ayrık olmasını istiyor.
        return {
            "temperature": 0.4,  # araç adlarını uydurmasın diye düşük
            "extra_body": {"reasoning_effort": "none", "reasoning_format": "hidden"},
        }
    if "gpt-oss" in MODEL:
        # gpt-oss temperature=1.0 için eğitildi; düşük değerler bozuk cevap üretebiliyor
        return {"temperature": 1.0, "extra_body": {"reasoning_effort": "low"}}
    return {"temperature": 0.3}


def son_paragraf(metin: str) -> str:
    """
    gpt-oss bazen düzgün cevaptan önce bozuk bir parça üretip boş satırlarla
    ayırıyor ("Salon un un un ... ?\\n\\n\\nSalon kliması açıldı."). Kısa sesli
    cevaplarda gerçek cevap hep son paragraf olduğu için sadece onu tutuyoruz.
    """
    parcalar = [p.strip() for p in re.split(r"\n\s*\n", metin) if re.search(r"\w", p)]
    return parcalar[-1] if parcalar else ""


_INGILIZCE = {
    "the", "a", "an", "we", "i", "need", "output", "wait", "should", "user", "let",
    "lets", "respond", "response", "answer", "simple", "weird", "so", "now", "call",
    "tool", "function", "must", "maybe", "ok", "okay", "is", "are", "to", "it", "this",
    "that", "and", "of", "in", "for", "with", "just", "say", "will", "be", "done",
    "assistant", "reply", "turkish", "light", "room", "set", "already", "called",
}


def dusunce_sizintisini_temizle(metin: str) -> str:
    """
    gpt-oss'un İngilizce iç düşünceleri bazen cevaba sızıyor
    ("Wait output weird, need simple.Salonun ışığını kırmızı yaptım.").
    İTO hep Türkçe konuştuğu için İngilizce cümleleri atıyoruz.
    """
    # Cümle sonu: . ! ? (ve varsa kapanan tırnak) ardından boşluk ya da büyük harf.
    # 22.5 gibi sayıları bölmez.
    cumleler = re.split(
        r"(?<=[.!?…])(?=\s|[A-ZÇĞİÖŞÜ\"“])|(?<=[.!?…][\"”])(?=\s|[A-ZÇĞİÖŞÜ])", metin
    )
    kalan = []
    for c in cumleler:
        kelimeler = re.findall(r"[a-zçğıöşü']+", c.lower())
        if not kelimeler:
            continue
        turkce_harf = re.search(r"[çğıöşü]", c.lower())
        ing = sum(k in _INGILIZCE for k in kelimeler)
        if ing >= 3 or (not turkce_harf and (ing >= 2 or ing / len(kelimeler) >= 0.5)):
            continue  # İngilizce düşünce sızıntısı
        if re.search(r"\*{2,}|…$", c.strip()):
            continue  # "Salonu ****." / "Salonu kap…" gibi yarım kalmış parçalar
        kalan.append(c.strip())
    return " ".join(kalan)


# ---------------------------------------------------------------
# Hazır onay cümleleri: başarılı cihaz komutlarında ikinci LLM çağrısına gerek yok
# (hem ~1 sn daha hızlı, hem de modelin düşünce sızıntısı riski sıfır)
# ---------------------------------------------------------------
ODA_IYELIK = {  # "salonun ışığı", "mutfağın kliması"
    "salon": "Salonun", "yatak_odasi": "Yatak odasının",
    "mutfak": "Mutfağın", "banyo": "Banyonun",
}
RENK_ADI = {
    "beyaz": "beyaz", "sari": "sarı", "kirmizi": "kırmızı", "mavi": "mavi",
    "yesil": "yeşil", "mor": "mor", "turuncu": "turuncu",
}
MOD_ADI = {"otomatik": "otomatik", "isitma": "ısıtma", "sogutma": "soğutma"}


def zaten_cumlesi(isim: str, a: dict, sonuc: dict) -> str | None:
    """Cihaz zaten istenen durumdaysa: 'Mutfağın ışığı zaten açık.'"""
    hepsi = a.get("oda") == "hepsi"
    oda = ODA_IYELIK.get(a.get("oda"), "")

    if isim == "isik_ayarla":
        ozne = "Bütün ışıklar" if hepsi else f"{oda} ışığı"
        d = next(iter(sonuc["isiklar"].values()))
        if not d["acik"]:
            return f"{ozne} zaten kapalı."
        if a.get("renk") and a.get("parlaklik") is not None:
            return f"{ozne} zaten {RENK_ADI[d['renk']]} ve yüzde {d['parlaklik']} parlaklıkta."
        if a.get("renk"):
            return f"{ozne} zaten {RENK_ADI[d['renk']]}."
        if a.get("parlaklik") is not None:
            return f"{ozne} zaten yüzde {d['parlaklik']} parlaklıkta."
        return f"{ozne} zaten açık."

    if isim == "klima_ayarla":
        ozne = "Bütün klimalar" if hepsi else f"{oda} kliması"
        d = next(iter(sonuc["klimalar"].values()))
        if not d["acik"]:
            return f"{ozne} zaten kapalı."
        if a.get("sicaklik") is not None and a.get("mod"):
            return f"{ozne} zaten {MOD_ADI[d['mod']]} modunda ve {d['hedef_sicaklik']} derecede."
        if a.get("sicaklik") is not None:
            return f"{ozne} zaten {d['hedef_sicaklik']} dereceye ayarlı."
        if a.get("mod"):
            return f"{ozne} zaten {MOD_ADI[d['mod']]} modunda."
        return f"{ozne} zaten açık."

    if isim == "perde_ayarla":
        acik = sonuc["aciklik"]
        durum = "kapalı" if acik == 0 else "tamamen açık" if acik == 100 else f"yüzde {acik} açık"
        return f"{oda} perdesi zaten {durum}."

    if isim == "kapi_kilidi":
        return "Kapı zaten kilitli." if sonuc["kapi_kilitli"] else "Kapının kilidi zaten açık."

    if isim == "tv_ayarla":
        tv = sonuc["tv"]
        if not tv["acik"]:
            return "Televizyon zaten kapalı."
        if a.get("ses") is not None:
            return f"Televizyonun sesi zaten {tv['ses']}."
        return "Televizyon zaten açık."

    return None


def onay_cumlesi(isim: str, a: dict, sonuc: dict) -> str | None:
    """Başarılı bir cihaz komutu için Türkçe onay cümlesi. Üretemezse None (LLM konuşur)."""
    if "hata" in sonuc or not sonuc.get("tamam"):
        return None  # hatayı LLM açıklasın
    if sonuc.get("zaten"):
        return zaten_cumlesi(isim, a, sonuc)

    if isim == "isik_ayarla":
        hedef = "Bütün ışıkları" if a.get("oda") == "hepsi" else f"{ODA_IYELIK.get(a.get('oda'), '')} ışığını"
        if a.get("acik") is False or a.get("parlaklik") == 0:
            return f"{hedef} kapattım."
        parcalar = []
        if a.get("renk"):
            parcalar.append(RENK_ADI.get(a["renk"], a["renk"]))
        if a.get("parlaklik") is not None:
            parcalar.append(f"yüzde {a['parlaklik']} parlaklığa")
        if parcalar:
            return f"{hedef} {' ve '.join(parcalar)} {'getirdim' if len(parcalar) == 2 or 'parlaklığa' in parcalar[0] else 'yaptım'}."
        return f"{hedef} açtım."

    if isim == "klima_ayarla":
        hedef = "Bütün klimaları" if a.get("oda") == "hepsi" else f"{ODA_IYELIK.get(a.get('oda'), '')} klimasını"
        if a.get("acik") is False:
            return f"{hedef} kapattım."
        if a.get("sicaklik") is not None and a.get("mod"):
            return f"{hedef} {MOD_ADI[a['mod']]} modunda {a['sicaklik']} dereceye ayarladım."
        if a.get("sicaklik") is not None:
            return f"{hedef} {a['sicaklik']} dereceye ayarladım."
        if a.get("mod"):
            return f"{hedef} {MOD_ADI[a['mod']]} modunda açtım."
        return f"{hedef} açtım."

    if isim == "perde_ayarla":
        hedef = f"{ODA_IYELIK.get(a.get('oda'), '')} perdesini"
        acik = a.get("aciklik", 0)
        if acik == 0:
            return f"{hedef} kapattım."
        if acik == 100:
            return f"{hedef} tamamen açtım."
        return f"{hedef} yüzde {acik} açtım."

    if isim == "kapi_kilidi":
        return "Kapıyı kilitledim." if a.get("kilitli") else "Kapının kilidini açtım."

    if isim == "tv_ayarla":
        if a.get("acik") is False:
            return "Televizyonu kapattım."
        if a.get("ses") is not None:
            return f"Televizyonun sesini {a['ses']} yaptım."
        return "Televizyonu açtım."

    return None  # ev_durumu gibi bilgi araçlarında cevabı LLM kurar


class Ajan:
    def __init__(self):
        self.mesajlar = [{"role": "system", "content": SISTEM_MESAJI}]
        self._tur = 0  # her yeni soru ya da iptal bunu artırır

    def iptal_et(self):
        """Devam eden cevabı geçersiz kılar (kullanıcı sözü kesince)."""
        self._tur += 1

    def _istek(self, mesajlar, zorunlu: bool, ilk_tur: bool):
        """
        Tek bir LLM isteği. Seslendirilecek metni yield eder,
        sonunda (tam_metin, araclar, yayinlanan_uzunluk) döndürür.
        """
        for deneme in range(MAKS_DENEME):
            metin = ""
            araclar = {}  # index -> {"id", "isim", "arg"}
            yayinlanan = 0
            try:
                akis = client.chat.completions.create(
                    model=MODEL,
                    messages=mesajlar,
                    tools=TOOLS,
                    tool_choice="required" if zorunlu else "auto",
                    stream=True,
                    **model_ayarlari(),
                )
                for parca in akis:
                    if not parca.choices:
                        continue
                    delta = parca.choices[0].delta

                    if delta.content:
                        metin += delta.content
                        # Kısa cevaplar tamamen tamponda bekler (denetim + temizlik için).
                        # Uzun bilgi cevaplarında, işlem iddiası yoksa beklemeden akıt.
                        if ilk_tur and not araclar and (
                            yayinlanan or (len(metin) > 120 and not eylem_iddiasi(metin))
                        ):
                            yield metin[yayinlanan:]
                            yayinlanan = len(metin)

                    for tc in delta.tool_calls or []:
                        c = araclar.setdefault(tc.index, {"id": "", "isim": "", "arg": ""})
                        if tc.id:
                            c["id"] = tc.id
                        if tc.function and tc.function.name:
                            c["isim"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            c["arg"] += tc.function.arguments
                return metin, araclar, yayinlanan
            except openai.APIError as e:
                # Model olmayan bir araç uydurursa Groq isteği reddeder.
                # Kullanıcıya henüz bir şey söylemediysek aynı isteği tekrar dene.
                if ("tool call validation" in str(e) and not yayinlanan
                        and deneme < MAKS_DENEME - 1):
                    print("\n  [model geçersiz araç çağırdı, tekrar deniyorum]", flush=True)
                    continue
                raise
        return "", {}, 0

    def sor(self, kullanici_metni: str):
        """Kullanıcının cümlesini alır, cevabı parça parça (yield) döndürür."""
        self._tur += 1
        tur = self._tur
        # Geçmişin kopyası üzerinde çalış; tur iptal edilirse yarım tur geçmişe yazılmaz
        m = self.mesajlar + [{"role": "user", "content": kullanici_metni}]

        arac_cagrildi = False  # bu turda en az bir araç çalıştı mı?
        zorla = False          # model araç çağırmadan "yaptım" dedi -> aracı zorunlu kıl

        for _ in range(MAKS_ARAC_TURU):
            if tur != self._tur:
                return
            zorunlu = zorla and not arac_cagrildi
            istek = m + ([{"role": "system", "content": DUZELTME}] if zorunlu else [])

            metin, araclar, yayinlanan = yield from self._istek(
                istek, zorunlu, ilk_tur=not arac_cagrildi
            )
            if tur != self._tur:
                return  # bu arada iptal edildi: sonucu kullanma

            if not araclar:
                # Denetim: araç çağırmadan işlem yaptığını iddia etti mi?
                if (not arac_cagrildi and not yayinlanan and not zorla
                        and eylem_iddiasi(metin)):
                    print("\n  [araç çağırmadan işlem yaptığını söyledi; aracı zorunlu kılıyorum]",
                          flush=True)
                    zorla = True
                    continue  # bu cevabı söylemeden tekrar iste
                if not yayinlanan:
                    metin = dusunce_sizintisini_temizle(son_paragraf(metin))
                    if not metin and arac_cagrildi:
                        metin = "Tamam, hallettim."
                kalan = metin[yayinlanan:]
                if kalan.strip():
                    yield kalan
                if tur != self._tur:
                    return  # seslendirilirken sözü kesildi: geçmişe yazma
                m.append({"role": "assistant", "content": metin})
                self.mesajlar = m  # tur tamamlandı, geçmişe işle
                return

            # Araç çağrıları: geçmişe ekle, çalıştır, sonuçları modele geri ver.
            # (Araçtan önce yazılan ara metni seslendirmiyoruz.)
            arac_cagrildi = True
            m.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["isim"], "arguments": c["arg"] or "{}"},
                    }
                    for c in araclar.values()
                ],
            })
            onaylar = []
            for c in araclar.values():
                sonuc = araci_calistir(c["isim"], c["arg"])
                print(f"\n  [araç] {c['isim']}({c['arg']}) -> {sonuc}", flush=True)
                m.append({"role": "tool", "tool_call_id": c["id"], "content": sonuc})
                try:
                    onaylar.append(onay_cumlesi(c["isim"], json.loads(c["arg"] or "{}"),
                                                json.loads(sonuc)))
                except (ValueError, KeyError, TypeError):
                    onaylar.append(None)

            # Hepsi başarılı cihaz komutuysa: cevabı LLM'e yazdırmadan kendimiz söyleyelim
            if onaylar and all(onaylar):
                metin = " ".join(onaylar)
                yield metin
                if tur != self._tur:
                    return
                m.append({"role": "assistant", "content": metin})
                self.mesajlar = m
                return
            # Aksi halde (hata, bilgi sorgusu) döngü başa döner ve LLM cevabı yazar

        yield "Üzgünüm, bu isteği tamamlayamadım."


# ---------------------------------------------------------------
# Yazılı sohbet testi
# ---------------------------------------------------------------
if __name__ == "__main__":
    ajan = Ajan()
    print(f"İTO hazır ({MODEL}). Çıkmak için 'q' yaz.")

    while True:
        girdi = input("\nSen: ").strip()
        if girdi.lower() in ("q", "çık", "exit"):
            break
        if not girdi:
            continue

        t0 = time.perf_counter()
        ilk_kelime = None
        print("İTO: ", end="", flush=True)
        try:
            for parca in ajan.sor(girdi):
                if ilk_kelime is None:
                    ilk_kelime = time.perf_counter() - t0
                print(parca, end="", flush=True)
        except Exception as e:
            print(f"\n[HATA] {e}")
            continue

        toplam = time.perf_counter() - t0
        ilk = f"{ilk_kelime:.2f} sn" if ilk_kelime is not None else "-"
        print(f"\n  (ilk kelime: {ilk} | toplam: {toplam:.2f} sn)")
