import os
import json
import time
from loguru import logger
from typing import List, Dict, Any, Optional
from app.services.gemini_model import (
    GEMINI_BLOCKED_PLAIN_MESSAGE,
    create_gemini_model,
    extract_gemini_response_text,
    gemini_safety_settings_block_none,
)
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
        Güçlendirilmiş analiz prompt'u (Toplu/Batch mimarisi).
        Tüm içerikleri tek tek değil, genel kitle psikolojisi olarak analiz eder.
        """
        simplified = []
        for c in contents:
            raw_t = str(c.get("text", "") or "")
            is_reaction_pair = "[ANA PAYLAŞIM/HABER:" in raw_t and "[GELEN TEPKİ/YORUM:" in raw_t
            cap = 1600 if is_reaction_pair else 500
            entry = {
                "id": str(c["id"]),
                "text": raw_t[:cap],
                "kaynak_tipi": str(c.get("source_category") or "general_agenda"),
                "icerik_modu": "tepki_cifti" if is_reaction_pair else "tek_baslik",
            }
            simplified.append(entry)

        body = f"""GÖREV: Sana {len(simplified)} farklı gönderi veriyorum. Bunları tek tek değil, genel kitle psikolojisi olarak analiz et.

ÖNEMLİ — TOPLUMSAL TEPKİ RADARI:
- Verilerde "[ANA PAYLAŞIM/HABER: ...] -> [GELEN TEPKİ/YORUM: ...]" biçimi görebilirsin; bu, bir kullanıcının hangi ana gönderiye tepki verdiğini gösterir.
- Bu formatta görevin yalnızca haberi özetlemek DEĞİL; ana odağın, halkın bu olaya verdiği TEPKİNİN (öfke, destek, protesto, alay, ironi) şiddeti, kutuplaşma düzeyi ve kitlesel kriz potansiyelidir.
- "icerik_modu": "tepki_cifti" olan öğelerde özeti tepki dinamiğine göre yaz; "tek_baslik" olanlarda (ajans/kanal veya yalnız ana gönderi) kamu güvenliği, ekonomi, siyasi risk gibi stratejik önem ve çerçeveyi analiz et.

Ortak bir kategori (Label) ve bu kitle hareketinin genel risk skorunu (Score) tek bir JSON nesnesi olarak dön.
Kimlik veya yöntem belirtme; "summary" ve çerçeveleyici metinlerde yerli istihbarat brifingi üslubu kullan.

ZORUNLU JSON ŞEMASI (SADECE TEK BİR NESNE DÖN):
{{
    "topic": "Ana konu (Ekonomi, Seçim, Dış Politika, Güvenlik, Sosyal Politika, Sağlık, Eğitim, Spor, Genel)",
    "frame": "Mesajın çerçevesi (Kriz, Başarı, Mağduriyet, Eleştiri, Destek, Haber, Provokasyon)",
    "stance": "supportive | critical | neutral",
    "target": "Mesajın hedef aldığı kişi/kurum (yoksa 'Genel')",
    "risk_level": "low | medium | high | critical",
    "confidence": 0.0,
    "summary": "Tek cümlelik stratejik kitle psikolojisi özeti (tepki çiftiyse tepkiyi vurgula)",
    "sentiment": "Pozitif | Negatif | Nötr",
    "sentiment_score": 0.0,
    "manipulation_prob": 0.0,
    "bot_likelihood": 0.0,
    "sarcasm_detected": false,
    "crisis_score": 0
}}

