import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.db.session import AsyncSessionLocal
from app.models.core import ElectionResult, ElectionCategory, CandidateDemographic

def parse_number(val):
    """Noktalı veya virgüllü metinleri gerçek sayılara çevirir (Örn: '14.233' -> 14233)"""
    if not val or str(val).strip() == "": return 0
    try:
        return int(str(val).replace(".", "").replace(",", "").strip())
    except ValueError:
        return 0

def detect_election_type(folder_name: str):
    """Klasör adından seçimin türünü ve detayını anlar"""
    folder_name = folder_name.lower()
    if "cb" in folder_name or "cumhurbaskanligi" in folder_name:
        return ElectionCategory.presidential, folder_name
    elif "milletvekili" in folder_name or "genel" in folder_name:
        return ElectionCategory.parliamentary, folder_name
    elif "referandum" in folder_name or "refarandum" in folder_name:
        return ElectionCategory.referendum, folder_name
    else:
        return ElectionCategory.local, folder_name

async def run_universal_seeder():
    print("🌍 [EVRENSEL YSK MOTORU] Devreye Girdi! Tüm Klasörler Taranıyor...")
    
    base_dir = os.path.join("app", "data", "ysk_raw")
    if not os.path.exists(base_dir):
        print(f"❌ HATA: {base_dir} klasörü bulunamadı!")
        return

    async with AsyncSessionLocal() as db:
        for year_folder in os.listdir(base_dir):
            year_path = os.path.join(base_dir, year_folder)
            if not os.path.isdir(year_path): continue
            
            try:
                election_year = int(year_folder)
            except ValueError:
                continue

            for type_folder in os.listdir(year_path):
                type_path = os.path.join(year_path, type_folder)
                if not os.path.isdir(type_path): continue

                category, detail = detect_election_type(type_folder)
                print(f"\n📂 İNCELENİYOR: {election_year} -> {type_folder} ({category.name})")

                for file_name in os.listdir(type_path):
                    file_path = os.path.join(type_path, file_name)
                    if not file_name.endswith(".json"): continue

                    with open(file_path, 'r', encoding='utf-8') as f:
                        try:
                            data = json.load(f)
                        except:
                            print(f"  ⚠️ JSON Okuma Hatası: {file_name}")
                            continue

                    # --- A. OY SONUÇLARINI İŞLEME ---
                    if "SecimSonuc" in file_name or "Sonuc" in file_name:
                        eklenen = 0
                        for row in data:
                            il_adi = row.get("İl Adı", "").strip()
                            if not il_adi or il_adi == "İLLER TOPLAMI" or "%" in il_adi: continue
                            
                            haric = ["İl Id", "İl Adı", "Kayıtlı Seçmen Sayısı", "Oy Kullanan Seçmen Sayısı", "Geçerli Oy Toplamı", " BAĞIMSIZ TOPLAM OY "]
                            
                            for key, val in row.items():
                                if key not in haric and "Oranı" not in key:
                                    parti_veya_tercih = key.strip()
                                    oy_sayisi = parse_number(val)
                                    
                                    if oy_sayisi > 0:
                                        db.add(ElectionResult(
                                            election_year=election_year,
                                            election_type=category,
                                            election_detail=detail,
                                            province=il_adi,
                                            party=parti_veya_tercih,
                                            vote_count=oy_sayisi
                                        ))
                                        eklenen += 1
                        print(f"  ✅ [SONUÇ] {file_name}: {eklenen} veri mühürlendi.")

                    # --- B. ADAY DEMOGRAFİSİNİ İŞLEME ---
                    elif "AdaylarınOgrenimDurumu" in file_name or "Ogrenim" in file_name:
                        eklenen_demo = 0
                        for row in data:
                            parti = row.get("Siyasi Parti Adı", row.get("Siyasi Partiler", "")).strip()
                            if not parti: continue
                            
                            egitim_seviyeleri = ["İlkokul", "Ortaokul/Lise", "Üniversite/Yüksekokul"]
                            for egitim in egitim_seviyeleri:
                                aday_sayisi = parse_number(row.get(f" {egitim} ", 0))
                                if aday_sayisi > 0:
                                    # BURADAKİ CANDIDATE_NAME KALDIRILDI!
                                    db.add(CandidateDemographic(
                                        election_year=election_year,
                                        election_type=category,
                                        province="TÜRKİYE GENELİ",
                                        party=parti,
                                        education=egitim
                                    ))
                                    eklenen_demo += 1
                        print(f"  🎓 [DEMOGRAFİ] {file_name}: {eklenen_demo} eğitim verisi mühürlendi.")

        print("\n💾 Veritabanına Yazılıyor, Lütfen Bekleyin...")
        await db.commit()
        print("🚀 HAREKAT BAŞARILI: Tüm YSK Arşivi Karargaha Yüklendi!")

if __name__ == "__main__":
    asyncio.run(run_universal_seeder())