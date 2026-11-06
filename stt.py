"""
stt.py — İTO'nun kulakları (Adım 3 + Adım 5: sözü kesme)

1) Mikrofon sürekli açık; 30 ms'lik çerçeveler halinde okunur.
2) VAD (Voice Activity Detection) konuşmanın başladığını ve bittiğini anlar.
3) İTO konuşurken: hoparlör yankısından belirgin şekilde yüksek ve en az
   0,3 sn süren insan sesi gelirse "sözümü kesti" deriz (barge-in).
4) Konuşma bitince ses Groq Whisper ile yazıya çevrilir.

Test (sözü kesme olmadan):  python stt.py
"""

import collections
import io
import queue
import time
import wave

import numpy as np
import sounddevice as sd
import webrtcvad

from agent import Ajan, client

ORNEKLEME = 16000                          # Whisper ve VAD için ideal: 16 kHz, mono
CERCEVE_MS = 30                            # webrtcvad 10/20/30 ms kabul eder
CERCEVE = ORNEKLEME * CERCEVE_MS // 1000   # 480 örnek = 960 bayt
STT_MODEL = "whisper-large-v3-turbo"       # Groq'ta hızlı ve Türkçe'de başarılı

# Whisper'a bağlam: özel isimleri ve ev kelimelerini doğru yazmasına yardım eder
STT_IPUCU = (
    "Hey İTO! İTO, salonun ışığını aç. Yatak odası, mutfak, banyo. "
    "Termostat, perde, televizyon, kapı kilidi, parlaklık, renk."
)

# Whisper sessizlik ya da gürültüde bazen bunları "duyar" (bilinen halüsinasyonlar)
HALUSINASYONLAR = {
    "altyazı m.k",
    "altyazılar",
    "izlediğiniz için teşekkürler",
    "izlediğiniz için teşekkür ederim",
    "abone olmayı unutmayın",
    "bir sonraki videoda görüşmek üzere",
}


def ses_seviyesi(cerceve: bytes) -> float:
    """Çerçevenin ses yüksekliği (RMS)."""
    a = np.frombuffer(cerceve, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a)))


