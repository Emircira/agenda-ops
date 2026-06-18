"""
Karargah makro ve toplum nabzı boru hattı.

- fetch_real_macro_data: Açık web / (isteğe bağlı) EVDS ile makro rakamlar.
- fetch_grievances_and_demands: Veritabanı içerikleri + Gemini sentezi.
- fetch_tcmb_data / fetch_polling_data: Legacy mock (manuel tetik için duruyor).
"""

from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
from loguru import logger

from app.core.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.repositories.content_repository import ContentRepository
from app.repositories.macro_repository import MacroRepository
from app.services.gemini_service import GeminiAIClient


def _run_async(coro):
    return asyncio.run(coro)


HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KarargahMacro/1.1; +https://localhost) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}


def _float_matches_stored(stored: Optional[float], new_val: float, tol: float = 1e-4) -> bool:
    if stored is None:
        return False
    return abs(float(stored) - float(new_val)) <= tol


def _normalized_poll_map(d: Any) -> dict[str, float]:
    if not isinstance(d, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in d.items():
        try:
            out[str(k)] = round(float(v), 4)
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items()))


def _poll_results_unchanged(stored: Any, new_map: dict[str, float]) -> bool:
    return _normalized_poll_map(stored) == _normalized_poll_map(new_map)


def _parse_tr_float(s: str) -> Optional[float]:
    try:
        return float(str(s).strip().replace("%", "").replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _first_pct_in_window(text: str, low: float, high: float) -> Optional[float]:
    """Metin içinde aralığa uyan ilk yüzdeyi bul."""
    if not text:
        return None
    for m in re.finditer(r"(?:%\s*)?(\d{1,3}(?:[.,]\d+)?)\s*%", text):
        val = _parse_tr_float(m.group(1))
        if val is not None and low <= val <= high:
            return val
    return None


def _pct_after_keywords(
    haystack: str,
    keywords: tuple[str, ...],
    low: float,
    high: float,
    window: int = 320,
) -> Optional[float]:
    lower = haystack.lower()
    for kw in keywords:
        pos = lower.find(kw.lower())
        if pos < 0:
            continue
        chunk = haystack[pos : pos + window]
        hit = _first_pct_in_window(chunk, low, high)
        if hit is not None:
            return hit
    return None


def _evds_last_value(series: str, days_back: int = 120) -> Optional[float]:
    """EVDS — isteğe bağlı EVDS_API_KEY ile; anahtar yoksa sessizce None."""
    key = (os.getenv("EVDS_API_KEY") or "").strip()
    if not key:
        return None
    end = datetime.utcnow().date()
    start = end - timedelta(days=days_back)
    url = (
        "https://evds2.tcmb.gov.tr/service/evds/"
        f"?series={series}&startDate={start.strftime('%d-%m-%Y')}"
        f"&endDate={end.strftime('%d-%m-%Y')}&type=json&frequency=1&key={key}"
    )
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=35)
        if r.status_code != 200:
            return None
        payload = r.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        for row in reversed(items):
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                if k == "UNIXTIME" or k == "Tarih":
                    continue
                if v in (None, ""):
                    continue
                val = _parse_tr_float(str(v))
                if val is not None:
                    return val
        return None
    except Exception as e:
        logger.debug(f"EVDS ({series}) okunamadı: {e}")
        return None


def _fetch_policy_rate_deterministic() -> Optional[float]:
    """Önce EVDS (anahtarlı), sonra TCMB Temel Faiz sayfası HTML."""
    for s in ("TP.KTF17", "TP.KTF14"):
        v = _evds_last_value(s)
        if v is not None and 5.0 <= v <= 70.0:
            return round(v, 2)

    urls = (
        "https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faiz+Oranlari",
        "https://www.tcmb.gov.tr/wps/wcm/connect/tr/tcmb+tr/main+menu/temel+faiz+oranlari",
    )
    for url in urls:
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=28)
            if r.status_code != 200 or not r.text:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            plain = soup.get_text("\n", strip=True)
            hit = _pct_after_keywords(
                plain,
                (
                    "politika faizi",
                    "haftalık repo",
                    "bir hafta vadeli",
                    "policy rate",
                    "one week repo",
                ),
                5.0,
                70.0,
            )
            if hit is not None:
                return round(hit, 2)
            hit = _first_pct_in_window(plain, 15.0, 70.0)
            if hit is not None:
                return round(hit, 2)
        except Exception as e:
            logger.debug(f"TCMB faiz sayfası ({url}): {e}")
    return None


