"""Tek kullanımlık: complaints_radar_cache tablosunu yoksa oluşturur."""
import asyncio

from app.db.session import engine
from app.models.core import ComplaintsRadarCache


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sc: ComplaintsRadarCache.__table__.create(sc, checkfirst=True)
        )
    print("complaints_radar_cache: OK")


if __name__ == "__main__":
    asyncio.run(main())
