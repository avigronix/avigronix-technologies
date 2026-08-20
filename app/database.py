import os

from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "shop_management")

client = AsyncIOMotorClient(MONGODB_URL)
db = client[DATABASE_NAME]
