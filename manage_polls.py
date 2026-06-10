import asyncio
from sqlalchemy import create_engine, text

def run():
    try:
        # Try sync psycopg2 or asyncpg via sqlalchemy
        engine = create_engine('postgresql://postgres:postgres@localhost:5432/agenda')
        with engine.connect() as conn:
            # Check what's in poll_data
            res = conn.execute(text("SELECT id, company, topic, poll_type FROM poll_data"))
            print("POLL_DATA:")
            for row in res:
                print(row)
            
            # Delete konda and metropoll
            res = conn.execute(text("DELETE FROM poll_data WHERE company ILIKE '%konda%' OR company ILIKE '%metropoll%'"))
            print("Deleted from poll_data:", res.rowcount)
            conn.commit()

            res = conn.execute(text("SELECT id, author_name, platform FROM contents WHERE platform = 'poll'"))
            print("CONTENTS (polls):")
            for row in res:
                print(row)
            
            # Delete konda and metropoll from contents if any
            res = conn.execute(text("DELETE FROM contents WHERE platform = 'poll' AND (author_name ILIKE '%konda%' OR author_name ILIKE '%metropoll%')"))
            print("Deleted from contents:", res.rowcount)
            conn.commit()
            
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    run()
