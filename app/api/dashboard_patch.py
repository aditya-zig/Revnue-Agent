"""Install the batched dashboard implementation before routers are mounted.

`app.main` loads API modules dynamically. Importing this module from the API
package initializer replaces only the expensive dashboard JSON route while
leaving the existing dashboard page and helper functions intact.
"""

from app.api import dashboard as legacy
from app.api.dashboard_fast import get_dashboard_fast

DASHBOARD_PATH = "/api/v1/dashboard"

legacy.router.routes[:] = [
    route
    for route in legacy.router.routes
    if not (
        getattr(route, "path", None) == DASHBOARD_PATH
        and "GET" in (getattr(route, "methods", set()) or set())
    )
]
legacy.router.add_api_route(
    DASHBOARD_PATH,
    get_dashboard_fast,
    methods=["GET"],
    tags=["dashboard"],
)
