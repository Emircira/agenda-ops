import asyncio
import sys
import os

# Proje dizinini yola ekle ki app klasörünü bulabilsin
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import engine
from app.models.tables import Base

async def create_all_tables():
    print("🛠️ Veritabanına bağlanılıyor...")
    try:
        # Tüm tabloları (Content, Opportunity, vb.) veritabanında oluşturur
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ BAŞARILI: 'contents' ve diğer tüm tablolar başarıyla oluşturuldu!")
    except Exception as e:
        print(f"❌ HATA: Tablolar oluşturulurken bir sorun çıktı: {e}")

if __name__ == "__main__":
    # Async fonksiyonu çalıştır
    asyncio.run(create_all_tables())