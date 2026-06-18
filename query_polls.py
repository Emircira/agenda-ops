import asyncio
import asyncpg

async def run():
    try:
        conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/agenda')
        rows = await conn.fetch("SELECT id, author_name, raw_json FROM contents WHERE platform = 'poll'")
        for r in rows:
            print(dict(r))
        await conn.close()
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    asyncio.run(run())
