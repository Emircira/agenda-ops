import os
import json
import google.generativeai as genai
from loguru import logger

class LabelingService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            model_name = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
            self.model = genai.GenerativeModel(model_name)
            self.is_llm_active = True
            logger.info("🚀 Gemini API Aktif: LabelingService tam kapasite çalışıyor.")
        else:
            self.is_llm_active = False
            self.model = None
            logger.warning("⚠️ GEMINI_API_KEY bulunamadı: LabelingService Fallback modunda çalışıyor.")


    def analyze_content(self, text: str, platform: str) -> dict:
        """İçeriği LLM ile analiz eder, başarısız olursa kural tabanlı yedeğe geçer."""
        if not text or len(text) < 10:
            return self._deterministic_fallback(text)

        if self.is_llm_active:
            try:
                return self._llm_analysis(text, platform)
            except Exception as e:
                logger.error(f"❌ LLM Analiz Hatası, Fallback devrede: {e}")
                return self._deterministic_fallback(text)
        else:
            return self._deterministic_fallback(text)

    def _llm_analysis(self, text: str, platform: str) -> dict:
        prompt = f"""
        Aşağıdaki {platform} içeriğini bir siyasi istihbarat analisti ve OSINT uzmanı gibi incele ve SADECE JSON formatında dön.
        
        İÇERİK:
        "{text[:1500]}"
        
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
        """
        response = self.model.generate_content(prompt)
        
        # Olası format hatalarını temizleme (Markdown tagleri vb.)
        raw_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(raw_json)

    def _deterministic_fallback(self, text: str) -> dict:
        """LLM çökerse sistemi ayakta tutacak Regex/Keyword tabanlı güvenilir analizci."""
        text_lower = text.lower()
        
        # Basit Kural Seti
        if any(w in text_lower for w in ["enflasyon", "zam", "fiyat", "maaş"]):
            topic, frame, risk = "Ekonomi", "Hayat Pahalılığı", "high"
        elif any(w in text_lower for w in ["sığınmacı", "mülteci", "suriyeli"]):
            topic, frame, risk = "Göçmenler", "Demografik Tehdit", "high"
        elif any(w in text_lower for w in ["terör", "operasyon", "şehit", "pkk"]):
            topic, frame, risk = "Güvenlik", "Terörle Mücadele", "med"
        else:
            topic, frame, risk = "Genel Gündem", "Haber Bildirimi", "low"

        return {
            "topic": topic,
            "frame": frame,
            "stance": "neutral",
            "target": "Bilinmiyor",
            "risk_level": risk,
            "confidence": 0.4,
            "summary": text[:100] + "..." if text else "İçerik yok.",
            "sentiment_score": 0.0,
            "manipulation_prob": 0.0,
            "bot_likelihood": 0.0,
            "sarcasm_detected": False
        }