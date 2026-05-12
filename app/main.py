import os
import asyncio
import json
import io
import requests
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from contextlib import asynccontextmanager
import glob
import re

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, inspect, func
from pydantic import BaseModel, Field
from typing import Optional, List
import google.generativeai as genai
from app.services.gemini_service import GeminiAIClient
from app.services.gemini_model import create_gemini_model

# KARARGAH ANA MİMARİ İMPORTLARI
from app.db.session import get_db, engine
from app.models.core import Base, Opportunity, Content, Source
from app.api.routers import router

# ÖZEL SEÇİM/DEMOGRAFİ MODELLERİN
from app.models.core import (
    CityDemographics, DistrictDemographics, ElectionResult, 
    CandidateDemographic, RegionAnalysis, ElectionCategory
)
from app.core.utils import (
    pre_filter_content,
    extract_youtube_comments_as_articles,
    calculate_twitter_bot_likelihood,
    twitter_bot_signal_summary,
)

# --- TÜRKÇE KARAKTER YARDIMCISI ---
def normalize_tr(text):
    if not text: return ""
    return str(text).replace('İ', 'i').replace('I', 'ı').replace('Ş', 'ş').replace('Ğ', 'ğ').replace('Ü', 'ü').replace('Ö', 'ö').replace('Ç', 'ç').lower()

# --- BAĞLAM İZOLASYONU (Context Isolation) YARDIMCISI ---
CONTEXT_ISOLATION_WARNING = """⚠️ DİKKAT — VERİ ZEHİRLENMESİ RİSKİ (CONTEXT ISOLATION):
Sana verilen veriler Spor, Siyaset ve Ekonomi gibi FARKLI KATEGORİLERDEN gelmektedir.
Kategorileri BİRBİRİNE KARIŞTIRMA! Kurallar:
1. Spor verilerindeki bir başkanlık seçimini, yönetim istifasını veya taraftar sloganını KESİNLİKLE siyasi bir kriz/fırsat olarak RAPORLAMA.
2. Siyasi lider analizlerini SADECE 'SİYASET' ve 'GENEL' verilerinden yap.
3. Ekonomi verilerini siyasi skorlamada DOĞRUDAN kullanma, sadece makroekonomik gösterge olarak değerlendir.
4. Spor verilerini, sadece toplumun genel psikolojik stresini (sosyolojik fay hatları) ölçmek için AYRI bir 'Spor Gündemi' başlığı altında analiz et."""

DOMAIN_LABELS = {
    'politics': 'SİYASET VERİLERİ',
    'sports': 'SPOR VERİLERİ',
    'economy': 'EKONOMİ VERİLERİ',
    'general': 'GENEL VERİLER'
}

def format_contents_by_domain(contents, max_chars=4000):
    """İçerikleri domain'e göre gruplar ve etiketli string döner."""
    groups = {}
    for c in contents:
        d = getattr(c, 'domain', 'general') or 'general'
        groups.setdefault(d, []).append(f"[{c.platform.upper()}] @{c.author_name}: {c.text}")
    
    parts = []
    total = 0
    for domain, label in DOMAIN_LABELS.items():
        items = groups.get(domain, [])
        if items:
            block = f"\n--- {label} ({len(items)} içerik) ---\n" + "\n".join(items)
            if total + len(block) > max_chars:
                block = block[:max(0, max_chars - total)]
            parts.append(block)
            total += len(block)
            if total >= max_chars:
                break
    
    return "\n".join(parts) if parts else "Veri bulunamadı."

# --- GLOBAL DURUM TAKİBİ ---
RESEARCH_PROGRESS = {} # { "keyword": { "status": "...", "percent": 0 } }

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 [1/4] Karargah Başlatılıyor: Sistem Kontrolleri Devrede...")
    
    # 1. VERİTABANI BAĞLANTI KONTROLÜ
    try:
        logger.info("📡 [2/4] Veritabanı bağlantısı kontrol ediliyor...")
        from sqlalchemy import text
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=5.0)
        logger.info("✅ Veritabanı: BAĞLANTI BAŞARILI")
    except Exception as e:
        logger.error(f"❌ Veritabanı: BAĞLANTI HATASI (Sistem devam ediyor): {e}")

    # 2. REDIS BAĞLANTI KONTROLÜ
    try:
        logger.info("📡 [3/4] Redis (Celery Broker) bağlantısı kontrol ediliyor...")
        import redis.asyncio as redis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = redis.from_url(redis_url)
        await asyncio.wait_for(r.ping(), timeout=5.0)
        await r.close()
        logger.info("✅ Redis: BAĞLANTI BAŞARILI")
    except Exception as e:
        logger.error(f"❌ Redis: BAĞLANTI HATASI (Sistem devam ediyor): {e}")

    # 3. AI (GEMINI) KONTROLÜ
    try:
        logger.info("📡 [4/4] Yapay Zeka (Gemini) yapılandırması kontrol ediliyor...")
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
            # Desteklenen modelleri listele
            models = genai.list_models()
            model_list = [m.name for m in models]
            logger.info(f"✅ Gemini API: HAZIR (Key: {api_key[:5]}***)")
            logger.info(f"📋 Desteklenen Modeller: {', '.join(model_list)}")
        else:
            logger.warning("⚠️ Gemini API: ANAHTAR EKSİK (AI özellikleri kısıtlı çalışacak)")
    except Exception as e:
        logger.error(f"❌ Gemini: KONTROL HATASI: {e}")

    logger.info("🇹🇷 Karargah Sistemleri Çevrimiçi ve Hazır!")
    yield
    logger.info("Karargah Sistemleri Kapanıyor...")

