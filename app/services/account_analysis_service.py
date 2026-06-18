"""Hesap analizi servisi — tek bir hesabin belirli bir konu/ozne hakkindaki
durusunu ve etkisini Gemini ile analiz eder.

PulseRepository.subject_top_account ile secilen 'orta seviye (dev olmayan)'
hesabin gonderileri buraya verilir; LLM durus profili + etki/manipulasyon
degerlendirmesi (JSON) dondurur. gemini_service.GeminiAIClient'in public
generate_content_async metodu kullanilir (OSINT direktifi + safety zaten
orada uygulanir).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.gemini_service import GeminiAIClient


def _strip_json_fence(text: str) -> str:
    if not text:
        return text
    t = text.strip()
    for fence in ("```json", "```JSON", "```"):
        t = t.replace(fence, "")
    return t.strip()


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Ham LLM metninden ilk JSON nesnesini guvenli sekilde cikarir."""
    if not raw:
        return None
    text = _strip_json_fence(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and data:
        data = data[0]
    return data if isinstance(data, dict) else None


async def analyze_account_profile(
    *,
    account_name: str,
    subject: str,
    by: str,
    posts: List[Dict[str, Any]],
    client: Optional[GeminiAIClient] = None,
) -> Optional[Dict[str, Any]]:
    """Secilen hesabin gonderilerinden durus + etki/manipulasyon profili (JSON).

    Donen alanlar (LLM uretir): profile, stance_label, key_arguments, influence,
    manipulation_risk, bot_risk, credibility_note. LLM hazir degilse / cikti bos
    ise None doner. JSON parse edilemezse ham metin 'profile' alaninda doner.
    """
    if client is None:
        client = GeminiAIClient()
    if not getattr(client, "model", None) or not getattr(client, "api_key", None):
        logger.warning("Gemini hazir degil; hesap profili cikarilamiyor.")
        return None

    lines: List[str] = []
    for i, p in enumerate((posts or [])[:15], 1):
        t = (p.get("text") or "").replace("\n", " ").strip()
        if not t:
            continue
        eng = p.get("engagement")
        suffix = f" (etkilesim ~{eng})" if eng else ""
        lines.append(f"{i}. {t[:400]}{suffix}")
    if not lines:
        return None
    posts_blob = "\n".join(lines)

    by_label = "\u00f6zne" if by == "target" else "konu"

    schema = (
        "{\n"
        '  "profile": "2-4 cumle: hesabin bu konudaki genel durusu, tonu ve soylemi",\n'
        '  "stance_label": "destek | kar\u015f\u0131 | n\u00f6tr | kar\u0131\u015f\u0131k",\n'
        '  "key_arguments": ["en fazla 4 kisa madde: one surdugu baslica argumanlar"],\n'
        '  "influence": "1-2 cumle: soyleminin etkisi ve yayilma bicimi",\n'
        '  "manipulation_risk": "d\u00fc\u015f\u00fck | orta | y\u00fcksek",\n'
        '  "bot_risk": "d\u00fc\u015f\u00fck | orta | y\u00fcksek",\n'
        '  "credibility_note": "1 cumle: guvenilirlik veya dikkat edilmesi gereken nokta"\n'
        "}"
    )

    prompt = (
        f'Sana bir X/Twitter hesabinin "{subject}" adli {by_label} hakkindaki '
        "gonderilerini veriyorum. Bu hesabin bu basliktaki DURUSUNU ve ETKISINI analiz et.\n\n"
        "Yalnizca tek bir JSON nesnesi dondur; markdown, aciklama veya kod citi ekleme.\n\n"
        "SEMA:\n" + schema + "\n\n"
        "KURALLAR:\n"
        "- Turkce yaz; notr istihbarat brifingi uslubu kullan; kendini yapay zeka olarak tanitma.\n"
        "- Kisiyi hedef gosterme veya hakaret yok; yalnizca soylem ve davranis oruntusunu degerlendir.\n"
        '- Veride net sinyal yoksa ilgili alana "belirsiz" yaz; alani bos birakma.\n\n'
        f"HESAP: {account_name}\n"
        f"{by_label.upper()}: {subject}\n\n"
        "GONDERILER:\n" + posts_blob + "\n"
    )

    try:
        raw = await client.generate_content_async(prompt)
        if not raw:
            return None
        data = _parse_json_object(raw)
        if data:
            return data
        # JSON degil (veya bloke mesaji): ham metni profile alaninda dondur
        return {"profile": str(raw)[:1200], "stance_label": "belirsiz"}
    except Exception as e:
        logger.exception(f"hesap profili analiz hatasi: {e}")
        return None
