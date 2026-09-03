"""ReRoute Intelligence application package."""

# Import the replay control model so Base.metadata includes it for the Vercel
# SQLite fallback path, which initializes tables with metadata.create_all().
from app.db.replay import MerchantReplayControl

__all__ = ["MerchantReplayControl"]
