# Install the hosted-dashboard optimization before app.main mounts API routers.
from app.api import dashboard_patch as _dashboard_patch  # noqa: F401
