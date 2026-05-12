import os
import json
import time
from loguru import logger
from typing import List, Dict, Any, Optional
from app.services.gemini_model import create_gemini_model
from app.services.karargah_llm_directive import with_karargah_osint_directive


class GeminiAIClient:
    """
    Gemini AI analiz motoru — Production-Ready.
    • Mini-batch analiz (20'lik paketler) → token limiti aşılmaz
    • Otomatik retry + backoff mekanizması
    • Güçlendirilmiş prompt (Türkçe siyasi istihbarat odaklı)
    • Hata toleranslı JSON parse (kısmi başarıda veri kaybı olmaz)
    """

    # Mini-batch boyutu: Gemini token limiti aşılmasın
    BATCH_SIZE = 20
    # API istekleri arası bekleme (saniye)
    API_DELAY = 2.0
    # Rate limit sonrası bekleme (saniye)
    RATE_LIMIT_DELAY = 15.0
    # Maksimum retry
    MAX_RETRIES = 3

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model = None
        if not self.api_key:
            logger.error("GEMINI_API_KEY bulunamadı!")
        else:
            try:
                self.model, self.model_name = create_gemini_model(self.api_key)
                logger.info(f"✅ Gemini AI Hazır: {self.model_name}")
            except Exception as e:
                logger.error(f"Gemini model başlatma hatası: {e}")

    def _build_prompt(self, contents: List[Dict[str, Any]]) -> str:
        """
        Güçlendirilmiş analiz prompt'u.
        Her içerik için ID ve kısa metin gönderir.
        """
        # Metni 500 karakterle sınırla (token tasarrufu)
        simplified = []
        for c in contents:
            entry = {
                "id": str(c["id"]),
                "text": str(c.get("text", ""))[:500],
                "kaynak_tipi": str(c.get("source_category") or "general_agenda"),
            }
            simplified.append(entry)

        body = f"""Sen bir siyasi istihbarat analisti ve OSINT uzmanısın. Türkiye bağlamında sosyal medya içeriklerini analiz et.

KAYNAK TİPİ (her içerikte "kaynak_tipi" alanı) — analizi buna göre çerçevele:
- competitor: Ticari veya politik rakip söylemi; stratejik rekabet ve tehdit perspektifi kullan.
- news_agency: Haber ajansı / objektif bilgi kaynağı; ticari rakip gibi varsayma; haber dili, tarafsızlık ve olası editoryal çerçeve üzerinden değerlendir.
- person_or_target: Takip edilen şahıs veya hedef; kişi odaklı izleme ve algı.
- general_agenda: Genel gündem / kolektif konuşma; trend sinyali.

Aşağıda {len(simplified)} adet sosyal medya içeriği verilmiştir. HER BİRİ için aşağıdaki JSON şemasını kullanarak analiz et.

ZORUNLU JSON ŞEMASI (her öğe için):
{{
    "id": "içerik_id_buraya",
    "topic": "Ana konu (Ekonomi, Seçim, Dış Politika, Güvenlik, Sosyal Politika, Sağlık, Eğitim, Spor, Genel)",
    "frame": "Mesajın çerçevesi (Kriz, Başarı, Mağduriyet, Eleştiri, Destek, Haber, Provokasyon)",
    "stance": "supportive | critical | neutral",
    "target": "Mesajın hedef aldığı kişi/kurum (yoksa 'Genel')",
    "risk_level": "low | medium | high | critical",
    "confidence": 0.0,
    "summary": "Tek cümlelik stratejik özet",
    "sentiment": "Pozitif | Negatif | Nötr",
    "sentiment_score": 0.0,
    "manipulation_prob": 0.0,
    "bot_likelihood": 0.0,
    "sarcasm_detected": false,
    "crisis_score": 0
}}

KURALLAR:
1. SADECE geçerli bir JSON dizisi (array) döndür. Başka hiçbir metin, açıklama veya markdown ekleme.
2. Her içerik için TAM OLARAK bir analiz objesi üret.
3. confidence: 0.0-1.0 arası güven puanı
4. sentiment_score: -1.0 (çok olumsuz) ile 1.0 (çok olumlu) arası
5. manipulation_prob: 0.0-1.0 arası dezenformasyon/manipülasyon ihtimali
6. bot_likelihood: 0.0-1.0 arası bot/troll hesap ihtimali
7. crisis_score: 0-100 arası kriz potansiyeli (0: yok, 100: acil kriz)
8. sarcasm_detected: alaycı/iğneleyici dil varsa true
9. Haber ajansı kaynaklı içeriklerde "rakip kampanyası" veya "ticari rakip" çerçevesinden kaçın; haber doğruluğu ve çerçeve üzerinden yorumla.

İÇERİKLER:
{json.dumps(simplified, ensure_ascii=False, indent=2)}"""
        return with_karargah_osint_directive(body)

    def _parse_response(self, response_text: str) -> List[Dict[str, Any]]:
        """
        Gemini yanıtını güvenli şekilde JSON'a çevirir.
        Markdown blokları, ekstra metin vb. temizler.
        """
        if not response_text:
            return []

        text = response_text.strip()

        # Markdown kod bloklarını temizle
        if "```json" in text:
            text = text.split("```json", 1)[1]
            if "```" in text:
                text = text.split("```", 1)[0]
            text = text.strip()
        elif "```" in text:
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1].strip()
            elif len(parts) == 2:
                text = parts[1].strip()

        # JSON array'in başlangıcını bul
        start_idx = text.find("[")
        end_idx = text.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx:end_idx + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Gemini JSON parse hatası: {e}")
            logger.debug(f"Ham yanıt (ilk 500 karakter): {response_text[:500]}")
            return []

        # Eğer yanıt bir obje içindeyse (örn: {"results": [...]}) onu çıkar
        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list):
                    return data[key]
            return []

        return data if isinstance(data, list) else []

    def _safe_generate(self, prompt: str) -> Optional[str]:
        """
        Gemini API'yi güvenli çağırır — retry + backoff.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                # Rate limit koruması
                if attempt > 0:
                    time.sleep(self.API_DELAY * (attempt + 1))

                response = self.model.generate_content(with_karargah_osint_directive(prompt))
                return response.text

            except Exception as e:
                error_str = str(e).lower()

                # Rate limit veya quota hatası
                if "429" in str(e) or "quota" in error_str or "rate" in error_str:
                    wait = self.RATE_LIMIT_DELAY * (attempt + 1)
                    logger.warning(
                        f"Gemini rate limit/quota (deneme {attempt+1}/{self.MAX_RETRIES}), "
                        f"{wait}sn bekleniyor..."
                    )
                    time.sleep(wait)
                # Geçici sunucu hatası
                elif "500" in str(e) or "503" in str(e) or "unavailable" in error_str:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Gemini sunucu hatası (deneme {attempt+1}): {e}, {wait}sn sonra tekrar...")
                    time.sleep(wait)
                else:
                    logger.error(f"Gemini API hatası (deneme {attempt+1}/{self.MAX_RETRIES}): {e}")
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(5)
                    else:
                        return None

        logger.error("Gemini API: Tüm denemeler tükendi!")
        return None

    def analyze_batch(self, contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        İçerikleri MINI-BATCH halinde analiz eder.
        • Her batch BATCH_SIZE (20) içerik
        • Hata olan batch atlanır, diğerleri devam eder
        • Kısmi başarıda veri kaybı olmaz
        """
        if not self.api_key or not self.model:
            raise RuntimeError("Gemini API Key veya model eksik; sahte/boş analiz sonucu dönülmez.")

        if not contents:
            return []

        all_results = []
        total_batches = (len(contents) + self.BATCH_SIZE - 1) // self.BATCH_SIZE

        logger.info(f"🧠 Gemini Batch Analiz: {len(contents)} içerik → {total_batches} mini-batch ({self.BATCH_SIZE}'lik paketler)")

        for batch_idx in range(0, len(contents), self.BATCH_SIZE):
            batch = contents[batch_idx:batch_idx + self.BATCH_SIZE]
            batch_num = (batch_idx // self.BATCH_SIZE) + 1

            try:
                prompt = self._build_prompt(batch)
                response_text = self._safe_generate(prompt)

                if not response_text:
                    logger.warning(f"Gemini batch {batch_num}/{total_batches}: Yanıt alınamadı, atlanıyor.")
                    continue

                batch_results = self._parse_response(response_text)

                if batch_results:
                    all_results.extend(batch_results)
                    logger.info(
                        f"✅ Gemini batch {batch_num}/{total_batches}: "
                        f"{len(batch_results)}/{len(batch)} içerik analiz edildi"
                    )
                else:
                    logger.warning(f"Gemini batch {batch_num}/{total_batches}: JSON parse sonucu boş.")

                # Batch'ler arası bekleme (rate limit koruması)
                if batch_idx + self.BATCH_SIZE < len(contents):
                    time.sleep(self.API_DELAY)

            except Exception as e:
                logger.error(f"❌ Gemini batch {batch_num}/{total_batches} HATA (atlanıyor): {e}")
                continue

        logger.info(f"🏁 Gemini Batch Analiz Tamamlandı: {len(all_results)}/{len(contents)} başarılı")
        return all_results

    async def generate_content_async(self, prompt: str) -> str:
        """Asenkron içerik üretimi."""
        try:
            if not self.model:
                return "Hata: Gemini modeli başlatılamadı."
            response = await self.model.generate_content_async(with_karargah_osint_directive(prompt))
            return response.text
        except Exception as e:
            logger.error(f"Gemini Async Hata: {e}")
            return f"Hata: {str(e)}"