def _fetch_inflation_tuik_deterministic() -> Optional[float]:
    """TÜFE yıllık enflasyon (yaklaşık 1–120 %) — EVDS + TCMB/TÜİK çevre metin."""
    for s in ("TP.FE.OKTG01", "TP.FG.UFE1Y"):
        v = _evds_last_value(s, days_back=400)
        if v is not None and 1.0 <= v <= 200.0:
            return round(v, 2)

    tuik_urls = (
        "https://www.tuik.gov.tr/",
        "https://data.tuik.gov.tr/",
    )
    for url in tuik_urls:
        try:
            r = requests.get(url, headers=HTTP_HEADERS, timeout=28)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            plain = soup.get_text("\n", strip=True)
            hit = _pct_after_keywords(
                plain,
                ("tüfe", "tüketici fiyat", "yıllık enflasyon", "enflasyon oranı"),
                3.0,
                200.0,
            )
            if hit is not None:
                return round(hit, 2)
        except Exception as e:
            logger.debug(f"TÜİK kaynağı ({url}): {e}")
    return None


def _fetch_unemployment_tuik_deterministic() -> Optional[float]:
    """İşsizlik oranı (— genelde tek haneli/çift hane %)."""
    for s in ("TP.GS11",):
        v = _evds_last_value(s, days_back=800)
        if v is not None and 3.0 <= v <= 35.0:
            return round(v, 2)

    try:
        r = requests.get("https://www.tuik.gov.tr/", headers=HTTP_HEADERS, timeout=28)
        if r.status_code == 200 and r.text:
            soup = BeautifulSoup(r.text, "lxml")
            plain = soup.get_text("\n", strip=True)
            hit = _pct_after_keywords(
                plain,
                ("işsizlik", "işsizlik oranı"),
                3.0,
                35.0,
            )
            if hit is not None:
                return round(hit, 2)
    except Exception as e:
        logger.debug(f"TÜİK işsizlik taraması: {e}")
    return None


def _fetch_inflation_enag_optional() -> Optional[float]:
    """ENAG grup — özet enflasyon (bonus gösterge)."""
    for base in ("https://www.enagrup.org.tr/", "https://www.enag.org.tr/"):
        try:
            r = requests.get(base, headers=HTTP_HEADERS, timeout=28)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            plain = soup.get_text("\n", strip=True)
            hit = _pct_after_keywords(plain, ("enflasyon", "yıllık"), 10.0, 250.0)
            if hit is not None:
                return round(hit, 2)
            hit = _first_pct_in_window(plain, 30.0, 200.0)
            if hit is not None:
                return round(hit, 2)
        except Exception as e:
            logger.debug(f"ENAG okuma ({base}): {e}")
    return None


@celery_app.task(name="fetch_real_macro_data", bind=True)
def fetch_real_macro_data(self):
    """
    Gerçek / yarı-gerek makro rakamları toplar (deterministik + kazıyıcı).
    Tek tek try/except: kısmi başarı mümkün; tam çökme yok.
    """

    async def _job():
        now = datetime.utcnow()
        updates: list[tuple[str, float, str]] = []

        for label, fn in (
            ("interest_rate", _fetch_policy_rate_deterministic),
            ("inflation_tuik", _fetch_inflation_tuik_deterministic),
            ("unemployment", _fetch_unemployment_tuik_deterministic),
        ):
            try:
                val = fn()
                if val is not None:
                    src = "tcmb_web" if "faiz" in label or label == "interest_rate" else "aggregate_web"
                    if label == "inflation_tuik":
                        src = "tuik_tcmb_evds_or_web"
                    if label == "unemployment":
                        src = "tuik_evds_or_web"
                    updates.append((label, float(val), src))
                    logger.info(f"📈 Makro çekildi: {label}={val} ({src})")
            except Exception as e:
                logger.debug(f"Makro '{label}' kaynağı okunamadı: {e}")

        try:
            enag = _fetch_inflation_enag_optional()
            if enag is not None:
                updates.append(("inflation_enag", float(enag), "enag_web"))
                logger.info(f"📈 Makro çekildi: inflation_enag={enag} (enag_web)")
        except Exception as e:
            logger.debug(f"ENAG bonus atlandı: {e}")

        if not updates:
            logger.info("Yeni veri yok, mevcut veriler korunuyor.")
            return "macro_real:no_delta"

        async with AsyncSessionLocal() as db:
            repo = MacroRepository(db)
            pending: list[tuple[str, float, str]] = []
            pending_meta: list[tuple[str, float, Optional[float]]] = []
            for ind_type, val, src in updates:
                try:
                    prev_row = await repo.get_latest_indicator_for_type(ind_type)
                    prev_val = float(prev_row.value) if prev_row and prev_row.value is not None else None
                    if _float_matches_stored(prev_val, val):
                        continue
                    pending.append((ind_type, val, src))
                    pending_meta.append((ind_type, float(val), prev_val))
                except Exception as e:
                    logger.debug(f"Makro karşılaştırma/yazım hazırlığı ({ind_type}): {e}")

            if not pending:
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                return "macro_real:no_change"

            for ind_type, val, src in pending:
                try:
                    await repo.add_indicator(
                        indicator_type=ind_type,
                        value=val,
                        recorded_date=now,
                        source=src,
                        commit=False,
                    )
                except Exception as e:
                    logger.debug(f"DB yazımı atlandı ({ind_type}): {e}")
            try:
                await repo.commit()
            except Exception as e:
                await repo.rollback()
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                logger.debug(f"fetch_real_macro_data commit hatası: {e}")
                return "macro_real:commit_failed"

            try:
                from app.workers.analysis_tasks import check_for_anomalies_after_macro_writes

                await check_for_anomalies_after_macro_writes(db, pending_meta)
            except Exception as e:
                logger.debug(f"Makro anomali hook: {e}")

        return f"macro_real:ok:{len(pending)}"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.info("Yeni veri yok, mevcut veriler korunuyor.")
        logger.debug(f"fetch_real_macro_data: {e}")
        return f"macro_real:error:{e}"


