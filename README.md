# İTO: Gerçek Zamanlı Sesli Ev Asistanı

Türkçe konuşan, düşük gecikmeli, sözü kesilebilen ve araç kullanan bir sesli ajan.
Akıllı ev simülasyonunu ve gerçek bir Wi-Fi ampulü sesle ya da web panelinden yönetir.

> Yapay zeka yazılımı eğitimi bitirme projesi.

## Özellikler

- **"Hey İTO" uyandırma sözü:** Adı söylenmeyen cümleleri yok sayar. Cevaptan sonra
  8 saniye boyunca adı söylemeden devam edilebilir.
- **Düşük gecikme:** Kullanıcı sustuktan sonra yaklaşık 1 saniyede sesli cevap.
  LLM cevabı parça parça üretilir ve cümle cümle seslendirilir.
- **Sözü kesilebilir (barge-in):** İTO konuşurken araya girilirse anında susar,
  kalan cevap iptal edilir. Hoparlör yankısı açılışta ölçülür.
- **Araç kullanımı (function calling):** Işık, klima, perde, kapı kilidi ve televizyon.
  Her odanın kendi ışığı ve kliması vardır.
- **Gerçek cihaz:** Bir odanın ışığı yerel ağ üzerinden gerçek bir Tuya ampule bağlanır.
- **Web paneli:** Kat planı, cihaz kontrolleri, canlı konuşma akışı, gecikme ölçümleri
  ve yazıyla komut.

## Mimari

```
Mikrofon -> VAD (webrtcvad) -> Whisper STT (Groq) -> "Hey İTO" kontrolü
         -> LLM + araçlar (Groq) -> akıllı ev simülasyonu ---> gerçek ampul (tinytuya)
         -> cümlelere bölme -> Piper TTS (yerel) -> hoparlör
                                   ^
            web paneli (http://localhost:8765) <-> aynı ev durumu
```

| Dosya | Görevi |
|---|---|
| `main.py` | Ana döngü: dinle, uyandırma, cevap, sözü kesme, panel bağlantısı |
| `stt.py` | Mikrofon, ses aktivitesi tespiti (VAD), yankı kalibrasyonu, Whisper |
| `agent.py` | LLM ajanı: araç döngüsü, doğrulama ve onay cümleleri |
| `tts.py` | Piper ile akışlı, kesilebilir seslendirme |
| `smart_home.py` | Ev simülasyonu ve LLM'e verilen araç tanımları |
| `ampul.py` | Gerçek Tuya ampul bağlantısı |
| `uyandirma.py` | "Hey İTO" algılama |
| `arayuz.py`, `arayuz.html` | Web kontrol paneli |
| `model_testi.py` | Modelleri gecikme ve araç kullanımı açısından karşılaştırır |

## Kurulum (Windows)

Python 3.12 gerekir.

```powershell
git clone <bu deponun adresi>
cd ito-asistan
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**Ses modeli:** `sesler` klasörü oluşturup şu iki dosyayı içine indir:

- [tr_TR-dfki-medium.onnx](https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx)
- [tr_TR-dfki-medium.onnx.json](https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx.json)

**Ayarlar:** `.env.example` dosyasını `.env` adıyla kopyala ve Groq API anahtarını yaz.

**Gerçek ampul (isteğe bağlı):** Ampulü Tuya Smart uygulamasına ekle, sonra
`python -m tinytuya wizard` ile yerel anahtarı al. Ampul yoksa İTO sadece simülasyonla çalışır.

## Kullanım

```powershell
python main.py
```

ya da `ITO.bat` dosyasına çift tıkla. Panel tarayıcıda kendiliğinden açılır.

Örnek komutlar:

- "Hey İTO, salonun ışığını mavi yap."
- "Hey İTO, yatak odasının klimasını 24 dereceye ayarla."
- "Hey İTO, evde hangi ışıklar açık?"
- "Hey İTO." → "Efendim?" → "Mutfak ışığını aç."

## Tasarım kararları

- **Eylem halüsinasyonuna karşı denetim:** Model araç çağırmadan "kapattım" derse cevap
  seslendirilmez, istek araç kullanımı zorunlu kılınarak tekrarlanır.
- **Onay cümlelerini kod üretir:** Başarılı cihaz komutlarında ikinci LLM çağrısı yapılmaz.
  Bu hem yaklaşık 1 saniye kazandırır hem de modelin iç düşüncelerinin cevaba sızmasını önler.
- **Durum farkındalığı:** Cihaz zaten istenen durumdaysa İTO bunu söyler
  ("Mutfağın ışığı zaten açık."). Açılışta gerçek ampulün durumu okunur.
- **Whisper ipucu:** Whisper'a "İTO" ve ev kelimelerini içeren bir ipucu verilir;
  böylece özel isim daha doğru yazıya çevrilir.

## Bilinen sınırlamalar

- Uyandırma sözü yazıya çevrilmiş metinden algılanır; uyku modunda da her cümle
  Whisper'a gönderilir.
- Laptop hoparlöründe yankı engelleme eşik tabanlıdır; kulaklıkla daha güvenilir çalışır.
- Docker'da (`docker compose up`) mikrofon ve hoparlöre erişilemediği için İTO yazı modunda çalışır.
