# import asyncio
# import secrets
# import uuid
# from datetime import datetime, timedelta

# from sqlalchemy import select

# from app.db.session import AsyncSessionLocal
# from app.db.models.user import User
# from app.db.models.password_reset_token import PasswordResetToken
# from app.core.config import settings
# from app.core.security import hash_token
# from app.services.email import send_password_reset_email

# async def create_super_user():
#   async with AsyncSessionLocal() as db:
#     result = await db.execute(
#       select(User).where(User.email == settings.SUPER_USER_EMAIL)
#     )
#     existing_user = result.scalar_one_or_none()

#     if existing_user:
#       print("Super user already exists")
#       return existing_user

#     user = User(
#       email=settings.SUPER_USER_EMAIL,
#       role="admin",
#     )

#     db.add(user)
#     await db.commit()
#     await db.refresh(user)

#     token = secrets.token_urlsafe(32)
#     hashed_token = hash_token(token)
#     expires_at = datetime.utcnow() + timedelta(minutes=30)

#     reset_entry = PasswordResetToken(
#       id=str(uuid.uuid4()),
#       user_id=user.id,
#       token_hash=hashed_token,
#       expires_at=expires_at,
#     )

#     db.add(reset_entry)
#     await db.commit()

#     send_password_reset_email(
#       user.email,
#       token,
#     )

#     print(f"Super user created + reset email sent to {user.email}")
#     return user

# if __name__ == "__main__":
#     asyncio.run(create_super_user())