def _normalize_share_dict(raw: dict[str, Any], limit: int = 3) -> dict[str, float]:
    tmp: dict[str, float] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        label = k.strip()[:96]
        if not label:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv < 0:
            continue
        tmp[label] = fv
    if not tmp:
        return {}
    items = sorted(tmp.items(), key=lambda x: -x[1])[:limit]
    total = sum(x[1] for x in items) or 1.0
    return {k: round(v * 100.0 / total, 1) for k, v in items}


@celery_app.task(name="fetch_grievances_and_demands", bind=True)
def fetch_grievances_and_demands(self):
    """Son 48 saat içeriklerinden Gemini ile şikâyet/talep sentezi — PollData."""

    async def _job():
        since = datetime.utcnow() - timedelta(hours=48)
        topic_live = "Canlı Toplum Nabzı"
        company = "Karargah İçgörü"

        async with AsyncSessionLocal() as db:
            content_repo = ContentRepository(db)
            try:
                rows = await content_repo.list_pulse_candidates_since(
                    since,
                    limit=100,
                    fetch_pool=280,
                )
            except Exception as e:
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                logger.debug(f"İçerik çekilemedi: {e}")
                return "pulse:db_error"

            if not rows:
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                return "pulse:no_content"

            chunks: list[str] = []
            for i, c in enumerate(rows, 1):
                txt = (getattr(c, "text", None) or "").replace("\r", " ").strip()
                if not txt:
                    continue
                platform = getattr(c, "platform", "") or "?"
                chunks.append(f"[{i} | {platform}] {txt[:1200]}")
            corpus = "\n\n".join(chunks)
            if len(corpus) < 80:
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                return "pulse:corpus_short"

        try:
            client = GeminiAIClient()
            result = client.analyze_grievances_demands_from_corpus(corpus)
        except Exception as e:
            logger.info("Yeni veri yok, mevcut veriler korunuyor.")
            logger.debug(f"Gemini pulse: {e}")
            return "pulse:gemini_error"

        if not result:
            logger.info("Yeni veri yok, mevcut veriler korunuyor.")
            return "pulse:gemini_empty"

        g_raw = result.get("grievances") or {}
        d_raw = result.get("demands") or {}
        grievances = _normalize_share_dict(g_raw if isinstance(g_raw, dict) else {}, 3)
        demands = _normalize_share_dict(d_raw if isinstance(d_raw, dict) else {}, 3)
        if len(grievances) < 1 or len(demands) < 1:
            logger.info("Yeni veri yok, mevcut veriler korunuyor.")
            return "pulse:parse_thin"

        today = date.today()
        async with AsyncSessionLocal() as db:
            repo = MacroRepository(db)
            try:
                last_g = await repo.get_latest_poll_row(
                    company=company, topic=topic_live, poll_type="grievances"
                )
                last_d = await repo.get_latest_poll_row(
                    company=company, topic=topic_live, poll_type="demands"
                )
                skip_g = last_g is not None and _poll_results_unchanged(last_g.results, grievances)
                skip_d = last_d is not None and _poll_results_unchanged(last_d.results, demands)
                if skip_g and skip_d:
                    logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                    return "pulse:no_change"

                if not skip_g:
                    await repo.add_poll(
                        company=company,
                        topic=topic_live,
                        results=grievances,
                        published_date=today,
                        poll_type="grievances",
                        commit=False,
                    )
                if not skip_d:
                    await repo.add_poll(
                        company=company,
                        topic=topic_live,
                        results=demands,
                        published_date=today,
                        poll_type="demands",
                        commit=False,
                    )
                await repo.commit()
            except Exception as e:
                await repo.rollback()
                logger.info("Yeni veri yok, mevcut veriler korunuyor.")
                logger.debug(f"PollData yazımı: {e}")
                return "pulse:db_write_failed"

            if not skip_g:
                try:
                    from app.workers.analysis_tasks import check_for_anomalies_after_grievance_write

                    await check_for_anomalies_after_grievance_write(
                        db,
                        company=company,
                        topic=topic_live,
                        latest_results=grievances,
                    )
                except Exception as ex:
                    logger.debug(f"Şikâyet anomali hook: {ex}")

        logger.info("✅ Karargah İçgörü: şikâyet/talep satırları güncellendi.")
        return "pulse:ok"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.info("Yeni veri yok, mevcut veriler korunuyor.")
        logger.debug(f"fetch_grievances_and_demands: {e}")
        return f"pulse:fatal:{e}"


