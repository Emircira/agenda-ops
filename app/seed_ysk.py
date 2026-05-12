import asyncio
import copy
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import delete, select
from app.db.session import AsyncSessionLocal
from app.models.core import (
    ElectionResult,
    ElectionCategory,
    CandidateDemographic,
    ElectionDemographicStat,
    ElectionRegionTrend,
    ElectionRegionArchive,
)
from app.ysk_seed.mapping import resolve_election_scope, is_referendum_il_result_filename


def parse_number(val):
    if not val or str(val).strip() == "":
        return 0
    try:
        return int(str(val).replace(".", "").replace(",", "").strip())
    except ValueError:
        return 0


def _strip_key(k: str) -> str:
    return str(k).strip()


def il_adlari_from_row(row: dict) -> tuple[Optional[str], Optional[str]]:
    """(il, ilçe) — YSK JSON anahtar varyantları."""
    il = None
    ilce = None
    for k, v in row.items():
        ks = _strip_key(k).lower().replace("İ", "i").replace("I", "ı")
        if ks in ("il adı", "il adi", "il"):
            il = str(v).strip() if v is not None else ""
        if "ilçe" in ks or "ilce" in ks:
            ilce = str(v).strip() if v is not None else ""
    if not il:
        for cand in ("İl Adı", "Il Adı", "IL ADI"):
            if cand in row and row[cand]:
                il = str(row[cand]).strip()
                break
    return (il or None), (ilce or None)


def is_summary_il(il_name: str) -> bool:
    if not il_name:
        return True
    u = il_name.upper()
    if "TOPLAM" in u:
        return True
    if u.startswith("%") or "%" in il_name:
        return True
    return False


def norm_archive_key(il_adi: str, ilce_adi: Optional[str]) -> tuple[str, str]:
    prov = (il_adi or "").strip().upper()
    dk = (ilce_adi or "").strip().upper() if ilce_adi else ""
    return prov, dk


def ensure_buf(buf: dict, il_adi: str, ilce_adi: Optional[str]) -> tuple[str, str]:
    key = norm_archive_key(il_adi, ilce_adi)
    if key not in buf:
        buf[key] = {
            "results": {"parties": {}, "referendum": {}},
            "demographics": {},
            "sources": [],
            "winner_candidate": None,
        }
    return key


def buf_add_votes(buf: dict, il_adi: str, ilce_adi: Optional[str], party: str, votes: int, rel_source: str):
    if not party or votes <= 0:
        return
    key = ensure_buf(buf, il_adi, ilce_adi)
    parties = buf[key]["results"]["parties"]
    parties[party] = parties.get(party, 0) + votes
    if rel_source and rel_source not in buf[key]["sources"]:
        buf[key]["sources"].append(rel_source)


def buf_add_demo_scalar(nested: dict, party: str, bucket: str, val: int):
    if not party or not bucket or val <= 0:
        return
    byp = nested.setdefault(party, {})
    byp[bucket] = byp.get(bucket, 0) + val


def deep_merge_archive_results(a: dict, b: dict) -> dict:
    """a üzerinde b'yi birleştirir (oy sayıları toplanır)."""
    a = a or {}
    b = b or {}
    out = copy.deepcopy(a)
    for sub in ("parties", "referendum"):
        if sub not in out:
            out[sub] = {}
        bs = b.get(sub) or {}
        for pk, pv in bs.items():
            try:
                pv = int(pv)
            except (TypeError, ValueError):
                continue
            out[sub][pk] = out[sub].get(pk, 0) + pv
    return out


def deep_merge_demographics(a: dict, b: dict) -> dict:
    a = a or {}
    b = b or {}
    out = copy.deepcopy(a)

    for key in ("gender_distribution", "age_distribution", "candidate_education_by_party"):
        if key in b:
            src = b[key]
            if not isinstance(src, dict):
                continue
            tgt = out.setdefault(key, {})
            for party, buckets in src.items():
                if not isinstance(buckets, dict):
                    continue
                t_party = tgt.setdefault(party, {})
                for buck, cnt in buckets.items():
                    try:
                        cnt = int(cnt)
                    except (TypeError, ValueError):
                        continue
                    t_party[buck] = t_party.get(buck, 0) + cnt

    for list_key in ("voter_sex_ratio_rows", "education_sample_rows"):
        rows = b.get(list_key)
        if isinstance(rows, list) and rows:
            out.setdefault(list_key, []).extend(rows)

    return out


