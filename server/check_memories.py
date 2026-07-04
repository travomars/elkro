import asyncio
from server import get_pool

async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetch("SELECT DISTINCT project_id FROM memories;")
        print("Projects:", [r['project_id'] for r in res])
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
