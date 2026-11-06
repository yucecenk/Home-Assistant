"""
main.py — İTO'nun tamamı: dinle -> anla -> düşün -> konuş  (+ sözü kesilebilir + panel)

Akış:
  - Mikrofon hep açık (stt.Dinleyici).
  - Kullanıcı konuşunca (ya da panelden yazınca) cevap ARKA PLANDA üretilip seslendirilir.
  - Kullanıcı araya girerse İTO anında susar, kalan cevap iptal edilir.
  - Kontrol paneli: http://localhost:8765

Ses donanımı yoksa (ör. Docker) ya da ITO_SES=0 ise "yazı modunda" çalışır:
komutlar panelden yazılır, cevaplar panelde görünür.

Çalıştırma:  python main.py
"""

import os
import threading
import time
import webbrowser

import arayuz
import uyandirma
from agent import MODEL, Ajan
from smart_home import gercek_ampul

# "Hey İTO" uyandırma sözü (ITO_UYANDIRMA=0 ile kapatılabilir)
UYANDIRMA = os.getenv("ITO_UYANDIRMA", "1") != "0"
# İTO sustuktan sonra bu kadar saniye boyunca adını söylemeden devam edebilirsin
TAKIP_SN = float(os.getenv("ITO_TAKIP_SN", "8"))


def ses_var_mi() -> bool:
    """Mikrofon ve hoparlör kullanılabilir mi?"""
    if os.getenv("ITO_SES", "1") == "0":
        return False
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
        sd.query_devices(kind="output")
        return True
    except Exception as e:
        print(f"[ses] mikrofon/hoparlör bulunamadı ({e}); yazı modunda çalışıyorum.")
        return False


class SessizKonusmaci:
    """Ses donanımı yokken Konusmaci'nın yerine geçer: cevabı sadece ekrana yazar."""

    def __init__(self):
        self.konusuyor = threading.Event()   # hiç konuşmaz
        self.ilk_ses_zamani = None
        self.son_ses_zamani = 0.0

    def akistan_konus(self, parcalar):
        self.ilk_ses_zamani = None
        for parca in parcalar:
            if self.ilk_ses_zamani is None and parca.strip():
                self.ilk_ses_zamani = time.perf_counter()
            print(parca, end="", flush=True)

    def soyle(self, metin: str, nesil=None):
        print(metin, flush=True)

    def durdur(self):
        pass

    def bekle(self):
        pass


