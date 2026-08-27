from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.tables import PolicyConfiguration


@dataclass(frozen=True)
class PolicyConfigurationValues:
    version: int
    quiet_hours_start: int
    quiet_hours_end: int
    contact_limit: int
    kill_switch: bool
    mock_identity: str

    @property
    def policy_version(self) -> str:
        return f"v{self.version}"


def get_policy_configuration(session: Session, app_state: Any) -> PolicyConfigurationValues:
    configuration = session.get(PolicyConfiguration, "active")
    if configuration is None:
        return PolicyConfigurationValues(
            version=1,
            quiet_hours_start=app_state.quiet_hours_start,
            quiet_hours_end=app_state.quiet_hours_end,
            contact_limit=app_state.contact_limit,
            kill_switch=app_state.kill_switch,
            mock_identity=app_state.mock_identity,
        )
    return PolicyConfigurationValues(
        version=configuration.version,
        quiet_hours_start=configuration.quiet_hours_start,
        quiet_hours_end=configuration.quiet_hours_end,
        contact_limit=configuration.contact_limit,
        kill_switch=configuration.kill_switch,
        mock_identity=configuration.mock_identity,
    )
