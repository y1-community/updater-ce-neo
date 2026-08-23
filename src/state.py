"""State machine driving the whole flash flow (port of InniUpdaterChin ``app.state``).

The GUI is driven by an explicit S1–S6 state machine plus abnormal sub-states,
per the original PRD. Every page switch, button enable and log line is a
consequence of a state transition.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List

from PySide6.QtCore import QObject, Signal


class FlashState(Enum):
    """Main flow state machine (S1–S6 plus abnormal sub-states)."""

    S1_SELECT_FILE = auto()
    S2_WAIT_CONNECTION = auto()
    S3_DEVICE_DETECTED = auto()
    S4_FLASHING = auto()
    S5_COMPLETE = auto()
    S5_FAILED = auto()
    S5_USB_DISCONNECTED = auto()
    S6_RETRYING = auto()


@dataclass
class FlashContext:
    """Mutable 'current run' state that the UI renders."""

    package_path: str = ""
    package_name: str = ""
    extract_dir: str = ""
    device_status: str = "idle"  # idle / connected / disconnected
    device_port: str = ""
    current_step: str = ""
    progress_percent: int = 0
    elapsed_time: str = "00:00"
    eta: str = "--:--"
    error_code: str = ""
    retry_count: int = 0
    log_available: bool = False
    log_messages: List[str] = field(default_factory=list)


# Allowed transitions (reconstructed exactly from bytecode).
_TRANSITIONS = {
    FlashState.S1_SELECT_FILE: (FlashState.S2_WAIT_CONNECTION,),
    FlashState.S2_WAIT_CONNECTION: (FlashState.S3_DEVICE_DETECTED, FlashState.S1_SELECT_FILE),
    FlashState.S3_DEVICE_DETECTED: (FlashState.S4_FLASHING, FlashState.S2_WAIT_CONNECTION, FlashState.S1_SELECT_FILE),
    FlashState.S4_FLASHING: (FlashState.S5_COMPLETE, FlashState.S5_FAILED, FlashState.S5_USB_DISCONNECTED),
    FlashState.S5_COMPLETE: (FlashState.S1_SELECT_FILE,),
    FlashState.S5_FAILED: (FlashState.S6_RETRYING, FlashState.S1_SELECT_FILE),
    FlashState.S5_USB_DISCONNECTED: (FlashState.S6_RETRYING, FlashState.S2_WAIT_CONNECTION),
    FlashState.S6_RETRYING: (FlashState.S2_WAIT_CONNECTION, FlashState.S5_FAILED, FlashState.S5_USB_DISCONNECTED),
}


class StateMachine(QObject):
    """Owns the current state + context and enforces legal transitions."""

    state_changed = Signal(FlashState)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = FlashState.S1_SELECT_FILE
        self._context = FlashContext()

    # -- accessors ----------------------------------------------------------
    @property
    def state(self) -> FlashState:
        return self._state

    @property
    def context(self) -> FlashContext:
        return self._context

    # -- transitions --------------------------------------------------------
    def transition_to(self, new_state: FlashState):
        if new_state not in _TRANSITIONS.get(self._state, ()):
            raise ValueError(
                f"非法状态转移: {self._state.name} → {new_state.name} (illegal state transition)"
            )
        self._set_state(new_state)

    def force_state(self, new_state: FlashState):
        """Bypass the transition table (used for resets / edge cases)."""
        self._set_state(new_state)

    def reset_for_retry(self):
        ctx = self._context
        ctx.retry_count += 1
        ctx.progress_percent = 0
        ctx.current_step = ""
        ctx.elapsed_time = "00:00"
        ctx.eta = "--:--"
        ctx.error_code = ""
        ctx.device_status = "idle"
        self._set_state(FlashState.S6_RETRYING)

    def reset_full(self):
        self._context = FlashContext()
        self._set_state(FlashState.S1_SELECT_FILE)

    # -- internals ----------------------------------------------------------
    def _set_state(self, new_state: FlashState):
        old = self._state
        self._state = new_state
        self._on_enter(new_state, old)
        self.state_changed.emit(new_state)

    def _on_enter(self, new_state: FlashState, old_state: FlashState):
        ctx = self._context
        if new_state is FlashState.S2_WAIT_CONNECTION:
            ctx.device_status = "idle"
        elif new_state is FlashState.S3_DEVICE_DETECTED:
            ctx.device_status = "connected"
        elif new_state is FlashState.S4_FLASHING:
            ctx.progress_percent = 0
            ctx.current_step = ""
            ctx.elapsed_time = "00:00"
            ctx.eta = "--:--"
        elif new_state is FlashState.S5_USB_DISCONNECTED:
            ctx.device_status = "disconnected"
        elif new_state is FlashState.S6_RETRYING:
            ctx.current_step = ""