KURALLAR:
1. SADECE geçerli bir JSON nesnesi (array değil, dict) döndür. Başka hiçbir metin, açıklama veya markdown ekleme.
2. confidence: 0.0-1.0 arası güven puanı
3. sentiment_score: -1.0 (çok olumsuz) ile 1.0 (çok olumlu) arası
4. manipulation_prob: 0.0-1.0 arası dezenformasyon/manipülasyon ihtimali
5. bot_likelihood: 0.0-1.0 arası bot/troll hesap ihtimali
6. crisis_score: 0-100 arası kriz potansiyeli (0: yok, 100: acil kriz)
7. sarcasm_detected: alaycı/iğneleyici dil varsa true
8. Haber ajansı kaynaklı tekil içeriklerde "rakip kampanyası" veya "ticari rakip" çerçevesinden kaçın; haber doğruluğu ve çerçeve üzerinden yorumla.
9. Hassas siyasi çerçeveler: summary ve çerçeve açıklamalarında üst direktifteki terminoloji filtresi ve stratejik raporlama diline uy.

İÇERİKLER (Toplu olarak değerlendir):
{json.dumps(simplified, ensure_ascii=False, indent=2)}"""
        return with_karargah_osint_directive(body)

    @staticmethod
    def _strip_markdown_json_fence(text: str) -> str:
        """Model bazen ```json ... ``` veya karışık markdown ile döner; parse öncesi sökülür."""
        if not text:
            return text
        t = text.strip()
        for fence in ("```json", "```JSON", "```"):
            t = t.replace(fence, "")
        return t.strip()

    def _parse_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        Gemini yanıtını güvenli şekilde JSON'a çevirir.
        """
        if not response_text:
            return None

        text = self._strip_markdown_json_fence(response_text)

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

        # JSON objesinin başlangıcını bul
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx:end_idx + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Gemini JSON parse hatası: {e}")
            logger.debug(f"Ham yanıt (ilk 500 karakter): {response_text[:500]}")
            return None

        if isinstance(data, list) and len(data) > 0:
            return data[0]
        
        return data if isinstance(data, dict) else None

    @staticmethod
    def _blocked_batch_analysis_json_string() -> str:
        """Google yanıt vermediğinde batch analiz şemasına uygun tek JSON nesnesi."""
        return json.dumps(
            {
                "topic": "Genel",
                "frame": "Haber",
                "stance": "neutral",
                "target": "Genel",
                "risk_level": "low",
                "confidence": 0.0,
                "summary": GEMINI_BLOCKED_PLAIN_MESSAGE,
                "sentiment": "Nötr",
                "sentiment_score": 0.0,
                "manipulation_prob": 0.0,
                "bot_likelihood": 0.0,
                "sarcasm_detected": False,
                "crisis_score": 0,
            },
            ensure_ascii=False,
        )

    def _safe_generate(self, prompt: str) -> Optional[str]:
        """
        Gemini API'yi güvenli çağırır — retry + backoff.
        """
        safety = gemini_safety_settings_block_none()
        for attempt in range(self.MAX_RETRIES):
            try:
                # Rate limit koruması
                if attempt > 0:
                    time.sleep(self.API_DELAY * (attempt + 1))

                # prompt zaten with_karargah_osint_directive ile üretildiyse tekrar sarmalama
                response = self.model.generate_content(
                    prompt,
                    safety_settings=safety,
                )
                text = extract_gemini_response_text(response)
                if text is None:
                    return self._blocked_batch_analysis_json_string()
                return text

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
        • 20 içerik için tek API isteği ile genel analiz sonucu alınır ve tüm listeye atanır
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

                group_result = self._parse_response(response_text)

                if group_result:
                    # 20 öğenin tümüne aynı genel sonucu ata
                    for c in batch:
                        item_res = group_result.copy()
                        item_res["id"] = c["id"]
                        all_results.append(item_res)
                        
                    logger.info(
                        f"✅ Gemini batch {batch_num}/{total_batches}: "
                        f"{len(batch)} içeriğe ortak kitle psikolojisi skoru atandı"
                    )
                else:
                    logger.warning(
                        f"Gemini batch {batch_num}/{total_batches}: JSON parse başarısız — "
                        f"ham içeriklerle graceful fallback uygulanıyor."
                    )
                    all_results.extend(self._fallback_per_item_from_batch(batch))

                # Batch'ler arası bekleme (rate limit koruması)
                if batch_idx + self.BATCH_SIZE < len(contents):
                    time.sleep(self.API_DELAY)

            except Exception as e:
                logger.error(f"❌ Gemini batch {batch_num}/{total_batches} HATA (atlanıyor): {e}")
                continue

        logger.info(f"🏁 Gemini Batch Analiz Tamamlandı: {len(all_results)}/{len(contents)} başarılı")
        return all_results

    def _fallback_per_item_from_batch(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """JSON parse başarısız olduğunda zinciri beslemek için minimal güvenli kayıtlar (DB ile uyumlu alanlar)."""
        out: List[Dict[str, Any]] = []
        for c in batch:
            cid = str(c.get("id") or "").strip()
            if not cid:
                continue
            snippet = str(c.get("text") or "")[:400]
            out.append({
                "id": cid,
                "topic": "Genel",
                "frame": "Haber",
                "stance": "neutral",
                "target": "Genel",
                "risk_level": "low",
                "confidence": 0.35,
                "summary": snippet or "Otomatik geçiş: LLM çıktısı çözümlenemedi.",
                "sentiment": "Nötr",
                "sentiment_score": 0.0,
                "manipulation_prob": 0.0,
                "bot_likelihood": 0.0,
                "sarcasm_detected": False,
                "crisis_score": 0,
            })
        return out

    async def analyze_city_complaints_hybrid(
        self,
        city_label: str,
        rss_items: List[Dict[str, Any]],
        x_posts: List[Dict[str, Any]],
    ) -> str:
        """Şikayet Radarı — RSS + X birleşik metin; kriz süzgeci talimatı."""
        rss_lines: List[str] = []
        for i, r in enumerate(rss_items[:12], 1):
            t = (r.get("text") or "").replace("\n", " ").strip()
            if t:
                rss_lines.append(f"{i}. {t[:520]}")
        x_lines: List[str] = []
        for i, p in enumerate(x_posts[:18], 1):
            author = p.get("author") or "?"
            t = (p.get("text") or "").replace("\n", " ").strip()
            if t:
                x_lines.append(f"{i}. @{author}: {t[:440]}")

        rss_blob = "\n".join(rss_lines) if rss_lines else "(RSS örneği yok)"
        x_blob = "\n".join(x_lines) if x_lines else "(X örneği yok)"

        prompt = f"""Sana sağlanan kaynakları (yalnızca haberler veya haber+sosyal medya) kullanarak {city_label} bağlamında boş siyasi söylemleri ve magazin gürültüsünü ele.

Yalnızca halkın doğrudan yaşam kalitesini bozan (altyapı, su, elektrik, güvenlik, ekonomik isyan, yol/ulaşım kesintisi vb.) gerçek kriz veya yoğun mağduriyet sinyallerini raporla.

ŞEHİR / BÖLGE: {city_label}

[RSS — haber özetleri]
{rss_blob}

[X — sosyal akış örnekleri]
{x_blob}

Kurallar: Türkçe, nötr istihbarat brifingi üslubu; kendini yapay zeka veya dil modeli olarak tanıtma. Kaynaklarda net sinyal yoksa bunu kısaca belirt."""

        return await self.generate_content_async(prompt)

    async def generate_content_async(self, prompt: str) -> str:
        """Asenkron içerik üretimi."""
        try:
            if not self.model:
                return "Hata: Gemini modeli başlatılamadı."
            safety = gemini_safety_settings_block_none()
            response = await self.model.generate_content_async(
                with_karargah_osint_directive(prompt),
                safety_settings=safety,
            )
            text = extract_gemini_response_text(response)
            if text is None:
                return GEMINI_BLOCKED_PLAIN_MESSAGE
            return text
        except Exception as e:
            logger.error(f"Gemini Async Hata: {e}")
            return f"Hata: {str(e)}"
