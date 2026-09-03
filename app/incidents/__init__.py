# Register deterministic incident persistence invariants before detector use.
from app.incidents import persistence as _persistence
from app.incidents.detector import DETECTOR_VERSION, detect_incidents

__all__ = ["DETECTOR_VERSION", "detect_incidents"]
