import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.models.core import ComplaintsRadarCache
from app.services.gemini_service import GeminiAIClient
from app.workers.ingest_tasks import get_x_provider, run_async


def _province_key(label: str) -> str:
    """main.normalize_tr ile aynı mantık — döngüsel import yok."""
    if not (label or "").strip():
        return "turkiye_geneli"
    t = str(label).strip()
    t = (
        t.replace("İ", "i")
        .replace("I", "ı")
        .replace("Ş", "ş")
        .replace("Ğ", "ğ")
        .replace("Ü", "ü")
        .replace("Ö", "ö")
        .replace("Ç", "ç")
        .lower()
    )
    return t.strip() or "turkiye_geneli"


def _complaint_queries_for_city(city_label: str) -> list[str]:
    c = (city_label or "").strip()
    if not c:
        c = "Türkiye"
    return [
        f"{c} su kesintisi",
        f"{c} elektrik isyanı",
        f"{c} yol kapama",
        f"{c} asayiş olayları",
        f"{c} tepki",
    ]


def _crisis_filter_prompt(city_label: str, raw_lines: str) -> str:
    return f"""Sen bir kriz masası analistisin. Aşağıdaki satırlar X (Twitter) aramasından örneklenmiş kısa gönderilerdir.

GÖREV:
- Boş magazin, dedikodu veya salt genel siyaset gündemini ele.
- Yalnızca halkın doğrudan yaşam kalitesini bozan (su/elektrik/ulaşım kesintisi, yol kapanması, güvenlik ve asayiş,
  ekonomik tepki ve yerelde örgütlenmiş rahatsızlık vb.) gerçek şikâyet veya saha huzursuzluğu sinyallerini özetle.
- İçerik yalnızca genel haber metni veya kampanya gürültüsüyse raporda yer verme; “belirsiz / genel gürültü” diye not düş.

ŞEHİR / BÖLGE BAĞLAMI: {city_label}

KİMLİK VE ÜSLUP:
- Kendini yapay zeka, dil modeli veya harici bir yardımcı olarak tanıtma; nötr istihbarat brifingi yaz.
- Türkçe, maddeler halinde veya kısa paragraflar halinde çıktı üret.

HAM SATIRLAR:
{raw_lines}
"""


async def _complaints_radar_async(province_label: str) -> str:
    label = (province_label or "").strip() or "Türkiye Geneli"
    key = _province_key(label)
    city_token = label if label != "Türkiye Geneli" else "Türkiye"
    queries = _complaint_queries_for_city(city_token)

    provider = get_x_provider()
    merged: list = []
    seen: set = set()

    for q in queries:
        try:
            chunk = await provider.fetch_keyword_posts(q, limit=12)
        except Exception as e:
            logger.warning(f"Şikayet radarı arama atlandı ({q}): {e}")
            chunk = []
        for p in chunk or []:
            eid = p.get("external_id")
            if eid and eid not in seen:
                seen.add(eid)
                merged.append(p)
        await asyncio.sleep(provider.API_DELAY)

    lines: list[str] = []
    for p in merged[:55]:
        author = p.get("author") or "?"
        txt = (p.get("text") or "").replace("\n", " ").strip()
        lines.append(f"- @{author}: {txt[:280]}")
    raw_blob = "\n".join(lines) if lines else "(Örnek çekilemedi — boş liste)"

    client = GeminiAIClient()
    prompt = _crisis_filter_prompt(label, raw_blob)
    analysis = await client.generate_content_async(prompt)

    payload = {
        "analysis": analysis,
        "sample_count": len(merged),
        "queries": queries,
    }

    async with AsyncSessionLocal() as db:
        stmt = (
            pg_insert(ComplaintsRadarCache)
            .values(
                province_key=key,
                province_label=label,
                cached_at=datetime.utcnow(),
                payload_json=payload,
            )
            .on_conflict_do_update(
                index_elements=["province_key"],
                set_={
                    "province_label": label,
                    "cached_at": datetime.utcnow(),
                    "payload_json": payload,
                },
            )
        )
        await db.execute(stmt)
        await db.commit()

    logger.info(f"✅ Şikayet radarı tamamlandı: {label} ({len(merged)} örnek)")
    return analysis


@celery_app.task(
    name="complaints_radar_scan",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=120,
    retry_backoff_max=600,
    max_retries=2,
    acks_late=True,
)
def complaints_radar_scan(self, province_label: str):
    """İl (veya Türkiye geneli) için X örneklemesi + Gemini kriz süzgeci; sonucu complaints_radar_cache yazar."""
    return run_async(_complaints_radar_async(province_label))