class Dinleyici:
    def __init__(
        self,
        agresiflik: int = 2,          # 0-3: yükseldikçe gürültüye daha az duyarlı
        on_tampon_ms: int = 500,      # konuşma başlamadan önceki sesi de sakla ("Mut-fak" kesilmesin)
        baslama_orani: float = 0.7,   # tampondaki çerçevelerin %70'i ses ise konuşma başladı
        bitis_ms: int = 700,          # bu kadar sessizlik = cümle bitti
        min_konusma_ms: int = 400,    # bundan kısa sesler (öksürük, tık) yok sayılır
        kesme_ms: int = 300,          # İTO konuşurken bu kadar net konuşma = söz kesme
        yanki_carpani: float = 2.0,   # kesme eşiği = yankı seviyesi x bu sayı
        sogutma_ms: int = 600,        # İTO sustuktan sonra kalan yankıyı yok say
    ):
        self.vad = webrtcvad.Vad(agresiflik)
        self.on_tampon = on_tampon_ms // CERCEVE_MS
        self.baslama_esigi = int(self.on_tampon * baslama_orani)
        self.bitis = bitis_ms // CERCEVE_MS
        self.min_konusma = min_konusma_ms // CERCEVE_MS
        self.kesme_esigi = kesme_ms // CERCEVE_MS
        self.yanki_carpani = yanki_carpani
        self.yanki_esigi = 1500.0     # kalibre_et() ile güncellenir
        self.sogutma = sogutma_ms / 1000

        self.konusma_bitti = 0.0      # gecikme ölçümü için
        self.son_seviye = 0.0         # son konuşmanın ses seviyesi (ayar için)

        # Mikrofon sürekli açık: İTO konuşurken de dinleyebilmemiz gerekiyor
        self._kuyruk: queue.Queue[bytes] = queue.Queue()
        self._akis = sd.RawInputStream(
            samplerate=ORNEKLEME, blocksize=CERCEVE, dtype="int16",
            channels=1, callback=lambda indata, *_: self._kuyruk.put(bytes(indata)),
        )
        self._akis.start()

    def _cerceve(self) -> bytes:
        while True:
            c = self._kuyruk.get()
            if len(c) == CERCEVE * 2:
                return c

    def _kuyrugu_bosalt(self):
        while not self._kuyruk.empty():
            self._kuyruk.get_nowait()

    # ---------------------------------------------------------------
    def kalibre_et(self, konusmaci, metin: str):
        """İTO konuşurken mikrofonun duyduğu yankıyı ölçer, kesme eşiğini ayarlar."""
        self._kuyrugu_bosalt()
        konusmaci.soyle(metin)

        t0 = time.perf_counter()
        while not konusmaci.konusuyor.is_set() and time.perf_counter() - t0 < 10:
            self._cerceve()

        seviyeler = []
        while konusmaci.konusuyor.is_set():
            seviyeler.append(ses_seviyesi(self._cerceve()))

        if seviyeler:
            yanki = float(np.percentile(seviyeler, 90))
            self.yanki_esigi = max(yanki * self.yanki_carpani, 400.0)
            print(f"  [kalibrasyon] yankı: {yanki:.0f} -> sözü kesme eşiği: {self.yanki_esigi:.0f}")

    # ---------------------------------------------------------------
    def konusma_bekle(self, konusmaci=None, kesince=None) -> bytes:
        """
        Kullanıcı konuşup susana kadar bekler, sesini (PCM) döndürür.
        konusmaci verilirse: İTO konuşurken söz kesmeyi de algılar.
        kesince: konuşma başladığı an çağrılır (İTO'yu susturmak için).
        """
        while True:
            halka = collections.deque(maxlen=self.on_tampon)
            kesme_puani = 0
            kayit = None
            kesildi = False

            # 1) Konuşmanın başlamasını bekle
            while kayit is None:
                c = self._cerceve()
                ses_var = self.vad.is_speech(c, ORNEKLEME)

                if konusmaci is not None and konusmaci.konusuyor.is_set():
                    # İTO konuşuyor: sadece yankıdan belirgin yüksek ses sayılır
                    halka.append((c, ses_var))
                    if ses_var and ses_seviyesi(c) > self.yanki_esigi:
                        kesme_puani += 1
                    else:
                        kesme_puani = max(0, kesme_puani - 1)
                    if kesme_puani >= self.kesme_esigi:
                        kesildi = True
                        kayit = [x for x, _ in halka]

                elif (konusmaci is not None
                      and time.perf_counter() - konusmaci.son_ses_zamani < self.sogutma):
                    # İTO yeni sustu: hoparlörde kalan yankıyı duymayalım
                    halka.clear()
                    kesme_puani = 0

                else:
                    # Normal dinleme
                    halka.append((c, ses_var))
                    kesme_puani = 0
                    if sum(v for _, v in halka) >= self.baslama_esigi:
                        kayit = [x for x, _ in halka]

            if kesince is not None:
                kesince()
            print("\n  [sözüm kesildi, dinliyorum...]" if kesildi else "  [dinliyorum...]",
                  flush=True)

            # 2) Konuşmanın bitmesini bekle
            sessiz = 0
            while True:
                c = self._cerceve()
                kayit.append(c)
                sessiz = 0 if self.vad.is_speech(c, ORNEKLEME) else sessiz + 1
                if sessiz >= self.bitis:
                    self.konusma_bitti = time.perf_counter()
                    break

            if len(kayit) - self.bitis >= self.min_konusma:
                self.son_seviye = float(np.percentile([ses_seviyesi(x) for x in kayit], 75))
                return b"".join(kayit)
            print("  [çok kısa, yok sayıldı]", flush=True)

    def kapat(self):
        self._akis.stop()
        self._akis.close()


def yaziya_cevir(pcm: bytes) -> str:
    """Ham sesi WAV'a sarar, Groq Whisper'a gönderir, metni döndürür."""
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)        # int16
        w.setframerate(ORNEKLEME)
        w.writeframes(pcm)

    sonuc = client.audio.transcriptions.create(
        file=("konusma.wav", tampon.getvalue()),
        model=STT_MODEL,
        language="tr",           # dili sabitlemek hem hızı hem doğruluğu artırır
        prompt=STT_IPUCU,        # Whisper'a beklenen kelimeleri önceden söyle
        temperature=0,
        timeout=6.0,             # birkaç saniyelik ses için bundan uzunu takılma demek
    )
    metin = sonuc.text.strip()
    if metin.lower().strip(" .!?,") in HALUSINASYONLAR:
        return ""
    return metin


# ---------------------------------------------------------------
# Test: konuş -> yazıya çevir -> İTO yazıyla cevap versin
# ---------------------------------------------------------------
if __name__ == "__main__":
    mikrofon = sd.query_devices(kind="input")["name"]
    print(f"Mikrofon: {mikrofon}")
    print("Konuşabilirsin. Durdurmak için Ctrl+C.\n")

    dinleyici = Dinleyici()
    ajan = Ajan()

    try:
        while True:
            pcm = dinleyici.konusma_bekle()
            try:
                metin = yaziya_cevir(pcm)
            except Exception as e:
                print(f"[STT HATASI] {e}")
                continue
            if not metin:
                print("  (anlaşılamadı)\n")
                continue
            print(f"Sen: {metin}   (ses seviyen: {dinleyici.son_seviye:.0f})")
            print("İTO: ", end="", flush=True)
            for parca in ajan.sor(metin):
                print(parca, end="", flush=True)
            print("\n")
    except KeyboardInterrupt:
        dinleyici.kapat()
        print("\nGörüşürüz!")
