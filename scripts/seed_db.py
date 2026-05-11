import asyncio
import sys
import os

# Path ayarı
sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.models.tables import Source, SourceType

async def seed():
    async with AsyncSessionLocal() as db:
        # Örnek RSS kaynakları (Siyasi gündem için)
        sources = [
            Source(type=SourceType.rss, name="BBC Turkce", url="http://feeds.bbci.co.uk/turkce/rss.xml"),
            Source(type=SourceType.rss, name="Sputnik TR", url="https://sputniknews.com.tr/export/rss2/archive/index.xml"),
            # Gerçek senaryoda buraya rakip partiler veya ilgili haber siteleri eklenir
        ]
        
        for s in sources:
            db.add(s)
        
        try:
            await db.commit()
            print("Seed data added successfully.")
        except Exception as e:
            print(f"Error seeding data: {e}")

if __name__ == "__main__":
    asyncio.run(seed())