import asyncio
from server import get_pool

async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE facts CASCADE;")
        await conn.execute("TRUNCATE entity_relationships CASCADE;")
    print("DB Truncated")
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
