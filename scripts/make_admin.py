import asyncio
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.db.models.user import User

async def make_admin():
  async with AsyncSessionLocal() as session:
    result = await session.execute(
      select(User).where(User.email == "test@example.com")
    )
    user = result.scalar_one_or_none()

    if not user:
      print("User not found")
      return

    user.role = "admin"
    await session.commit()
    print("User updated to admin")

if __name__ == "__main__":
  asyncio.run(make_admin())