# ─── Legacy mock görevler (elle / test için) ───


def _clamp_pct(d: dict[str, float], jitter: float = 0.0) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in d.items():
        x = max(0.0, float(v) + random.uniform(-jitter, jitter))
        out[k] = round(min(100.0, x), 1)
    total = sum(out.values()) or 1.0
    scale = 100.0 / total
    return {k: round(v * scale, 1) for k, v in out.items()}


@celery_app.task(name="fetch_tcmb_data", bind=True)
def fetch_tcmb_data(self):
    """Mock makro — gerçek hat için fetch_real_macro_data kullanın."""

    async def _job():
        async with AsyncSessionLocal() as db:
            repo = MacroRepository(db)
            now = datetime.utcnow()
            rows = [
                ("inflation_tuik", round(random.uniform(58.0, 68.0), 2), "mock_tcmb_pipeline"),
                ("inflation_enag", round(random.uniform(95.0, 115.0), 2), "mock_tcmb_pipeline"),
                ("unemployment", round(random.uniform(8.5, 10.5), 2), "mock_tcmb_pipeline"),
                ("interest_rate", round(random.choice([40.0, 42.5, 45.0, 47.5, 50.0]), 2), "mock_tcmb_pipeline"),
            ]
            for ind_type, val, src in rows:
                await repo.add_indicator(
                    indicator_type=ind_type,
                    value=float(val),
                    recorded_date=now,
                    source=src,
                    commit=False,
                )
            await repo.commit()
            logger.info(f"✅ fetch_tcmb_data: {len(rows)} mock makro satırı yazıldı.")
            return f"tcmb_mock:{len(rows)}"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.exception("fetch_tcmb_data")
        raise


@celery_app.task(name="fetch_polling_data", bind=True)
def fetch_polling_data(self):

    async def _job():
        async with AsyncSessionLocal() as db:
            repo = MacroRepository(db)
            pub = date.today() - timedelta(days=random.randint(0, 3))
            jitter = 1.2

            grievance_sets = [
                {
                    "Ekonomi ve enflasyon": 72.0,
                    "Göç / sığınmacı konusu": 11.0,
                    "Adalet ve güven": 9.0,
                    "Eğitim": 8.0,
                },
            ]
            demand_sets = [
                {
                    "Alım gücünün artırılması": 48.0,
                    "Kamu hizmeti kalitesi": 26.0,
                    "Güvenlik ve düzen": 16.0,
                    "Şeffaflık": 10.0,
                },
            ]
            polls: list[tuple[str, str, str, dict[str, float], date]] = [
                (
                    "Saha Örneklem A.Ş.",
                    "Türkiye'nin öne çıkan sorunları (mock)",
                    "grievances",
                    _clamp_pct(grievance_sets[0], jitter),
                    pub,
                ),
                (
                    "Karargah İç Görü",
                    "Halkın öncelikli talepleri (mock)",
                    "demands",
                    _clamp_pct(demand_sets[0], jitter),
                    pub - timedelta(days=1),
                ),
            ]
            for company, topic, ptype, results, pdate in polls:
                await repo.add_poll(
                    company=company,
                    topic=topic,
                    results=results,
                    published_date=pdate,
                    poll_type=ptype,
                    commit=False,
                )
            await repo.commit()
            return f"polling_mock:{len(polls)}"

    try:
        return _run_async(_job())
    except Exception as e:
        logger.exception("fetch_polling_data")
        raise


@celery_app.task(name="fetch_macro_pipeline_daily", bind=True)
def fetch_macro_pipeline_daily(self):
    """Eski birleşik mock zinciri — geri uyumluluk."""
    fetch_tcmb_data.delay()
    fetch_polling_data.delay()
    return "macro_pipeline_queued"
