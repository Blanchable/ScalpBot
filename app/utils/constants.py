from enum import Enum


class AppState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    SCANNING = "SCANNING"
    PENDING_ENTRY = "PENDING_ENTRY"
    IN_POSITION = "IN_POSITION"
    EXITING = "EXITING"
    STOPPING = "STOPPING"
    HALTED = "HALTED"
    ERROR = "ERROR"
