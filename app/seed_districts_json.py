import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.db.session import AsyncSessionLocal
from app.models.core import DistrictDemographics
from sqlalchemy import delete

async def run_district_seeder():
    print("\n🔬 [MİKRO-HEDEFLEME] İlçe Verileri Mühürleniyor...")
    
    json_path = os.path.join("app", "data", "district_stats.json")
    
    if not os.path.exists(json_path):
        print(f"❌ HATA: {json_path} dosyası bulunamadı!")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        district_data = json.load(f)

    async with AsyncSessionLocal() as db:
        print("🧹 Eski ilçe verileri temizleniyor...")
        await db.execute(delete(DistrictDemographics))
        
        eklenen = 0
        for item in district_data:
            demo = DistrictDemographics(
                province=item["province"].upper(),
                district=item["district"].upper(),
                year=item.get("year", 2024),
                total_population=item.get("total_population", 0),
                growth_rate=item.get("growth_rate", 0.0),
                university_grad_pct=item.get("university_grad_pct", 0.0),
                unemployment_rate=item.get("unemployment_rate", 0.0),
                foreign_pop_pct=item.get("foreign_pop_pct", 0.0),
                source_json_file="district_stats.json",
                source_category="tuik_district_aggregate",
            )
            db.add(demo)
            eklenen += 1
            print(f"  🎯 {item['province']} / {item['district']} kılcal damarı sisteme işlendi.")

        await db.commit()
        print(f"\n✅ BAŞARILI: {eklenen} ilçenin mikro sosyolojisi enjekte edildi!")

if __name__ == "__main__":
    asyncio.run(run_district_seeder())