# FASTAPI TANIMLAMASI
app = FastAPI(
    title="AgendaOps MVP", 
    description="Gündem Kurma ve Karar Destek İstihbarat Sistemi",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
templates = Jinja2Templates(directory="app/templates")

def get_gemini_model():
    """Gemini API modelini döndürür."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY bulunamadı!")
        return None
    model, _ = create_gemini_model(api_key)
    return model

async def gemini_generate_content(prompt: str):
    """Gemini ile içerik üretir."""
    model = get_gemini_model()
    if not model:
        raise RuntimeError("Gemini API Key eksik; sahte analiz metni üretilemez.")
    response = await model.generate_content_async(prompt)
    return response.text

# =======================================================
# TEMEL ROTALAR VE DASHBOARD
# =======================================================
@app.get("/", include_in_schema=False)
async def read_root():
    return RedirectResponse(url="/dashboard")

@app.get("/api/init-db", tags=["Veritabanı"])
async def zorla_tablo_olustur():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(ElectionResult.metadata.create_all)
        await conn.run_sync(RegionAnalysis.metadata.create_all)
        # Sources ve Contents tablolarına domain kolonu ekle + type enum fix
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE sources ALTER COLUMN type TYPE VARCHAR USING type::text"))
        except Exception:
            pass
        try:
            from sqlalchemy import text
            await conn.execute(text("ALTER TABLE sources ADD COLUMN IF NOT EXISTS domain VARCHAR DEFAULT 'general'"))
            await conn.execute(text("ALTER TABLE contents ADD COLUMN IF NOT EXISTS domain VARCHAR DEFAULT 'general'"))
            await conn.execute(text("ALTER TABLE contents ADD COLUMN IF NOT EXISTS is_analyzed BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE contents ADD COLUMN IF NOT EXISTS raw_json JSONB DEFAULT '{}'"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contents_is_analyzed ON contents (is_analyzed)"))
            
            # ContentLabel genişletmeleri
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS sentiment_score FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS manipulation_prob FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS bot_likelihood FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS sarcasm_detected BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS crisis_score INTEGER DEFAULT 0"))
            await conn.execute(text("ALTER TABLE content_labels ADD COLUMN IF NOT EXISTS sentiment VARCHAR"))
        except Exception as e:
            logger.warning(f"Kolon ekleme atlandı veya hata oluştu: {e}")
            pass
    return {"mesaj": "🚀 HAREKAT BAŞARILI: Tablolar oluşturuldu!"}

@app.get("/dashboard", include_in_schema=False)
async def dashboard(request: Request, window: int = 6, db: AsyncSession = Depends(get_db)):
    try:
        time_threshold = datetime.utcnow() - timedelta(hours=window)
        
        # Veritabanı sorguları
        c_res = await db.execute(select(Content).where(Content.published_at >= time_threshold).order_by(desc(Content.published_at)).limit(100))
        o_res = await db.execute(select(Opportunity).order_by(desc(Opportunity.score)).limit(10))
        s_res = await db.execute(select(Source).order_by(desc(Source.id)))
        
        poll_query = select(Content).where(Content.platform == 'poll').order_by(desc(Content.published_at)).limit(1)
        poll_result = await db.execute(poll_query)
        latest_poll = poll_result.scalar_one_or_none()
        
        poll_json = "null"
        if latest_poll and latest_poll.raw_json:
            poll_data = {
                "firm": latest_poll.author_name,
                "date": latest_poll.published_at.strftime('%d.%m.%Y'),
                "sample": latest_poll.raw_json.get("sample", 0),
                "data": [
                    latest_poll.raw_json.get("chp", 0), latest_poll.raw_json.get("akp", 0),
                    latest_poll.raw_json.get("mhp", 0), latest_poll.raw_json.get("dem", 0),
                    latest_poll.raw_json.get("iyi", 0), latest_poll.raw_json.get("yrp", 0),
                ]
            }
            poll_json = json.dumps(poll_data)

        return templates.TemplateResponse("dashboard.html", {
            "request": request, "contents": c_res.scalars().all(), 
            "opportunities": o_res.scalars().all(), "sources": s_res.scalars().all(), "poll_json": poll_json
        })
    except Exception as e:
        logger.error(f"Dashboard hatası: {e}")
        # Eğer tablo bulunamadı hatasıysa kullanıcıyı init-db'ye yönlendirecek bir mesaj göster
        error_msg = str(e)
        if "relation" in error_msg and "does not exist" in error_msg:
            return templates.TemplateResponse("error.html", {
                "request": request, 
                "message": "⚠️ Veritabanı tabloları henüz oluşturulmamış.",
                "action_url": "/api/init-db",
                "action_text": "Tabloları Şimdi Oluştur"
            })
        return templates.TemplateResponse("error.html", {"request": request, "message": f"Sistem Hatası: {error_msg}"})

@app.get("/secim", include_in_schema=False)
async def secim_page(request: Request):
    return templates.TemplateResponse("secim.html", {"request": request})

# =======================================================
# KAYNAK YÖNETİMİ (Source CRUD — /api/sources)
# =======================================================
class SourceCreateRequest(BaseModel):
    name: str
    url: str
    type: str
    domain: str = "general"  # politics, sports, economy, general

@app.post("/api/sources", tags=["Kaynak Yönetimi"])
async def create_source_direct(req: SourceCreateRequest, db: AsyncSession = Depends(get_db)):
    """Yeni istihbarat kaynağı ekler (dashboard frontend'den gelen çağrılar için)."""
    try:
        new_source = Source(name=req.name, url=req.url, type=req.type, domain=req.domain, active=True)
        db.add(new_source)
        await db.commit()
        await db.refresh(new_source)
        return {"success": True, "id": new_source.id, "message": f"'{req.name}' kaynağı başarıyla eklendi."}
    except Exception as e:
        await db.rollback()
        logger.error(f"Kaynak ekleme hatası: {e}")
        return {"success": False, "error": str(e)}

@app.delete("/api/sources/{source_id}", tags=["Kaynak Yönetimi"])
async def delete_source_direct(source_id: int, db: AsyncSession = Depends(get_db)):
    """Kaynağı siler."""
    try:
        res = await db.execute(select(Source).where(Source.id == source_id))
        source = res.scalar_one_or_none()
        if not source:
            return {"success": False, "error": "Kaynak bulunamadı."}
        await db.delete(source)
        await db.commit()
        return {"success": True, "message": "Kaynak silindi."}
    except Exception as e:
        await db.rollback()
        return {"success": False, "error": str(e)}

# --- Doğrudan Veri Çekici (Celery/Redis BAĞIMSIZ) ---
@app.post("/api/run-worker/{worker_name}", tags=["Veri Çekme"])
async def run_worker(worker_name: str, db: AsyncSession = Depends(get_db)):
    """RSS/YouTube/Twitter verilerini Celery olmadan doğrudan çeker."""
    try:
        if worker_name == 'rss':
            from app.providers.rss_provider import RSSProvider
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            
            provider = RSSProvider()
            # Veritabanındaki aktif RSS kaynaklarını al
            res = await db.execute(
                select(Source.id, Source.name, Source.url, Source.domain)
                .where(Source.type == 'rss', Source.active == True)
            )
            sources = [dict(row) for row in res.mappings().all()]
            
            # Gömülü (Default) Kaynaklar + DB Kaynakları
            all_sources = [{"name": "Google Haberler", "url": "https://news.google.com/rss?hl=tr&gl=TR&ceid=TR:tr", "id": None, "domain": "general"}]
            for s in sources:
                all_sources.append({"name": s["name"], "url": s["url"], "id": s["id"], "domain": s.get("domain") or "general"})
            
            total_added = 0
            total_skipped = 0
            errors = []
            
            for s_info in all_sources:
                try:
                    articles = await provider.fetch_feed(s_info["url"], s_info["name"])
                    for article in articles:
                        article['domain'] = s_info['domain']
                        article['source_id'] = s_info['id']
                        article['is_analyzed'] = True
                        try:
                            stmt = pg_insert(Content).values(**article)
                            stmt = stmt.on_conflict_do_nothing(index_elements=['external_id'])
                            result = await db.execute(stmt)
                            if result.rowcount > 0:
                                total_added += 1
                            else:
                                total_skipped += 1
                        except Exception as insert_err:
                            total_skipped += 1
                            logger.warning(f"İçerik kayıt hatası: {insert_err}")
                except Exception as src_err:
                    errors.append(f"{s_info['name']}: {str(src_err)}")
                    logger.error(f"RSS çekim hatası [{s_info['name']}]: {src_err}")
            
            await db.commit()
            from app.workers.ingest_tasks import _trigger_analysis_chain
            _trigger_analysis_chain("RSS")
            msg = f"✅ {len(sources)} kaynak tarandı. {total_added} yeni içerik eklendi, {total_skipped} zaten mevcuttu."
            if errors:
                msg += f" ⚠️ {len(errors)} kaynakta hata: {'; '.join(errors[:3])}"
            logger.info(msg)
            return {"success": True, "message": msg, "added": total_added, "skipped": total_skipped}
        
        elif worker_name == 'youtube':
            from app.providers.youtube_provider import YouTubeProvider, YouTubeQuotaExceeded
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            
            provider = YouTubeProvider()
            res = await db.execute(
                select(Source.id, Source.name, Source.url, Source.domain)
                .where(Source.type == 'youtube', Source.active == True)
            )
            sources = [dict(row) for row in res.mappings().all()]
            
            if not sources: return {"success": False, "error": "Hiç YouTube kaynağı tanımlı değil."}
            
            total_added, total_skipped = 0, 0
            for source in sources:
                source_id = source["id"]
                source_name = source["name"]
                source_url = source["url"]
                source_domain = source.get("domain") or "general"
                try:
                    # 'UC' ile başlıyorsa kanal id'si olarak kabul et, yoksa keyword araması yap
                    if source_url.startswith('UC'):
                        videos = await provider.fetch_channel_videos(source_url)
                    else:
                        videos = await provider.fetch_keyword_videos(source_url)
                    
                    for video in videos:
                        vid_id = video["id"] if isinstance(video.get("id"), str) else video["id"]["videoId"]
                        # Videonun altındaki en hit 10 yorumu çekiyoruz
                        comments = await provider.fetch_video_comments(vid_id, max_results=10)
                        articles = extract_youtube_comments_as_articles(video, comments, source_id, source_domain)
                        
                        for article in articles:
                            article['is_analyzed'] = True
                            stmt = pg_insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                            result = await db.execute(stmt)
                            if result.rowcount > 0: total_added += 1
                            else: total_skipped += 1
                except Exception as e:
                    if isinstance(e, YouTubeQuotaExceeded):
                        logger.error(f"YouTube kotası doldu [{source_name}], diğer kaynaklara geçiliyor: {e}")
                        continue
                    logger.error(f"YouTube hatası [{source_name}]: {e}")
            await db.commit()
            from app.workers.ingest_tasks import _trigger_analysis_chain
            _trigger_analysis_chain("YouTube")
            return {"success": True, "message": f"✅ {len(sources)} YouTube kaynağı tarandı. {total_added} içerik eklendi.", "added": total_added, "skipped": total_skipped}
        
        elif worker_name == 'twitter':
            from app.workers.ingest_tasks import get_x_provider, _post_to_article
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            import asyncio as _asyncio
            
            provider = get_x_provider()
            res = await db.execute(
                select(Source.id, Source.type, Source.name, Source.url, Source.domain).where(
                    Source.type.in_(['twitter_self', 'twitter_competitor', 'twitter_trend', 'x']),
                    Source.active == True
                )
            )
            sources = [dict(row) for row in res.mappings().all()]
            
            total_added, total_skipped = 0, 0
            errors = []
            
            for idx, source in enumerate(sources):
                source_id = source["id"]
                source_type = source["type"]
                source_name = source["name"] or source["url"]
                source_url = source["url"]
                source_domain = source.get("domain") or "general"
                try:
                    logger.info(f"[{idx+1}/{len(sources)}] Twitter taraması: {source_name} ({source_type})")
                    
                    if source_type == 'twitter_trend':
                        posts = await provider.fetch_keyword_posts(source_url, limit=50)
                    else:
                        posts = await provider.fetch_mentions(source_url)
                    
                    for post in posts:
                        try:
                            article = _post_to_article(
                                post, 
                                source_id=source_id, 
                                domain=source_domain
                            )
                            stmt = pg_insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                            result = await db.execute(stmt)
                            if result.rowcount > 0: total_added += 1
                            else: total_skipped += 1
                        except Exception as e:
                            total_skipped += 1
                            logger.warning(f"Veri kayıt hatası (atlandı): {e}")
                            
                except Exception as e:
                    errors.append(f"{source_name}: {str(e)[:50]}")
                    logger.error(f"Twitter hatası [{source_name}]: {e}")
                
                # Kaynaklar arası rate-limit bekleme
                if idx < len(sources) - 1:
                    await _asyncio.sleep(3)
            
            # Kaynak yoksa gündem tara
            if not sources:
                try:
                    posts = await provider.fetch_keyword_posts("Türkiye gündemi", limit=50)
                    for post in posts:
                        try:
                            article = _post_to_article(post, source_id=None, domain="general")
                            stmt = pg_insert(Content).values(**article).on_conflict_do_nothing(index_elements=['external_id'])
                            result = await db.execute(stmt)
                            if result.rowcount > 0: total_added += 1
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Gündem taraması hatası: {e}")
            
            await db.commit()
            from app.workers.ingest_tasks import _trigger_analysis_chain
            _trigger_analysis_chain("Twitter")
            src_count = len(sources) if sources else 1
            msg = f"✅ {src_count} Twitter kaynağı tarandı. {total_added} içerik eklendi, {total_skipped} mevcut."
            if errors:
                msg += f" ⚠️ {len(errors)} hata: {'; '.join(errors[:3])}"
            return {"success": True, "message": msg, "added": total_added, "skipped": total_skipped}
        
        else:
            return {"success": False, "error": f"Bilinmeyen worker: {worker_name}"}
    except Exception as e:
        logger.error(f"Worker hatası [{worker_name}]: {e}")
        return {"success": False, "error": str(e)}

# --- Anket CRUD (Polls) ---
class PollCreateRequest(BaseModel):
    firm: str
    sample: int = 0
    chp: float = 0
    akp: float = 0
    mhp: float = 0
    dem: float = 0
    iyi: float = 0
    yrp: float = 0

@app.post("/api/polls", tags=["Anket Yönetimi"])
async def create_poll(req: PollCreateRequest, db: AsyncSession = Depends(get_db)):
    """Manuel anket verisi ekler ve Content tablosuna kaydeder."""
    try:
        import uuid
        poll_content = Content(
            id=uuid.uuid4(),
            platform='poll',
            external_id=f"poll_{req.firm}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            author_name=req.firm,
            published_at=datetime.utcnow(),
            text=f"Anket: {req.firm} — CHP:{req.chp}, AKP:{req.akp}, MHP:{req.mhp}, DEM:{req.dem}, İYİ:{req.iyi}, YRP:{req.yrp}",
            content_type='article',
            raw_json={
                "sample": req.sample,
                "chp": req.chp, "akp": req.akp, "mhp": req.mhp,
                "dem": req.dem, "iyi": req.iyi, "yrp": req.yrp
            }
        )
        db.add(poll_content)
        await db.commit()
        return {"success": True, "message": "Anket verisi kaydedildi."}
    except Exception as e:
        await db.rollback()
        return {"success": False, "error": str(e)}

@app.post("/api/extract-polls", tags=["Anket Yönetimi"])
async def extract_polls_from_news(db: AsyncSession = Depends(get_db)):
    """Yapay zeka ile haberlerden anket verisi çıkarır."""
    try:
        # Son 48 saatteki haberleri çek
        time_limit = datetime.utcnow() - timedelta(hours=48)
        res = await db.execute(select(Content).where(Content.published_at >= time_limit, Content.platform == 'rss'))
        contents = res.scalars().all()
        
        if not contents:
            return {"success": False, "error": "Son 48 saatte haber bulunamadı."}
            
        texts = "\n".join([c.text for c in contents[:20]]) # İlk 20 habere odaklan
        prompt = f"""Aşağıdaki haber metinlerinden seçim anketi sonuçlarını çıkar. 
        Sadece JSON formatında şu yapıda dön: 
        {{"found": true, "firm": "Şirket Adı", "chp": 30.1, "akp": 32.5, ...}}
        Eğer anket yoksa {{"found": false}} dön.
        
        Metinler:
        {texts}
        """
        
        ai_response = await gemini_generate_content(prompt)
        # JSON temizleme
        if "```json" in ai_response:
            ai_response = ai_response.split("```json")[1].split("```")[0].strip()
            
        data = json.loads(ai_response)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Poll extraction error: {e}")
        return {"success": False, "error": str(e)}

# =======================================================
# YAPAY ZEKA ANALİZ ROTALARI (TOPLU ANALİZ, HABER TARAMA)
# =======================================================
class BulkAnalysisRequest(BaseModel): window: int = 24

@app.post("/api/bulk-analyze", tags=["Yapay Zeka Analiz"])
async def bulk_analyze(req: BulkAnalysisRequest, db: AsyncSession = Depends(get_db)):
    """Tüm içerikleri toplu analiz eder."""
    from app.workers.labeling_tasks import batch_analyze_contents
    batch_analyze_contents.delay()
    return {"success": True, "message": "Toplu analiz kuyruğa alındı (Gemini)."}

@app.post("/api/twitter-bulk-analyze", tags=["Yapay Zeka Analiz"])
async def twitter_bulk_analyze(req: BulkAnalysisRequest, db: AsyncSession = Depends(get_db)):
    """Twitter içeriklerini analiz eder."""
    from app.workers.labeling_tasks import batch_analyze_contents
    batch_analyze_contents.delay()
    return {"success": True, "message": "Twitter analizi başlatıldı."}

class NewsScanRequest(BaseModel): keyword: str

@app.post("/api/news-scan", tags=["Yapay Zeka Analiz"])
async def news_scan(req: NewsScanRequest, db: AsyncSession = Depends(get_db)):
    """Belirli bir kelime için haber taraması ve analizi yapar."""
    logger.error("news_scan endpoint'i gerçek haber taraması uygulamıyor; sahte başarı döndürülmedi.")
    raise HTTPException(status_code=501, detail="Gerçek haber taraması bu endpoint'te uygulanmamış. Sahte başarı döndürülmez.")

# --- GÜNDEM ÖZETİ CACHE SİSTEMİ ---
_hot_topics_cache = {"data": None, "timestamp": None}
HOT_TOPICS_CACHE_TTL = timedelta(hours=6)

@app.get("/api/dashboard/hot-topics", tags=["Dashboard Verileri"])
async def get_dashboard_hot_topics(refresh: bool = False, db: AsyncSession = Depends(get_db)):
    """Gündemdeki sıcak gelişmeleri özetler."""
    global _hot_topics_cache
    now = datetime.utcnow()
    
    if not refresh and _hot_topics_cache["data"] and _hot_topics_cache["timestamp"] and (now - _hot_topics_cache["timestamp"] < HOT_TOPICS_CACHE_TTL):
        return {**_hot_topics_cache["data"], "cached": True}

    try:
        time_threshold = now - timedelta(hours=24)
        res = await db.execute(select(Content).where(Content.published_at >= time_threshold).limit(50))
        contents = res.scalars().all()
        
        if not contents:
            return {"status": "success", "topics": [], "alerts": ["Son 24 saatte veri bulunamadı."]}

        text_blob = format_contents_by_domain(contents)
        prompt = f"""Aşağıdaki sosyal medya ve haber verilerini analiz ederek Türkiye gündemindeki en önemli 3 konuyu belirle.
        Her konu için: Başlık, Özet, Fırsat Skoru (0-100), Kriz Skoru (0-100), Aksiyon Tavsiyesi ve Hedef Kitle belirle.
        Ayrıca genel bir duygu analizi ve önemli uyarılar ekle.
        Yanıtı SADECE JSON formatında şu yapıda dön:
        {{
            "topics": [
                {{"title": "..", "summary": "..", "opportunity_score": 80, "crisis_score": 20, "action_advice": "..", "target_audience": ".."}}
            ],
            "competitor_sentiment": {{"leader": {{"positive": 60, "negative": 40}}, "competitor": {{"positive": 45, "negative": 55}}}},
            "alerts": ["..", ".."]
        }}
        
        Veriler:
        {text_blob}
        """
        
        ai_response = await gemini_generate_content(prompt)
        if "```json" in ai_response:
            ai_response = ai_response.split("```json")[1].split("```")[0].strip()
        
        data = json.loads(ai_response)
        _hot_topics_cache = {"data": data, "timestamp": now}
        return {**data, "cached": False}
    except Exception as e:
        logger.error(f"Hot topics error: {e}")
        return {"status": "error", "message": str(e)}

# =======================================================
# GERÇEK VERİ ANALİZ ROTALARI (4 YENİ ENDPOINT)
# =======================================================

# 1. X (Twitter) Stratejik Üçlü Analiz (Kendi vs Rakip vs Gündem)
@app.get("/api/analysis/triple-compare", tags=["Yapay Zeka Analiz"])
async def triple_compare_analysis(db: AsyncSession = Depends(get_db)):
    """X (Twitter) için Kendi Hesabımız, Rakip ve Gündem karşılaştırmalı analizi yapar."""
    try:
        from app.models.core import Content, Source
        # Son 24 saatteki X içeriklerini kaynak tiplerine göre çek
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        
        query = select(Content, Source.type).join(Source, Content.source_id == Source.id).where(
            Content.platform.in_(['x', 'twitter']),
            Content.published_at >= time_threshold
        )
        res = await db.execute(query)
        rows = res.all()
        
        # Gruplandırma
        data = {"self": [], "competitor": [], "trend": []}
        for content, s_type in rows:
            clean_text = content.text[:200] + "..." if len(content.text) > 200 else content.text
            if s_type == "twitter_self":
                data["self"].append(clean_text)
            elif s_type == "twitter_competitor":
                data["competitor"].append(clean_text)
            elif s_type == "twitter_trend":
                data["trend"].append(clean_text)

        if not any(data.values()):
            return {"success": False, "error": "Son 24 saatte analiz edilecek X verisi bulunamadı."}

        # Gemini için prompt hazırla
        prompt = f"""
        Aşağıda bir siyasi aktörün kendi paylaşımları, rakibinin paylaşımları ve genel Twitter gündemi yer almaktadır.
        Bu verileri karşılaştırarak stratejik bir 'Üçlü Analiz' (Triple Analysis) yap.
        
        KENDİ PAYLAŞIMLARIMIZ:
        {json.dumps(data["self"][:5], ensure_ascii=False)}
        
        RAKİP PAYLAŞIMLARI:
        {json.dumps(data["competitor"][:5], ensure_ascii=False)}
        
        GENEL GÜNDEM (Trendler):
        {json.dumps(data["trend"][:5], ensure_ascii=False)}
        
        Lütfen şu başlıklarla analiz et:
        1. **Uyum Analizi**: Kendi söylemlerimiz genel gündemle ne kadar örtüşüyor? (Gündemi biz mi belirliyoruz yoksa takip mi ediyoruz?)
        2. **Rakip Tehdidi**: Rakip hangi konularda bizden daha fazla etkileşim/alan kazanıyor?
        3. **İdeolojik Farklar**: Söylemlerdeki temel ideolojik ayrışma noktaları neler?
        4. **Aksiyon Önerisi**: Gündemi ele geçirmek için hangi söylem değişikliği yapılmalı?
        
        Yanıtı profesyonel, maddeler halinde ve stratejik bir dille ver. HTML formatında (<b>, <br>, <ul> vb.) zenginleştirilmiş metin dön.
        """
        
        ai_response = await gemini_generate_content(prompt)
        return {"success": True, "analysis": ai_response}
        
    except Exception as e:
        logger.error(f"Triple compare error: {e}")
        return {"success": False, "error": str(e)}

# 2. Demografik ve İdeolojik Söylem Analizi (Manuel Tetiklemeli)
@app.post("/api/discourse-analyze", tags=["Yapay Zeka Analiz"])
async def discourse_analyze(db: AsyncSession = Depends(get_db)):
    """Demografik ve ideolojik söylem analizi yapar."""
    try:
        time_threshold = datetime.utcnow() - timedelta(hours=48)
        res = await db.execute(select(Content).where(Content.published_at >= time_threshold).limit(100))
        contents = res.scalars().all()
        
        text_blob = "\n".join([f"[{c.platform}] {c.text}" for c in contents])
        prompt = f"Aşağıdaki verileri demografik ve ideolojik açıdan analiz et. Hangi kitle neye tepki veriyor? Trend söylemler neler? \n\n{text_blob}"
        
        analysis = await gemini_generate_content(prompt)
        return {"success": True, "analysis": analysis}
    except Exception as e:
        return {"success": False, "error": str(e)}

# 2. Platform Bazlı Konu/Kelime Analizi (YouTube & RSS)
class PlatformAnalyzeRequest(BaseModel):
    platform_name: str  # 'youtube' veya 'rss'
    keyword: str

@app.post("/api/platform-analyze", tags=["Yapay Zeka Analiz"])
async def platform_analyze(req: PlatformAnalyzeRequest, db: AsyncSession = Depends(get_db)):
    """Platform bazlı konu/kelime analizi yapar."""
    try:
        res = await db.execute(select(Content).where(Content.platform == req.platform_name, Content.text.contains(req.keyword)).limit(50))
        contents = res.scalars().all()
        
        if not contents: return {"success": False, "error": "Veri bulunamadı."}
        
        text_blob = "\n".join([c.text for c in contents])
        prompt = f"'{req.keyword}' konusu hakkında {req.platform_name} platformundaki genel algıyı özetle. \n\n{text_blob}"
        
        analysis = await gemini_generate_content(prompt)
        return {"success": True, "analysis": analysis}
    except Exception as e:
        return {"success": False, "error": str(e)}




# 3. Lider vs Rakip Duygu Analizi (Gerçek Veriye Dayalı)
@app.post("/api/leader-comparison", tags=["Yapay Zeka Analiz"])
async def leader_comparison(db: AsyncSession = Depends(get_db)):
    """Lider vs Rakip duygu analizi yapar."""
    logger.error("leader_comparison endpoint'i gerçek veri analizi yapmıyor; statik sahte analiz kaldırıldı.")
    raise HTTPException(status_code=501, detail="Gerçek lider/rakip duygu analizi uygulanmamış. Sahte analiz döndürülmez.")

# 4. Gerçek Platform Hacim İstatistikleri (Volume Chart)
@app.get("/api/volume-stats", tags=["Dashboard Verileri"])
async def volume_stats(db: AsyncSession = Depends(get_db)):
    try:
        from sqlalchemy import func, cast, Integer, extract
        time_threshold = datetime.utcnow() - timedelta(hours=24)
        res = await db.execute(
            select(
                Content.platform,
                func.count(Content.id).label("cnt")
            ).where(Content.published_at >= time_threshold)
            .group_by(Content.platform)
        )
        rows = res.all()
        
        platform_counts = {}
        for row in rows:
            p = row[0]
            c = row[1]
            if p in ('youtube', 'youtube_comment'):
                platform_counts['youtube'] = platform_counts.get('youtube', 0) + c
            else:
                platform_counts[p] = platform_counts.get(p, 0) + c
        
        # Saatlik dağılım (son 24 saat, 6 dilim)
        hourly = {"twitter": [], "youtube": [], "rss": []}
        slot_labels = []
        now = datetime.utcnow()
        for i in range(6):
            slot_end = now - timedelta(hours=i * 4)
            slot_start = now - timedelta(hours=(i + 1) * 4)
            slot_labels.insert(0, slot_start.strftime("%H:%M"))
            
            slot_res = await db.execute(
                select(Content.platform, func.count(Content.id))
                .where(Content.published_at >= slot_start, Content.published_at < slot_end)
                .group_by(Content.platform)
            )
            slot_data = {r[0]: r[1] for r in slot_res.all()}
            hourly["twitter"].insert(0, slot_data.get("twitter", 0))
            hourly["youtube"].insert(0, slot_data.get("youtube", 0) + slot_data.get("youtube_comment", 0))
            hourly["rss"].insert(0, slot_data.get("rss", 0))
        
        return {
            "success": True,
            "totals": platform_counts,
            "labels": slot_labels,
            "series": hourly
        }
    except Exception as e: return {"success": False, "error": str(e)}


@app.get("/api/stream/recent", tags=["Dashboard Verileri"])
async def recent_content_stream(limit: int = 30, db: AsyncSession = Depends(get_db)):
    """AI analizini beklemeden son kaydedilen gerçek içerikleri stream panellerine döndürür."""
    safe_limit = max(1, min(limit, 100))
    res = await db.execute(
        select(Content)
        .where(Content.platform.in_(["twitter", "x", "youtube", "youtube_comment", "rss"]))
        .order_by(desc(Content.fetched_at), desc(Content.published_at))
        .limit(safe_limit * 3)
    )
    rows = res.scalars().all()

    grouped = {"twitter": [], "youtube": [], "rss": []}
    for item in rows:
        platform = item.platform or ""
        key = "youtube" if platform in {"youtube", "youtube_comment"} else "twitter" if platform in {"twitter", "x"} else "rss"
        if len(grouped[key]) >= safe_limit:
            continue
        grouped[key].append({
            "id": str(item.id),
            "platform": platform,
            "author_name": item.author_name or "Unknown",
            "text": item.text or "",
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "fetched_at": item.fetched_at.isoformat() if item.fetched_at else None,
            "url": item.url,
            "is_analyzed": bool(item.is_analyzed),
        })

    return {"success": True, "streams": grouped}

# =======================================================
# 5. ŞİKAYET RADARI (Complaints Radar — YENİ MODÜL)
# =======================================================
class ComplaintRadarRequest(BaseModel):
    city: Optional[str] = None

@app.post("/api/complaints-radar", tags=["Yapay Zeka Analiz"])
async def complaints_radar(req: ComplaintRadarRequest, db: AsyncSession = Depends(get_db)):
    """Şehir bazlı şikayet ve zafiyet analizi yapar."""
    try:
        # Şehir bazlı verileri çek (X, YouTube, RSS fark etmeksizin)
        query = select(Content)
        if req.city:
            query = query.where(Content.text.ilike(f"%{req.city}%"))
        
        res = await db.execute(query.limit(50))
        contents = res.scalars().all()
        
        city_name = req.city if req.city else "Genel Türkiye"
        
        if not contents:
            logger.error(f"Complaints radar için gerçek veri bulunamadı: {city_name}")
            raise HTTPException(status_code=404, detail=f"{city_name} için canlı veri bulunamadı. Sahte öngörü raporu üretilmez.")
        else:
            text_blob = "\n".join([f"[{c.platform}] {c.text[:300]}" for c in contents])
            prompt = f"""
            Aşağıdaki canlı verileri kullanarak {city_name} için 'Şikayet ve Zafiyet Analizi' yap.
            Hangi konular (Ulaşım, Ekonomi, Altyapı vb.) öne çıkıyor? Rakip aktörler nerede hata yapıyor?
            
            CANLI VERİLER:
            {text_blob}
            
            Lütfen 3 ana başlıkta topla:
            1. **En Çok Şikayet Edilen Konu**
            2. **Rakip Zafiyet Noktası** (Nereye saldırılmalı?)
            3. **Saha Kriz Skoru** (0-100)
            """
        
        analysis = await gemini_generate_content(prompt)
        return {"success": True, "analysis": analysis}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Complaints radar error: {e}")
        return {"success": False, "error": str(e)}


# =======================================================
# 6. DERİN ARAŞTIRMA MİMARİSİ (Deep Research — YENİ)
# =======================================================
class DeepResearchRequest(BaseModel):
    keyword: str


DEEP_RESEARCH_MULTIPLIER = 5
DEEP_X_LIMIT = 100 * DEEP_RESEARCH_MULTIPLIER
DEEP_X_REPLY_THREADS = 5 * DEEP_RESEARCH_MULTIPLIER
DEEP_RSS_MAX_ITEMS = 200 * DEEP_RESEARCH_MULTIPLIER
DEEP_YOUTUBE_VIDEO_LIMIT = 100 * DEEP_RESEARCH_MULTIPLIER
DEEP_YOUTUBE_COMMENT_LIMIT = 50 * DEEP_RESEARCH_MULTIPLIER
DEEP_YOUTUBE_COMMENT_VIDEO_LIMIT = 10 * DEEP_RESEARCH_MULTIPLIER

@app.post("/api/deep-research", tags=["Veri Çekme"])
async def deep_research(req: DeepResearchRequest, db: AsyncSession = Depends(get_db)):
    """Anahtar kelime için YouTube, X ve RSS üzerinde 5x derin tarama yapar."""
    keyword = (req.keyword or "").strip()
    if not keyword:
        return {"success": False, "error": "Anahtar kelime zorunludur."}

    from urllib.parse import quote_plus
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from app.providers.rss_provider import RSSProvider
    from app.providers.youtube_provider import YouTubeProvider, YouTubeQuotaExceeded
    from app.workers.ingest_tasks import get_x_provider, _post_to_article

    stats = {"youtube": 0, "rss": 0, "twitter": 0}
    added = {"youtube": 0, "rss": 0, "twitter": 0}
    skipped = {"youtube": 0, "rss": 0, "twitter": 0}
    errors = []

    async def insert_article(article: dict, bucket: str):
        article["is_analyzed"] = True
        try:
            stmt = pg_insert(Content).values(**article).on_conflict_do_nothing(index_elements=["external_id"])
            res = await db.execute(stmt)
            await db.commit()
            if res.rowcount > 0:
                added[bucket] += 1
            else:
                skipped[bucket] += 1
        except Exception as e:
            await db.rollback()
            skipped[bucket] += 1
            logger.warning(f"Deep Research kayıt hatası [{bucket}]: {e}")

    RESEARCH_PROGRESS[keyword] = {"status": "RSS kaynakları 5x derinlikte taranıyor...", "percent": 10}

    rss_provider = RSSProvider()
    rss_sources = [{
        "name": f"Google News Search: {keyword}",
        "url": f"https://news.google.com/rss/search?q={quote_plus(keyword)}&hl=tr&gl=TR&ceid=TR:tr",
        "id": None,
        "domain": "general",
        "filter": False,
    }]
    try:
        res = await db.execute(select(Source).where(Source.type == "rss", Source.active == True))
        for source in res.scalars().all():
            rss_sources.append({
                "name": source.name,
                "url": source.url,
                "id": source.id,
                "domain": getattr(source, "domain", "general") or "general",
                "filter": True,
            })
    except Exception as e:
        await db.rollback()
        errors.append(f"RSS kaynak listesi: {e}")

    for source in rss_sources:
        try:
            articles = await rss_provider.fetch_feed(source["url"], source["name"], max_items=DEEP_RSS_MAX_ITEMS)
            for article in articles:
                if source["filter"] and keyword.lower() not in (article.get("text") or "").lower():
                    continue
                article["source_id"] = source["id"]
                article["domain"] = source["domain"]
                stats["rss"] += 1
                await insert_article(article, "rss")
        except Exception as e:
            errors.append(f"RSS {source['name']}: {e}")

    RESEARCH_PROGRESS[keyword] = {"status": "YouTube videoları ve yorumları 5x derinlikte taranıyor...", "percent": 35}
    try:
        yt_provider = YouTubeProvider()
        videos = await yt_provider.fetch_keyword_videos(keyword, max_results=DEEP_YOUTUBE_VIDEO_LIMIT)
        for video in videos[:DEEP_YOUTUBE_COMMENT_VIDEO_LIMIT]:
            vid_id = video["id"] if isinstance(video.get("id"), str) else video["id"]["videoId"]
            comments = await yt_provider.fetch_video_comments(vid_id, max_results=DEEP_YOUTUBE_COMMENT_LIMIT)
            articles = extract_youtube_comments_as_articles(video, comments, None, "general")
            stats["youtube"] += len(articles)
            for article in articles:
                await insert_article(article, "youtube")
    except YouTubeQuotaExceeded as e:
        logger.error(f"Deep Research: YouTube kotası doldu, RSS/X akışı devam ediyor: {e}")
        errors.append("YouTube Kotası Doldu, diğer kaynaklara geçiliyor.")
    except Exception as e:
        errors.append(f"YouTube: {e}")

    RESEARCH_PROGRESS[keyword] = {"status": "X gönderileri ve reply threadleri 5x derinlikte taranıyor...", "percent": 65}
    try:
        x_provider = get_x_provider()
        posts = await x_provider.fetch_keyword_posts(keyword, limit=DEEP_X_LIMIT)
        seen_ids = {p.get("external_id") for p in posts}
        reply_threads = 0
        for post in list(posts):
            if reply_threads >= DEEP_X_REPLY_THREADS:
                break
            if post.get("_replies", 0) <= 0:
                continue
            if reply_threads > 0:
                await asyncio.sleep(getattr(x_provider, "API_DELAY", 3.0))
            replies = await x_provider.fetch_tweet_replies(post["external_id"])
            reply_threads += 1
            for reply in replies:
                if reply.get("external_id") not in seen_ids:
                    reply["target_type"] = "twitter_reply"
                    reply["target_name"] = keyword
                    posts.append(reply)
                    seen_ids.add(reply.get("external_id"))

        for post in posts:
            stats["twitter"] += 1
            article = _post_to_article(post, source_id=None, domain="general")
            await insert_article(article, "twitter")
    except Exception as e:
        errors.append(f"X/Twitter: {e}")

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        errors.append(f"DB commit: {e}")

    try:
        from app.workers.ingest_tasks import _trigger_analysis_chain
        _trigger_analysis_chain("Deep Research")
    except Exception as e:
        logger.warning(f"Deep Research analiz kuyruğu tetiklenemedi: {e}")

    RESEARCH_PROGRESS[keyword] = {"status": "Derin araştırma tamamlandı, AI analiz kuyruğu tetiklendi.", "percent": 100}
    analysis = (
        f"**Derin Tarama Özeti**\n"
        f"{keyword} için 5x derinlikte tarama yapıldı. "
        f"RSS {stats['rss']}, YouTube {stats['youtube']} yorum, X/Twitter {stats['twitter']} gönderi/reply işlendi.\n\n"
        f"**Kayıt Durumu**\n"
        f"Yeni kayıt: {sum(added.values())}, mevcut/atlanan: {sum(skipped.values())}. "
        f"AI etiketleme kuyruğu tetiklendi; bot ve dijital ayak izi göstergeleri analiz sonrası OSINT paneline yansır."
    )
    if errors:
        analysis += f"\n\n**Uyarılar**\n" + "\n".join(f"- {e}" for e in errors[:5])

    return {
        "success": True,
        "message": f"'{keyword}' için 5x derin araştırma tamamlandı.",
        "stats": stats,
        "added": added,
        "skipped": skipped,
        "analysis": analysis,
    }


@app.get("/api/deep-research/status/{keyword}", tags=["Veri Çekme"])
async def get_deep_research_status(keyword: str):
    """Araştırmanın o anki durumunu döner."""
    return RESEARCH_PROGRESS.get(keyword, {"status": "Beklemede...", "percent": 0})

# =======================================================
# SEÇİM RADARI MİMARİSİ (KUSURSUZ TEK KOPYA)
# =======================================================
@app.get("/api/districts", tags=["Seçim Veritabanı"])
async def get_districts(province: str, db: AsyncSession = Depends(get_db)):
    try:
        # Önce district_stats.json dosyasından oku
        import os, json
        data_dir = os.path.join(os.getcwd(), "app", "data")
        demo_file = os.path.join(data_dir, "district_stats.json")
        districts = []
        if os.path.exists(demo_file):
            with open(demo_file, "r", encoding="utf-8-sig") as f:
                demo_data = json.load(f)
                if isinstance(demo_data, list):
                    for item in demo_data:
                        if isinstance(item, dict) and item.get("province", "").upper() == province.upper():
                            dist_name = item.get("district")
                            if dist_name: districts.append(dist_name)
        if districts:
            return {"districts": sorted(list(set(districts)))}
            
        # Eğer JSON'da yoksa veritabanından (DistrictDemographics) bak (Fallback)
        res = await db.execute(select(DistrictDemographics).where(DistrictDemographics.province == province))
        db_districts = res.scalars().all()
        # Modelde kolon district olarak tanımlı
        return {"districts": [d.district for d in db_districts if hasattr(d, 'district') and d.district]}
    except Exception as e: 
        logger.error(f"İlçe çekme hatası: {e}")
        return {"districts": []}

class PollRadarRequest(BaseModel):
    province: str; district: str = ""; party_a: float; party_b: float; party_c: float; party_d: float

@app.post("/api/election/poll-radar", tags=["Seçim Veritabanı"])
async def poll_radar(req: PollRadarRequest):
    """Anket sonuçlarını analiz eder ve simülasyon yapar."""
    try:
        prompt = f"""{req.province} {req.district} bölgesi için şu anki anket sonuçlarını değerlendir:
        Parti A: %{req.party_a}, Parti B: %{req.party_b}, Parti C: %{req.party_c}, Diğer: %{req.party_d}
        Bu sonuçlara göre bölgedeki siyasi dengeleri ve olası senaryoları özetle."""
        
        analysis = await gemini_generate_content(prompt)
        return {"success": True, "analysis": analysis}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/election/analyze", tags=["Seçim Veritabanı"])
async def analyze_election(province: str, election_type: str, district: str = "", force_refresh: bool = False, db: AsyncSession = Depends(get_db)):
    """Seçim verilerini analiz eder."""
    try:
        # Önce Cache kontrolü
        cache_query = select(RegionAnalysis).where(RegionAnalysis.province == province, RegionAnalysis.election_year == 2024)
        if district: cache_query = cache_query.where(RegionAnalysis.district == district)
        else: cache_query = cache_query.where((RegionAnalysis.district == None) | (RegionAnalysis.district == ""))
            
        cache_result = await db.execute(cache_query.order_by(desc(RegionAnalysis.last_analyzed_at)).limit(1))
        cached_data = cache_result.scalar_one_or_none()
        
        if not force_refresh and cached_data and cached_data.ai_summary:
            return {"success": True, "analysis": cached_data.ai_summary, "cached": True}

        result_query = select(ElectionResult).where(ElectionResult.province == province)
        if district:
            result_query = result_query.where(ElectionResult.district == district)
        result_query = result_query.limit(50)
        election_rows = (await db.execute(result_query)).scalars().all()
        if not election_rows:
            logger.error(f"Election analyze için gerçek seçim verisi bulunamadı: {province} {district}")
            raise HTTPException(status_code=404, detail="Gerçek seçim verisi bulunamadı. Sahte analiz üretilmez.")

        real_rows = [
            {
                "year": row.election_year,
                "type": str(row.election_type),
                "province": row.province,
                "district": row.district,
                "party": row.party,
                "vote_count": row.vote_count,
            }
            for row in election_rows
        ]
        prompt = f"{province} {district} bölgesi için gerçek seçim verilerini analiz et.\n\nVERİ:\n{json.dumps(real_rows, ensure_ascii=False)}"
        analysis = await gemini_generate_content(prompt)
        
        # Cache'e kaydet (Basitleştirilmiş)
        if cached_data:
            cached_data.ai_summary = analysis
            cached_data.last_analyzed_at = datetime.utcnow()
        else:
            new_cache = RegionAnalysis(
                province=province, district=district, election_year=2024,
                ai_summary=analysis, last_analyzed_at=datetime.utcnow()
            )
            db.add(new_cache)
        
        await db.commit()
        return {"success": True, "analysis": analysis, "cached": False}
    except HTTPException:
        raise
    except Exception as e: 
        logger.error(f"Election analyze error: {e}")
        return {"success": False, "error": str(e)}

# =======================================================
# WIKIPEDIA BOTU (ANKET KAZIMA)
# =======================================================
@app.get("/api/election/scrape-poll", tags=["Veri Kazıma"])
async def scrape_latest_poll():
    url = "https://tr.wikipedia.org/wiki/Bir_sonraki_T%C3%BCrkiye_genel_se%C3%A7imleri_i%C3%A7in_yap%C4%B1lan_anketler"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        # Virgülleri ondalık, noktaları binlik ayırıcı olarak okuyarak Pandas'ın sayıları yanlış (10 veya 100 kat büyük) çevirmesini engeller
        tables = pd.read_html(io.StringIO(r.text), decimal=',', thousands='.')
        
        df = None
        for table in tables:
            if isinstance(table.columns, pd.MultiIndex):
                table.columns = ['_'.join(map(str, col)).strip() for col in table.columns.values]
            cols_upper = [str(c).upper() for c in table.columns]
            if any('AK PART' in c or 'AKP' in c for c in cols_upper) and any('CHP' in c for c in cols_upper):
                df = table; break
                
        if df is None: return {"status": "error", "message": "Tablo bulunamadı."}

        def find_col(keywords):
            for col in df.columns:
                for kw in keywords:
                    if kw.upper() in str(col).upper(): return col
            return None

        col_akp = find_col(['AK PART', 'AKP', 'ADALET VE KALKINMA'])
        col_chp = find_col(['CHP', 'CUMHURİYET HALK'])
        col_mhp = find_col(['MHP', 'MİLLİYETÇİ HAREKET'])
        col_yrp = find_col(['YRP', 'YENİDEN REFAH'])
        col_iyi = find_col(['İYİ PART', 'İYİ', 'IYI'])
        col_dem = find_col(['DEM PART', 'HDP', 'DEM', 'YEŞİL SOL'])
        col_zaf = find_col(['ZAFER PART', 'ZAFER'])
        col_firma = find_col(['ŞİRKET', 'FİRMA', 'ANKET'])
        col_tarih = find_col(['TARİH', 'ZAMAN'])

        def clean_val(val):
            if pd.isna(val) or str(val).strip() in ['-', '—', '']: return 0.0
            try: return float(str(val).split('[')[0].replace(',', '.').replace('%', '').strip())
            except: return 0.0

        latest_poll = None
        for i in range(len(df)-1, -1, -1):
            row = df.iloc[i]
            if "Ortalama" in str(row[col_firma] if col_firma else ""): continue
            if (clean_val(row[col_akp]) if col_akp else 0) > 0:
                latest_poll = row; break

        if latest_poll is None: return {"status": "error", "message": "Geçerli rakam bulunamadı."}

        iktidar = (clean_val(latest_poll[col_akp]) if col_akp else 0) + (clean_val(latest_poll[col_mhp]) if col_mhp else 0) + (clean_val(latest_poll[col_yrp]) if col_yrp else 0)
        muhalefet = (clean_val(latest_poll[col_chp]) if col_chp else 0) + (clean_val(latest_poll[col_iyi]) if col_iyi else 0)
        tepki = (clean_val(latest_poll[col_dem]) if col_dem else 0) + (clean_val(latest_poll[col_zaf]) if col_zaf else 0)
        
        # Eğer okunan veriler yüzdelik değil de ham katılımcı sayısıysa (toplamı 100'den çok büyükse)
        # Bütün parti kolonlarını toplayıp orantılayarak gerçek yüzdeleri bulalım.
        ignore_cols = []
        for col in df.columns:
            c_str = str(col).upper()
            if any(x in c_str for x in ['ŞİRKET', 'FİRMA', 'ANKET', 'TARİH', 'ZAMAN', 'ÖRNEKLEM', 'KATILIMCI', 'FARK']):
                ignore_cols.append(col)
                
        total_votes = 0.0
        for col in df.columns:
            if col not in ignore_cols:
                total_votes += clean_val(latest_poll[col])
                
        if total_votes > 150: # Veri açıkça oran değil, ham sayıysa
            iktidar = (iktidar / total_votes) * 100
            muhalefet = (muhalefet / total_votes) * 100
            tepki = (tepki / total_votes) * 100

        return {
            "status": "success",
            "firma": str(latest_poll[col_firma] if col_firma else "Bilinmeyen").split('[')[0],
            "tarih": str(latest_poll[col_tarih] if col_tarih else "Yakın Zaman").split('[')[0],
            "data": {"a": round(iktidar, 1), "b": round(muhalefet, 1), "c": round(tepki, 1), "d": round(max(0, 100 - (iktidar+muhalefet+tepki)), 1)}
        }
    except Exception as e: return {"status": "error", "message": f"Bot Hatası: {str(e)}"}

# =======================================================
# OSINT ISTIHBARAT ANALIZI (Intelligence Insights — YENİ)
# =======================================================
@app.get("/api/osint/intelligence", tags=["OSINT"])
async def get_osint_intelligence(window: int = 48, db: AsyncSession = Depends(get_db)):
    """AI etiketleri ve Twitter hesap metriklerinden OSINT göstergeleri üretir."""
    try:
        from app.models.core import ContentLabel, Content
        
        time_threshold = datetime.utcnow() - timedelta(hours=window)
        
        query = select(
            func.avg(ContentLabel.sentiment_score).label("avg_sentiment"),
            func.avg(ContentLabel.manipulation_prob).label("avg_manipulation"),
            func.avg(ContentLabel.bot_likelihood).label("avg_bot"),
            func.count(ContentLabel.content_id).label("total_labeled")
        ).join(Content, Content.id == ContentLabel.content_id).where(Content.published_at >= time_threshold)
        
        res = await db.execute(query)
        stats = res.one()
        
        sarcasm_query = select(func.count(ContentLabel.content_id)).where(ContentLabel.sarcasm_detected == True).join(Content, Content.id == ContentLabel.content_id).where(Content.published_at >= time_threshold)
        sarcasm_count = (await db.execute(sarcasm_query)).scalar() or 0

        twitter_res = await db.execute(
            select(Content.raw_json)
            .where(Content.platform == "twitter", Content.published_at >= time_threshold)
            .limit(1000)
        )
        twitter_raw_items = [row[0] for row in twitter_res.all() if isinstance(row[0], dict)]
        bot_signals = [
            twitter_bot_signal_summary(raw)
            for raw in twitter_raw_items
            if raw.get("account_metrics")
        ]
        metadata_bot_avg = (
            sum(item["score"] for item in bot_signals) / len(bot_signals)
            if bot_signals else 0.0
        )
        label_bot_avg = float(stats.avg_bot or 0)
        combined_bot = max(label_bot_avg, metadata_bot_avg)
        avg_ratio = sum(item["ratio"] for item in bot_signals) / len(bot_signals) if bot_signals else 0.0
        avg_tweets_per_day = sum(item["tweets_per_day"] for item in bot_signals) / len(bot_signals) if bot_signals else 0.0
        
        return {
            "success": True,
            "sentiment": round(float(stats.avg_sentiment or 0) * 100, 1),
            "manipulation": round(float(stats.avg_manipulation or 0) * 100, 1),
            "bot_likelihood": round(combined_bot * 100, 1),
            "bot_likelihood_ai": round(label_bot_avg * 100, 1),
            "bot_likelihood_metadata": round(metadata_bot_avg * 100, 1),
            "bot_accounts_sampled": len(bot_signals),
            "bot_follow_ratio_avg": round(avg_ratio, 2),
            "bot_tweets_per_day_avg": round(avg_tweets_per_day, 2),
            "sarcasm_rate": round((sarcasm_count / (stats.total_labeled or 1)) * 100, 1),
            "total_analyzed": stats.total_labeled
        }
    except Exception as e:
        logger.error(f"OSINT intelligence error: {e}")
        return {"success": False, "error": str(e)}

@app.get("/api/osint/entity-graph", tags=["OSINT"])
async def get_entity_graph(db: AsyncSession = Depends(get_db)):
    """Varlık (Entity) ilişkilerini Mermaid formatında döner."""
    try:
        from app.models.core import Entity, EntityRelation
        
        entities_res = await db.execute(select(Entity).limit(20))
        entities = entities_res.scalars().all()
        
        relations_res = await db.execute(select(EntityRelation).limit(50))
        relations = relations_res.scalars().all()
        
        if not entities or not relations:
            raise HTTPException(status_code=404, detail="Digital Footprint için gerçek entity/relation verisi bulunamadı.")
            
        mermaid = "graph LR\n"
        entity_map = {str(e.id).replace('-', ''): e.name for e in entities}
        
        for rel in relations:
            s_id = str(rel.source_entity_id).replace('-', '')
            t_id = str(rel.target_entity_id).replace('-', '')
            source = entity_map.get(s_id, "Bilinmiyor")
            target = entity_map.get(t_id, "Bilinmiyor")
            mermaid += f'  {s_id}["{source}"] -- "{rel.relation_type}" --> {t_id}["{target}"]\n'
            
        return {"success": True, "mermaid": mermaid}
    except HTTPException:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- Sistem Durumu ve İstatistikler ---
@app.get("/api/system/stats", tags=["Sistem"])
async def get_system_stats(db: AsyncSession = Depends(get_db)):
    try:
        from sqlalchemy import func
        total_content = await db.scalar(select(func.count(Content.id)))
        active_sources = await db.scalar(select(func.count(Source.id)).where(Source.active == True))
        
        last_24h = datetime.utcnow() - timedelta(hours=24)
        daily_content = await db.scalar(select(func.count(Content.id)).where(Content.published_at >= last_24h))
        
        # Platform bazlı dağılım
        p_res = await db.execute(select(Content.platform, func.count(Content.id)).group_by(Content.platform))
        platform_stats = {p: count for p, count in p_res.all()}
        
        # Son loglar (Son 10 içerik)
        log_res = await db.execute(select(Content).order_by(desc(Content.fetched_at)).limit(10))
        recent_logs = []
        for c in log_res.scalars().all():
            recent_logs.append({
                "id": str(c.id),
                "platform": c.platform,
                "author": c.author_name,
                "text": c.text[:100] + "...",
                "time": c.fetched_at.strftime('%H:%M:%S')
            })
            
        return {
            "success": True,
            "total_content": total_content or 0,
            "active_sources": active_sources or 0,
            "daily_content": daily_content or 0,
            "platforms": platform_stats,
            "recent_logs": recent_logs
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/system/trigger-pipeline", tags=["Sistem"], status_code=202)
async def trigger_pipeline():
    """Yapay Zeka Etiketleme ve Fırsat Kartı Üretimini Tetikler."""
    try:
        from celery import chain
        from app.workers.ingest_tasks import (
            clean_and_triage_recent_content,
            run_osint_bot_stage,
            synthesize_ai_opportunities,
            publish_stream_update,
        )
        from app.workers.labeling_tasks import batch_analyze_contents

        pipeline = chain(
            clean_and_triage_recent_content.si("Manual Trigger"),
            run_osint_bot_stage.s(),
            batch_analyze_contents.si(),
            synthesize_ai_opportunities.si(),
            publish_stream_update.si("Manual Trigger"),
        )
        async_result = pipeline.apply_async(queue="default")

        return {
            "success": True,
            "message": "Görev Alındı: AI analiz pipeline arka planda sırayla çalışacak.",
            "task_id": async_result.id,
        }
    except Exception as e:
        logger.error(f"Trigger pipeline error: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    # Docker içinde uvicorn.run kullanılacaksa host mutlaka 0.0.0.0 olmalı
    logger.info("⚠️ Sunucu manuel olarak başlatılıyor...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)