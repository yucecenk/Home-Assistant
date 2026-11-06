"""
tts.py — İTO'nun sesi (Adım 4)

Piper (yerel, ücretsiz) ile metni sese çevirir.
Akış (pipeline):
    LLM parçaları -> cümlelere böl -> [sentez thread'i] -> [çalma thread'i] -> hoparlör
Böylece İTO ilk cümleyi söylerken sonraki cümleler hazırlanır.

durdur() ile konuşma anında kesilebilir (Adım 5'te sözü kesme için kullanacağız).

Test:  python tts.py
"""

import os
import queue
import re
import shutil
import threading
import time
from pathlib import Path

import sounddevice as sd
from piper import PiperVoice
from piper.phonemize_espeak import ESPEAK_DATA_DIR

MODEL_YOLU = "sesler/tr_TR-dfki-medium.onnx"
PARCA_SN = 0.1  # sesi 100 ms'lik dilimlerle çal -> durdur() hızlı tepki verir


def espeak_klasoru() -> Path:
    """
    Piper, Türkçe telaffuz için espeak-ng verisini kullanır. espeak-ng (C kütüphanesi)
    Windows'ta Türkçe karakterli yolları ("Smart Home İTO") açamıyor. Yol ASCII
    değilse veriyi bir kereliğine ASCII bir klasöre kopyalayıp oradan okuyoruz.
    """
    kaynak = Path(ESPEAK_DATA_DIR)
    if str(kaynak.resolve()).isascii():
        return kaynak
    hedef = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ito-espeak-ng-data"
    if not str(hedef).isascii():
        hedef = Path("C:/ito-espeak-ng-data")
    if not (hedef / "phontab").exists():
        print(f"espeak verisi kopyalanıyor -> {hedef}", flush=True)
        shutil.copytree(kaynak, hedef, dirs_exist_ok=True)
    return hedef


def tts_icin_temizle(metin: str) -> str:
    """TTS'in okuyamayacağı / garip okuyacağı işaretleri düzeltir."""
    metin = re.sub(r"[*#_`>\[\]]", "", metin)       # markdown kalıntıları
    metin = metin.replace("°C", " derece").replace("°", " derece")
    metin = metin.replace("%", "yüzde ")
    metin = metin.replace("…", ".")
    metin = re.sub(r"(?:\.\s*){2,}", ". ", metin)  # "... ... ..." -> tek nokta
    metin = re.sub(r"\s+", " ", metin).strip()
    if not re.search(r"\w", metin):  # içinde harf/rakam yoksa okunacak bir şey yok
        return ""
    return metin


def cumlelere_bol(parcalar):
    """LLM'den gelen kelime parçalarını biriktirir, cümle bittikçe dışarı verir."""
    tampon = ""
    for parca in parcalar:
        tampon += parca
        while True:
            # Nokta, soru, ünlem vb. + boşluk = cümle sonu
            m = re.search(r"[.!?…;:](\s)", tampon)
            # İlk cevabı hızlandırmak için uzun cümleyi virgülden de bölebiliriz
            if not m and len(tampon) > 60:
                m = re.search(r",(\s)", tampon)
            if not m:
                break
            cumle, tampon = tampon[: m.end(1)], tampon[m.end(1):]
            if cumle.strip():
                yield cumle.strip()
    if tampon.strip():
        yield tampon.strip()


class Konusmaci:
    def __init__(self, model_yolu: str = MODEL_YOLU):
        print("Ses modeli yükleniyor...", flush=True)
        self.ses = PiperVoice.load(model_yolu, espeak_data_dir=espeak_klasoru())
        self.ornekleme = self.ses.config.sample_rate

        self._cumleler: queue.Queue = queue.Queue()
        self._sesler: queue.Queue = queue.Queue()
        self._nesil = 0                  # durdur() her çağrıldığında artar
        self.ilk_ses_zamani = None       # gecikme ölçümü
        self.son_ses_zamani = 0.0        # en son ses çalınan an (yankı soğuması için)
        self.konusuyor = threading.Event()

        threading.Thread(target=self._sentezci, daemon=True).start()
        threading.Thread(target=self._calici, daemon=True).start()

    # ---------- dışarıdan kullanılan fonksiyonlar ----------
    def soyle(self, metin: str, nesil: int | None = None):
        """Tek bir cümleyi sıraya ekler."""
        metin = tts_icin_temizle(metin)
        if metin:
            self._cumleler.put((self._nesil if nesil is None else nesil, metin))

    def akistan_konus(self, parcalar):
        """LLM akışını alır, ekrana basar ve cümle tamamlandıkça seslendirir."""
        self.ilk_ses_zamani = None
        # Bu cevabın "nesli" baştan sabitlenir: araya durdur() girerse
        # LLM'den sonradan gelen cümleler de otomatik olarak çöpe gider.
        nesil = self._nesil

        def ekrana_bas(akis):
            for parca in akis:
                if nesil == self._nesil:
                    print(parca, end="", flush=True)
                yield parca

        for cumle in cumlelere_bol(ekrana_bas(parcalar)):
            self.soyle(cumle, nesil)

    def bekle(self):
        """Sıradaki her şey söylenene kadar bekler."""
        self._cumleler.join()
        self._sesler.join()

    def durdur(self):
        """Konuşmayı anında keser, sıradaki her şeyi çöpe atar."""
        self._nesil += 1

    # ---------- arka plan thread'leri ----------
    def _sentezle(self, metin: str):
        # piper-tts sürümleri arasında API farkı var; ikisini de destekle
        if hasattr(self.ses, "synthesize_stream_raw"):
            yield from self.ses.synthesize_stream_raw(metin)
        else:
            for parca in self.ses.synthesize(metin):
                yield parca.audio_int16_bytes

    def _sentezci(self):
        while True:
            nesil, metin = self._cumleler.get()
            try:
                if nesil == self._nesil:
                    for ses in self._sentezle(metin):
                        if nesil != self._nesil:
                            break
                        self._sesler.put((nesil, ses))
            except Exception as e:
                print(f"\n[TTS HATASI] {e}")
            finally:
                self._cumleler.task_done()

    def _calici(self):
        dilim = int(self.ornekleme * PARCA_SN) * 2  # bayt (int16)
        with sd.RawOutputStream(samplerate=self.ornekleme, channels=1, dtype="int16") as akis:
            while True:
                nesil, ses = self._sesler.get()
                try:
                    for i in range(0, len(ses), dilim):
                        if nesil != self._nesil:
                            break
                        self.konusuyor.set()
                        if self.ilk_ses_zamani is None:
                            self.ilk_ses_zamani = time.perf_counter()
                        akis.write(ses[i:i + dilim])
                        self.son_ses_zamani = time.perf_counter()
                finally:
                    self._sesler.task_done()
                    if self._sesler.empty() and self._cumleler.empty():
                        self.konusuyor.clear()


# ---------------------------------------------------------------
# Test
# ---------------------------------------------------------------
if __name__ == "__main__":
    k = Konusmaci()
    t0 = time.perf_counter()
    k.akistan_konus(iter([
        "Merhaba, ben İTO. ", "Salonun ışığını ", "kırmızı yaptım. ",
        "Termostat şu an 24°C, ", "perdeler %50 açık.",
    ]))
    k.bekle()
    print(f"\n(ilk ses: {k.ilk_ses_zamani - t0:.2f} sn)")
