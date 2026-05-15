import os
import json
from loguru import logger
from app.services.gemini_model import (
    GEMINI_BLOCKED_PLAIN_MESSAGE,
    create_gemini_model,
    extract_gemini_response_text,
    gemini_safety_settings_block_none,
)
from app.services.karargah_llm_directive import with_karargah_osint_directive

class LabelingService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.model, self.model_name = create_gemini_model(self.api_key)
            self.is_llm_active = self.model is not None
            logger.info(f"🚀 Gemini API Aktif: LabelingService modeli={self.model_name}")
        else:
            self.is_llm_active = False
            self.model = None
            logger.error("GEMINI_API_KEY bulunamadı: AI etiketleme sahte fallback üretmez.")


    def analyze_content(self, text: str, platform: str) -> dict:
        """İçeriği yalnızca gerçek LLM ile analiz eder."""
        if not text or len(text) < 10:
            raise ValueError("Analiz için yeterli gerçek içerik yok.")

        if not self.is_llm_active:
            raise RuntimeError("Gemini modeli aktif değil; sahte/kural tabanlı etiket üretilmez.")

        try:
            return self._llm_analysis(text, platform)
        except Exception as e:
            logger.error(f"❌ LLM Analiz Hatası: {e}")
            raise

    def _llm_analysis(self, text: str, platform: str) -> dict:
        is_rx = "[ANA PAYLAŞIM/HABER:" in text and "[GELEN TEPKİ/YORUM:" in text
        tepki_bloku = """
Veride "[ANA PAYLAŞIM/HABER: ...] -> [GELEN TEPKİ/YORUM: ...]" yapısı varsa:
Görevin sadece ana haberi özetlemek değil; halkın bu olaya verdiği TEPKİNİN (öfke, destek, protesto, alay) şiddeti, kutuplaşma ve kriz potansiyelini ölçmektir.
Yalnız ana gönderi (ajans/kanal) ise stratejik/kamu önemini (güvenlik, ekonomi vb.) değerlendir.
"""
        prompt = with_karargah_osint_directive(f"""
        Aşağıdaki {platform} içeriğini bir siyasi istihbarat analisti ve OSINT uzmanı gibi incele ve SADECE JSON formatında dön.
        {tepki_bloku if is_rx else ""}
        
        İÇERİK:
        "{text[:1800]}"
        
        JSON FORMATI:
        {{
            "topic": "Ana konu (Örn: Ekonomi, Terör, Eğitim, Seçim)",
            "frame": "Konunun sunuluş çerçevesi (Örn: Kriz, Başarı, Mağduriyet)",
            "stance": "support, oppose veya neutral",
            "target": "Eleştirilen/Desteklenen hedef kişi/kurum",
            "risk_level": "low, med veya high",
            "confidence": 0.9,
            "summary": "1 cümlelik net özet",
            "sentiment_score": -1.0 ile 1.0 arası sayısal değer,
            "manipulation_prob": 0.0 ile 1.0 arası manipülasyon/propaganda ihtimali,
            "bot_likelihood": 0.0 ile 1.0 arası bot/troll hesabı olma ihtimali,
            "sarcasm_detected": true veya false (alaycı/iğneleyici dil tespiti)
        }}
        """)
        safety = gemini_safety_settings_block_none()
        response = self.model.generate_content(prompt, safety_settings=safety)
        response_text = extract_gemini_response_text(response)
        if response_text is None:
            return {
                "topic": "Genel",
                "frame": "Haber",
                "stance": "neutral",
                "target": "Genel",
                "risk_level": "low",
                "confidence": 0.0,
                "summary": GEMINI_BLOCKED_PLAIN_MESSAGE,
                "sentiment_score": 0.0,
                "manipulation_prob": 0.0,
                "bot_likelihood": 0.0,
                "sarcasm_detected": False,
            }

        raw_json = response_text.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_json)
