"""
model_testi.py — Groq modellerini aynı sorularla karşılaştırır (gecikme + düşünme).

Çalıştırma:
    python model_testi.py                      # varsayılan iki model
    python model_testi.py qwen/qwen3.8-27b     # tek model
"""

import sys
import time

import agent
from agent import SISTEM_MESAJI, client, model_ayarlari
from smart_home import TOOLS

MODELLER = sys.argv[1:] or ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
SORULAR = [
    "Merhaba İTO!",
    "Salonun ışığını mavi yap.",
    "Senin adın ne?",
    # Aşağıdakilerde "araç: evet" OLMALI; "hayır" ise model işi yapmış gibi konuşuyor
    "Salon klimasını aç.",
    "Yatak odası, televizyonu kapat.",
    "Mutfak klimasını 24 dereceye ayarla.",
]


def olc(model: str, soru: str):
    agent.MODEL = model  # model_ayarlari() bu değere göre ayar seçer
    t0 = time.perf_counter()
    ilk = None
    metin = ""
    arac = False
    dusunce = 0

    akis = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SISTEM_MESAJI},
            {"role": "user", "content": soru},
        ],
        tools=TOOLS,
        stream=True,
        **model_ayarlari(),
    )
    for parca in akis:
        if not parca.choices:
            continue
        d = parca.choices[0].delta
        r = getattr(d, "reasoning", None) or (d.model_extra or {}).get("reasoning")
        if r:
            dusunce += len(r)
        if d.content or d.tool_calls:
            if ilk is None:
                ilk = time.perf_counter() - t0
            metin += d.content or ""
            arac = arac or bool(d.tool_calls)

    toplam = time.perf_counter() - t0
    ilk_s = f"{ilk:.2f}" if ilk is not None else "-"
    print(f"  ilk: {ilk_s:>5} sn | toplam: {toplam:5.2f} sn | araç: {'evet' if arac else 'hayır':5}"
          f" | düşünce: {dusunce:4} krk | {soru}")
    if metin.strip():
        print(f"      -> {metin.strip()[:80]}")


if __name__ == "__main__":
    for model in MODELLER:
        print(f"\n=== {model} ===")
        for soru in SORULAR:
            try:
                olc(model, soru)
            except Exception as e:
                print(f"  [HATA] {soru}: {e}")