class Asistan:
    def __init__(self):
        self.ses = ses_var_mi()
        if self.ses:
            from stt import Dinleyici
            from tts import Konusmaci
            self.konusmaci = Konusmaci()
            self.dinleyici = Dinleyici()
        else:
            self.konusmaci = SessizKonusmaci()
            self.dinleyici = None
        self.ajan = Ajan()
        self.iptal = threading.Event()
        self.cevap_thread: threading.Thread | None = None
        self._kilit = threading.Lock()
        self.uyandi_zaman = 0.0      # en son "Hey İTO" denen an
        self.selam_sonu = 0.0        # açılış selamı takip süresi başlatmasın
        self.basladi_aktif = False   # kullanıcı konuşmaya başladığında İTO uyanık mıydı?

    # ---------- uyanıklık ----------
    def dusunuyor_mu(self) -> bool:
        return self.cevap_thread is not None and self.cevap_thread.is_alive()

    def aktif_mi(self) -> bool:
        """İTO şu an komut kabul ediyor mu? (adını söylemeye gerek var mı?)"""
        if not UYANDIRMA:
            return True
        if self.konusmaci.konusuyor.is_set() or self.dusunuyor_mu():
            return True
        son_ses = self.konusmaci.son_ses_zamani
        if son_ses <= self.selam_sonu:
            son_ses = 0.0
        son = max(self.uyandi_zaman, son_ses)
        return son > 0 and time.perf_counter() - son < TAKIP_SN

    # ---------- durum (panel için) ----------
    def durum(self) -> str:
        if self.konusmaci.konusuyor.is_set():
            return "konusuyor"
        if self.dusunuyor_mu():
            return "dusunuyor"
        if not self.ses:
            return "yazi"
        return "dinliyor" if self.aktif_mi() else "uyuyor"

    # ---------- kesme ----------
    def konusma_basladi(self):
        """Mikrofon bir konuşma yakaladığı an çağrılır."""
        self.basladi_aktif = self.aktif_mi()
        # İTO konuşuyorsa sözünü kes. Sadece düşünüyorsa henüz iptal etme:
        # gelen ses gürültü olabilir; geçerli bir komut çıkarsa o zaman iptal ederiz.
        if self.konusmaci.konusuyor.is_set():
            self.kes()

    def kes(self):
        """Devam eden cevabı iptal eder ve İTO'yu susturur."""
        self.iptal.set()
        self.ajan.iptal_et()      # yarım kalan cevap geçmişe yazılmasın
        self.konusmaci.durdur()

    # ---------- cevap ----------
    def cevapla_baslat(self, metin: str, t_baslangic: float):
        with self._kilit:
            self.iptal = threading.Event()
            self.cevap_thread = threading.Thread(
                target=self._cevapla, args=(metin, self.iptal, t_baslangic), daemon=True
            )
            self.cevap_thread.start()

    def _cevapla(self, metin: str, iptal: threading.Event, t_baslangic: float):
        parcalar = []

        def kesilebilir(akis):
            try:
                for parca in akis:
                    if iptal.is_set():
                        return
                    parcalar.append(parca)
                    yield parca
            finally:
                akis.close()  # iptal edilince LLM bağlantısını da kapat

        print("İTO: ", end="", flush=True)
        try:
            self.konusmaci.akistan_konus(kesilebilir(self.ajan.sor(metin)))
        except Exception as e:
            print(f"\n[LLM HATASI] {e}")
            arayuz.olay("bilgi", f"Cevap alınamadı: {e}")
            self.konusmaci.soyle("Bir sorun oluştu, tekrar dener misin?")

        # İlk sesin çıkmasını bekle, gecikmeyi ölç
        t0 = time.perf_counter()
        while (self.konusmaci.ilk_ses_zamani is None and not iptal.is_set()
               and time.perf_counter() - t0 < 10):
            time.sleep(0.02)

        cevap = "".join(parcalar).strip()
        if iptal.is_set():
            if cevap:
                arayuz.olay("ito", cevap, kesildi=True)
            print(flush=True)
            return
        gecikme = None
        if self.konusmaci.ilk_ses_zamani:
            gecikme = round(self.konusmaci.ilk_ses_zamani - t_baslangic, 2)
            print(f"\n  (gecikme: {gecikme:.2f} sn)\n", flush=True)
        if cevap:
            arayuz.olay("ito", cevap, gecikme=gecikme)

    # ---------- panelden yazılı komut ----------
    def metin_komutu(self, metin: str):
        print(f"\nSen (panel): {metin}")
        arayuz.olay("kullanici", metin, kaynak="yazi")
        self.kes()
        self.cevapla_baslat(metin, time.perf_counter())

    # ---------- ana döngü ----------
    def calistir(self):
        gercek_ampul()  # gerçek ampule baştan bağlan, ilk komutta beklemeyelim

        arayuz.durum_saglayici = self.durum
        arayuz.metin_komutu = self.metin_komutu
        adres = arayuz.baslat()
        print(f"Kontrol paneli: {adres}")
        if os.getenv("ITO_TARAYICI_AC", "1") != "0":
            webbrowser.open(adres)

        if not self.ses:
            print(f"İTO hazır ({MODEL}), yazı modunda. Komutları panelden yaz. "
                  "Durdurmak için Ctrl+C.\n")
            arayuz.olay("bilgi", "İTO yazı modunda: mikrofon yok, komutları aşağıya yaz.")
            while True:
                time.sleep(1)

        from stt import yaziya_cevir

        # Açılış selamı sırasında hoparlör yankısı ölçülür (kesme eşiği için)
        selam = ("Merhaba, ben İTO. Bir şey istediğinde Hey İTO de." if UYANDIRMA
                 else "Merhaba, ben İTO. Seni dinliyorum.")
        self.dinleyici.kalibre_et(self.konusmaci, selam)
        self.selam_sonu = self.konusmaci.son_ses_zamani
        if UYANDIRMA:
            print(f"İTO hazır ({MODEL}). Komut vermek için \"Hey İTO\" de. "
                  "Durdurmak için Ctrl+C.\n")
            arayuz.olay("bilgi", 'İTO hazır. "Hey İTO" diyerek başla ya da aşağıya yaz.')
        else:
            print(f"İTO hazır ({MODEL}). Konuşabilirsin. Durdurmak için Ctrl+C.\n")
            arayuz.olay("bilgi", "İTO hazır. Konuşabilir ya da aşağıya yazabilirsin.")

        while True:
            pcm = self.dinleyici.konusma_bekle(self.konusmaci, kesince=self.konusma_basladi)
            t_sustu = self.dinleyici.konusma_bitti

            # Önceki cevabı BEKLEMİYORUZ: iptal edildi, takılı kalsa bile
            # yeni cümle hemen yazıya çevrilir.
            try:
                metin = yaziya_cevir(pcm)
            except Exception as e:
                print(f"[STT HATASI] {e}")
                arayuz.olay("bilgi", "Ses yazıya çevrilemedi, tekrar söyler misin?")
                continue
            t_stt = time.perf_counter() - t_sustu
            if not metin:
                print("  (anlaşılamadı)\n")
                continue
            print(f"Sen: {metin}   (STT: {t_stt:.2f} sn | ses seviyen: {self.dinleyici.son_seviye:.0f})")

            komut, ad_gecti = uyandirma.ayikla(metin) if UYANDIRMA else (metin, False)
            if not (ad_gecti or self.basladi_aktif):
                print('  [uyuyorum: "Hey İTO" denmedi, yok sayıldı]\n')
                arayuz.olay("bilgi", f'Yok sayıldı, "Hey İTO" denmedi: {metin}')
                continue

            arayuz.olay("kullanici", metin, kaynak="ses")
            self.kes()  # geçerli yeni komut: varsa önceki cevabı iptal et

            if ad_gecti and not komut:
                # Sadece "Hey İTO" dendi: kısa bir cevap ver, sonraki cümleyi bekle
                self.uyandi_zaman = time.perf_counter()
                print("İTO: Efendim?\n")
                self.konusmaci.soyle("Efendim?")
                arayuz.olay("ito", "Efendim?")
                continue

            if ad_gecti:
                self.uyandi_zaman = time.perf_counter()
            self.cevapla_baslat(komut, t_sustu)


if __name__ == "__main__":
    try:
        Asistan().calistir()
    except KeyboardInterrupt:
        print("\nGörüşürüz!")
