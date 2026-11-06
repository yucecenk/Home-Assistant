# İTO sesli ev asistanı — Docker imajı
#
# Derle:      docker compose build
# Çalıştır:   docker compose up
# Panel:      http://localhost:8765

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Ses kütüphaneleri: PortAudio (sounddevice için) ve ALSA'yı PulseAudio'ya
# yönlendiren eklenti (sesli mod için; yazı modunda kullanılmaz ama zararı yok)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libportaudio2 libasound2-plugins \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > /etc/asound.conf

WORKDIR /app

# Önce sadece bağımlılıklar: kod değişince bu katman yeniden kurulmaz (hızlı derleme)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Türkçe ses modeli ve kod
COPY sesler/ sesler/
COPY *.py arayuz.html ./

# Konteyner içinde: panel dışarıdan erişilebilsin, tarayıcı açmaya çalışma
ENV ITO_HOST=0.0.0.0 \
    ITO_TARAYICI_AC=0

EXPOSE 8765

# Root olmayan kullanıcıyla çalış
RUN useradd --create-home ito && chown -R ito /app
USER ito

CMD ["python", "main.py"]