def recompute_winners_from_results(results: dict) -> tuple[Optional[int], Optional[str]]:
    parties = (results or {}).get("parties") or {}
    ref = (results or {}).get("referendum") or {}
    merged = {}
    merged.update(parties)
    for k, tv in ref.items():
        merged[k] = merged.get(k, 0) + int(tv) if isinstance(tv, (int, float)) else merged.get(k, 0)
    total = sum(int(v) for v in merged.values()) if merged else None
    if not merged:
        return total, None
    win = max(merged.items(), key=lambda x: x[1])[0]
    return total, win


async def flush_archive_buffer(
    db,
    election_year: int,
    category: ElectionCategory,
    detail_slug: str,
    buf: dict[tuple[str, str], dict],
):
    """Upsert: (yıl, tip, detail, il, ilçe) benzersiz; JSON alanları birleştirilir."""
    for (prov, dkey), payload in buf.items():
        stmt = select(ElectionRegionArchive).where(
            ElectionRegionArchive.election_year == election_year,
            ElectionRegionArchive.election_type == category,
            ElectionRegionArchive.election_detail == detail_slug,
            ElectionRegionArchive.province == prov,
            ElectionRegionArchive.district_key == dkey,
        )
        ex = (await db.execute(stmt)).scalar_one_or_none()

        new_results = payload.get("results") or {"parties": {}, "referendum": {}}
        new_demo = payload.get("demographics") or {}
        new_sources = list(payload.get("sources") or [])
        wc = payload.get("winner_candidate")

        if ex:
            ex.results_json = deep_merge_archive_results(ex.results_json or {}, new_results)
            ex.demographics_json = deep_merge_demographics(ex.demographics_json or {}, new_demo)
            merged_sources = list(ex.source_files_json or [])
            for s in new_sources:
                if s and s not in merged_sources:
                    merged_sources.append(s)
            ex.source_files_json = merged_sources
            tot, winner = recompute_winners_from_results(ex.results_json)
            ex.total_valid_votes = tot
            ex.winner_party = winner
            if wc:
                ex.winner_candidate = wc
        else:
            tot, winner = recompute_winners_from_results(new_results)
            db.add(
                ElectionRegionArchive(
                    election_year=election_year,
                    election_type=category,
                    election_detail=detail_slug,
                    province=prov,
                    district_key=dkey,
                    total_valid_votes=tot,
                    winner_party=winner,
                    winner_candidate=wc,
                    results_json=new_results,
                    demographics_json=new_demo,
                    source_files_json=new_sources,
                )
            )


