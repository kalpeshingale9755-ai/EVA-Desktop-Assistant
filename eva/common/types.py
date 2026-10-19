"""Shared vocabulary from the approved architecture."""
from enum import Enum


class TaskOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class InterfaceState(str, Enum):
    BACKGROUND = "BACKGROUND"
    WAKING = "WAKING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    EXECUTING = "EXECUTING"
    RESPONDING = "RESPONDING"
    ERROR = "ERROR"


class Mode(str, Enum):
    COMMAND_MODE = "COMMAND_MODE"
    CONVERSATION_MODE = "CONVERSATION_MODE"
