import asyncio
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from app.db.session import AsyncSessionLocal
from app.models.core import CityDemographics
from sqlalchemy import delete

async def run_json_seeder():
    print("\n💎 [JSON OPERASYONU] Veriler Mühürleniyor...")
    
    json_path = os.path.join("app", "data", "city_stats.json")
    
    if not os.path.exists(json_path):
        print(f"❌ HATA: {json_path} dosyası bulunamadı!")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        city_data = json.load(f)

    async with AsyncSessionLocal() as db:
        print("🧹 Eski veriler temizleniyor...")
        await db.execute(delete(CityDemographics))
        
        eklenen = 0
        for item in city_data:
            demo = CityDemographics(
                province=item["province"].upper(),
                year=item.get("year", 2024),
                total_population=item.get("total_population", 0),
                growth_rate=item.get("growth_rate", 0.0),
                university_grad_pct=item.get("university_grad_pct", 0.0),
                unemployment_rate=item.get("unemployment_rate", 0.0),
                foreign_pop_pct=item.get("foreign_pop_pct", 0.0)
            )
            db.add(demo)
            eklenen += 1
            print(f"  ✅ {item['province']} mühürlendi.")

        await db.commit()
        print(f"\n🎯 BAŞARILI: {eklenen} ilin sosyolojik verisi sisteme enjekte edildi!")

if __name__ == "__main__":
    asyncio.run(run_json_seeder())