async def rebuild_region_trends(db):
    res = await db.execute(select(ElectionResult))
    rows = res.scalars().all()

    totals: dict[tuple, dict[int, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    sources: dict[tuple, dict[int, set]] = defaultdict(lambda: defaultdict(set))

    for r in rows:
        detail = (r.election_detail or "").strip()
        key = (r.province, (r.district or "").strip(), r.election_type, detail)
        totals[key][r.election_year][r.party] += int(r.vote_count or 0)
        if r.source_json_file:
            sources[key][r.election_year].add(r.source_json_file)

    await db.execute(delete(ElectionRegionTrend))

    for (prov, dist, et, election_detail), year_map in totals.items():
        sorted_years = sorted(year_map.keys())
        prev_shares: dict[str, float] = {}
        detail_col = election_detail if election_detail else None
        for year in sorted_years:
            party_votes = dict(year_map[year])
            tot = sum(party_votes.values()) or 1
            src = sources[(prov, dist, et, election_detail)][year]
            src_s = ",".join(sorted(src)) if src else None
            for party, vc in party_votes.items():
                share = 100.0 * vc / tot
                prev = prev_shares.get(party)
                yoy = (share - prev) if prev is not None else None
                db.add(
                    ElectionRegionTrend(
                        province=prov,
                        district=dist or None,
                        election_type=et,
                        election_detail=detail_col,
                        party=party,
                        ref_year=year,
                        vote_share_pct=round(share, 4),
                        vote_count=int(vc),
                        yoy_delta_pct=round(float(yoy), 4) if yoy is not None else None,
                        source_json_file=src_s,
                    )
                )
            prev_shares = {p: 100.0 * v / tot for p, v in party_votes.items()}


async def run_universal_seeder():
    print("🌍 [YSK MOTORU] app/data/ysk_raw yıl klasörleri (2009–2024) taranıyor…")

    base_dir = Path(__file__).resolve().parent / "data" / "ysk_raw"
    if not base_dir.is_dir():
        print(f"❌ HATA: {base_dir} bulunamadı!")
        return

    reset = os.getenv("YSK_SEED_RESET", "1") not in ("0", "false", "False")
    async with AsyncSessionLocal() as db:
        if reset:
            print(
                "🧹 election_results / election_region_archives / election_demographic_stats / "
                "election_region_trends temizleniyor (YSK_SEED_RESET=1)."
            )
            await db.execute(delete(ElectionRegionTrend))
            await db.execute(delete(ElectionDemographicStat))
            await db.execute(delete(ElectionRegionArchive))
            await db.execute(delete(ElectionResult))
            await db.commit()
        else:
            print("ℹ️ YSK_SEED_RESET=0 — mevcut kayıtlar silinmedi; yinelenen kayıt oluşabilir.")

        for year_folder in sorted(os.listdir(base_dir)):
            year_path = base_dir / year_folder
            if not year_path.is_dir():
                continue
            try:
                election_year = int(year_folder)
            except ValueError:
                continue
            if election_year < 2009 or election_year > 2024:
                print(f"⚠️ Yıl atlandı ({election_year}), aralık 2009–2024 dışında.")
                continue

            for type_folder in sorted(os.listdir(year_path)):
                type_path = year_path / type_folder
                if not type_path.is_dir():
                    continue

                category, detail_slug = resolve_election_scope(election_year, type_folder)
                print(f"\n📂 {election_year} / {type_folder} → {category.name} / detail={detail_slug}")

                archive_buf: dict[tuple[str, str], dict] = {}

                for file_name in sorted(os.listdir(type_path)):
                    file_path = type_path / file_name
                    if not file_path.suffix.lower() == ".json":
                        continue
                    rel_source = f"{year_folder}/{type_folder}/{file_name}"

                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception as e:
                        print(f"  ⚠️ JSON okunamadı: {file_name} ({e})")
                        continue

                    if not isinstance(data, list):
                        print(f"  ⚠️ Liste bekleniyordu: {file_name}")
                        continue

                    fn_lower = file_name.lower()
                    fn_compact = fn_lower.replace(" ", "").replace("_", "")

                    is_ref_il = is_referendum_il_result_filename(fn_lower)
                    is_sonuc = (
                        not is_ref_il
                        and (
                            "secimsonuc" in fn_lower
                            or ("sonuc" in fn_lower and "secim" in fn_lower)
                        )
                        and "cinsiyet" not in fn_lower
                        and "yasdagilim" not in fn_lower
                        and "secimturuyas" not in fn_lower
                    )
                    is_cinsiyet = "cinsiyet" in fn_lower
                    is_yas = "yasdagilim" in fn_lower or "secimturuyas" in fn_lower
                    is_secilen_il = (
                        "secilen" in fn_lower
                        and "aday" in fn_lower
                        and "il" in fn_lower
                        and "ogrenim" not in fn_lower
                        and "öğrenim" not in fn_lower
                    )
                    is_ogrenim = ("ogrenim" in fn_lower or "öğrenim" in fn_lower) and not is_secilen_il
                    is_kadin_erkek = (
                        "kadinerkek" in fn_compact
                        or "kadin_erkek" in fn_lower.replace(" ", "_")
                        or (
                            "kadin" in fn_lower
                            and "erkek" in fn_lower
                            and ("orani" in fn_lower or "oran" in fn_lower)
                        )
                    )

                    if is_ref_il or is_sonuc:
                        eklenen = 0
                        haric = {
                            "İl Id",
                            "Il Id",
                            "İl Adı",
                            "Il Adı",
                            "Kayıtlı Seçmen Sayısı",
                            "Oy Kullanan Seçmen Sayısı",
                            "Geçerli Oy Toplamı",
                            "İlçe Adı",
                            "Ilce Adı",
                            " BAĞIMSIZ TOPLAM OY ",
                        }
                        if is_ref_il:
                            haric |= {
                                "Evet Oranı",
                                "Hayır Oranı",
                                "Evet Orani",
                                "Hayir Orani",
                            }
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, ilce_adi = il_adlari_from_row(row)
                            if not il_adi or is_summary_il(il_adi):
                                continue
                            for key, val in row.items():
                                kn = _strip_key(key)
                                if kn in haric or "oran" in kn.lower() or "Oranı" in kn:
                                    continue
                                parti_veya_tercih = kn.strip()
                                oy_sayisi = parse_number(val)
                                if oy_sayisi <= 0:
                                    continue
                                db.add(
                                    ElectionResult(
                                        election_year=election_year,
                                        election_type=category,
                                        election_detail=detail_slug,
                                        province=il_adi.strip(),
                                        district=(ilce_adi.strip() if ilce_adi else None),
                                        party=parti_veya_tercih,
                                        vote_count=oy_sayisi,
                                        raw_data={"source_rel": rel_source, "is_referendum_row": is_ref_il},
                                        source_json_file=rel_source,
                                    )
                                )
                                if is_ref_il:
                                    key_b = ensure_buf(archive_buf, il_adi, ilce_adi)
                                    rsub = archive_buf[key_b]["results"]["referendum"]
                                    rsub[parti_veya_tercih] = rsub.get(parti_veya_tercih, 0) + oy_sayisi
                                    if rel_source not in archive_buf[key_b]["sources"]:
                                        archive_buf[key_b]["sources"].append(rel_source)
                                else:
                                    buf_add_votes(
                                        archive_buf,
                                        il_adi,
                                        ilce_adi,
                                        parti_veya_tercih,
                                        oy_sayisi,
                                        rel_source,
                                    )
                                eklenen += 1
                        tag = "[IlReferandumSonuc]" if is_ref_il else "[SecimSonuc]"
                        print(f"  ✅ {tag} {file_name}: {eklenen} satır.")

                    elif is_cinsiyet:
                        n = 0
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, ilce_adi = il_adlari_from_row(row)
                            parti = (
                                row.get("Siyasi Parti Adı")
                                or row.get("Siyasi Partiler")
                                or row.get("Siyasi parti adı")
                            )
                            parti = _strip_key(parti) if parti else ""
                            if not parti:
                                continue
                            if not il_adi:
                                il_adi = "TÜRKİYE GENELİ"
                            kbuf = ensure_buf(archive_buf, il_adi, ilce_adi)
                            demo = archive_buf[kbuf]["demographics"].setdefault("gender_distribution", {})
                            for gender_key, dim_bucket in (
                                ("Kadın Sayısı", "Kadın"),
                                ("Erkek Sayısı", "Erkek"),
                                (" Kadın Aday Sayısı ", "Kadın"),
                                (" Erkek Aday Sayısı ", "Erkek"),
                            ):
                                raw_k = None
                                for k in row:
                                    if _strip_key(k).replace("  ", " ") == _strip_key(gender_key).replace("  ", " "):
                                        raw_k = k
                                        break
                                if raw_k is None:
                                    continue
                                c = parse_number(row.get(raw_k))
                                if c <= 0:
                                    continue
                                buf_add_demo_scalar(demo, parti, dim_bucket, c)
                                db.add(
                                    ElectionDemographicStat(
                                        election_year=election_year,
                                        election_type=category,
                                        election_detail=detail_slug,
                                        province=il_adi,
                                        district=ilce_adi,
                                        party=parti,
                                        dimension="gender",
                                        bucket=dim_bucket,
                                        count_value=c,
                                        source_json_file=rel_source,
                                    )
                                )
                                n += 1
                        print(f"  ✅ [CinsiyetDagilim] {file_name}: {n} satır.")

                    elif is_yas:
                        n = 0
                        age_keys = {
                            "18-24",
                            "25-29",
                            "30-34",
                            "35-39",
                            "40-44",
                            "45-49",
                            "50-54",
                            "55-59",
                            "60-64",
                            "65-69",
                            "70-74",
                            "+75",
                        }
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, ilce_adi = il_adlari_from_row(row)
                            parti = row.get("Siyasi Partiler") or row.get("Siyasi Parti Adı")
                            parti = _strip_key(parti) if parti else ""
                            if not parti:
                                continue
                            if not il_adi:
                                il_adi = "TÜRKİYE GENELİ"
                            kbuf = ensure_buf(archive_buf, il_adi, ilce_adi)
                            demo = archive_buf[kbuf]["demographics"].setdefault("age_distribution", {})
                            for k, val in row.items():
                                b = _strip_key(k)
                                if b not in age_keys:
                                    continue
                                cnt = parse_number(val)
                                if cnt <= 0:
                                    continue
                                buf_add_demo_scalar(demo, parti, b, cnt)
                                db.add(
                                    ElectionDemographicStat(
                                        election_year=election_year,
                                        election_type=category,
                                        election_detail=detail_slug,
                                        province=il_adi,
                                        district=ilce_adi,
                                        party=parti,
                                        dimension="age",
                                        bucket=b,
                                        count_value=cnt,
                                        source_json_file=rel_source,
                                    )
                                )
                                n += 1
                        print(f"  ✅ [YasDagilim] {file_name}: {n} satır.")

                    elif is_kadin_erkek:
                        copied = 0
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, ilce_adi = il_adlari_from_row(row)
                            if not il_adi or is_summary_il(il_adi):
                                continue
                            kbuf = ensure_buf(archive_buf, il_adi, ilce_adi)
                            archive_buf[kbuf]["demographics"].setdefault("voter_sex_ratio_rows", []).append(
                                copy.deepcopy(row)
                            )
                            copied += 1
                        print(f"  ✅ [KadinErkekOrani] {file_name}: {copied} il satırı arşivlendi.")

                    elif is_secilen_il:
                        n = 0
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, _ = il_adlari_from_row(row)
                            if not il_adi or is_summary_il(il_adi):
                                continue
                            parti_src = (
                                row.get("Siyasi Parti Adı")
                                or row.get("Siyasi Parti")
                                or row.get("Siyasi Partiler")
                            )
                            parti = _strip_key(parti_src) if parti_src else ""
                            if not parti:
                                continue
                            aday = row.get("Adı Soyadı") or row.get("Ad Soyad")
                            aday_s = str(aday).strip() if aday else ""
                            ak = ensure_buf(archive_buf, il_adi, None)
                            archive_buf[ak]["winner_candidate"] = aday_s or archive_buf[ak].get("winner_candidate")
                            db.add(
                                ElectionResult(
                                    election_year=election_year,
                                    election_type=category,
                                    election_detail=detail_slug,
                                    province=il_adi.strip(),
                                    district=None,
                                    party=parti,
                                    vote_count=1,
                                    raw_data={
                                        "data_kind": "elected_mayor_only",
                                        "candidate": aday_s,
                                    },
                                    source_json_file=rel_source,
                                )
                            )
                            n += 1
                        print(
                            f"  🏆 [SecilenAdaylarIl] {file_name}: {n} kazanan satırı "
                            f"(oy dağılımı değil; analiz Wikipedia ile tamamlanabilir)."
                        )

                    elif is_ogrenim:
                        eklenen_demo = 0
                        for row in data:
                            if not isinstance(row, dict):
                                continue
                            il_adi, _ = il_adlari_from_row(row)
                            parti = row.get("Siyasi Parti Adı", row.get("Siyasi Partiler", ""))
                            parti = _strip_key(parti) if parti else ""
                            if not parti:
                                continue
                            archive_prov = (il_adi or "").strip()
                            if not archive_prov or is_summary_il(archive_prov):
                                archive_prov = "TÜRKİYE GENELİ"
                            kb = ensure_buf(archive_buf, archive_prov, None)
                            edu = archive_buf[kb]["demographics"].setdefault("candidate_education_by_party", {})
                            egitim_seviyeleri = ["İlkokul", "Ortaokul/Lise", "Üniversite/Yüksekokul"]
                            for egitim in egitim_seviyeleri:
                                for k in row:
                                    if egitim in _strip_key(k):
                                        aday_sayisi = parse_number(row[k])
                                        if aday_sayisi > 0:
                                            e_party = edu.setdefault(parti, {})
                                            e_party[egitim] = e_party.get(egitim, 0) + aday_sayisi
                                            db.add(
                                                CandidateDemographic(
                                                    election_year=election_year,
                                                    election_type=category,
                                                    province="TÜRKİYE GENELİ",
                                                    party=parti,
                                                    education=egitim,
                                                    source_json_file=rel_source,
                                                )
                                            )
                                            eklenen_demo += 1
                                        break
                        print(f"  🎓 [Ogrenim] {file_name}: {eklenen_demo} satır.")

                if archive_buf:
                    await flush_archive_buffer(db, election_year, category, detail_slug, archive_buf)
                    print(f"  📦 Arşiv kayıtları güncellendi: {len(archive_buf)} bölge anahtarı.")

        print("\n💾 election_results / election_region_archives / demographics commit…")
        await db.commit()

        print("📈 election_region_trends yeniden hesaplanıyor…")
        await rebuild_region_trends(db)
        await db.commit()
        print("🚀 YSK seed + arşiv + trend tamamlandı.")


if __name__ == "__main__":
    asyncio.run(run_universal_seeder())
