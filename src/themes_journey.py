"""The Rockbox 360p themes & fonts stage: one owner for its state and rules.

The stage spans two widgets — the install card that draws the steps
(``ui/flash_page.py``) and the window that owns the run, the completion screen
and the Settings switch (``ui/main_window.py``) — so its state used to live as
a dozen private attributes split between the two. This object is the single
owner: which stage is running, whether a player answered over ADB, whether the
power-on prompt is waiting, whether the folder picker is open, which watch is
polling. It also answers the rules both files used to branch on inline.

It is plain Python. The widgets keep their widgets, their timers, their Qt
workers and their copy; they call in here for the decisions.
"""

from __future__ import annotations


class ThemesJourney:
    """State and rules for one Rockbox themes/fonts stage."""

    def __init__(self) -> None:
        self._model = "Y1"
        self._automatic = False
        self._active = False
        self._transfer_running = False
        self._placement_failed = False
        self._prompt_pending = False
        self._power_on_pending = False
        self._power_on_model = ""
        self._power_on_after_firmware = False
        self._player_taken = False
        self._multiple_players = False
        self._last_logged_progress = 0
        # Set once the completion screen has taken over from the stage.
        self.completion_shown = False
        # UI objects whose lifetime *is* journey state: the ADB presence
        # watcher (plus any still finishing a stop) and the folder picker.
        self.watch = None
        self.retired_watches: set = set()
        self.folder_dialog = None

    # ------------------------------------------------------------------
    # Stage state
    # ------------------------------------------------------------------
    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = (value or "Y1")

    @property
    def automatic(self) -> bool:
        """True when the app started this stage by itself (Settings gate)."""
        return self._automatic

    @property
    def active(self) -> bool:
        """A themes run owns the window: prompt, guidance, or transfer.

        The one answer to "is a themes run on?" for the window's install-run
        lock, its device-lost guard and its close prompt. ``transfer_running``
        is the narrower question the card asks about what it is drawing.
        """
        return self._active

    @property
    def transfer_running(self) -> bool:
        """The in-card transfer owns the screen (the power-on prompt does not)."""
        return self._transfer_running

    @property
    def placement_failed(self) -> bool:
        """The stage gave up placing the pack and ended on the completion screen."""
        return self._placement_failed

    @property
    def prompt_pending(self) -> bool:
        """Asking the user to power the player on before the transfer."""
        return self._prompt_pending

    @property
    def power_on_pending(self) -> bool:
        """The window asked for a boot and is waiting for the Continue."""
        return self._power_on_pending

    @property
    def power_on_model(self) -> str:
        """The player the window asked to boot (empty once it has answered)."""
        return self._power_on_model

    @property
    def power_on_after_firmware(self) -> bool:
        """The pending boot follows a firmware install in the same run."""
        return self._power_on_after_firmware

    @property
    def player_taken(self) -> bool:
        """A player answered over ADB: one player, one transfer from here."""
        return self._player_taken

    @property
    def multiple_players(self) -> bool:
        """More than one player is attached, so no transfer can be addressed."""
        return self._multiple_players

    def begin(self, model: str, *, automatic: bool = False) -> None:
        """Start a stage from a clean slate."""
        self._model = (model or "Y1")
        self._automatic = bool(automatic)
        self._active = True
        self._transfer_running = True
        self._placement_failed = False
        self._prompt_pending = False
        self._power_on_pending = False
        self._power_on_model = ""
        self._power_on_after_firmware = False
        self._player_taken = False
        self._multiple_players = False
        self._last_logged_progress = 0

    def finish(self) -> None:
        """The transfer left the card: completed, given up, or abandoned."""
        self._active = False
        self._transfer_running = False
        self._prompt_pending = False

    def mark_placement_failed(self) -> None:
        self._active = False
        self._transfer_running = False
        self._placement_failed = True

    def reset_completion(self) -> None:
        """A new run starts: its completion screen has not been shown yet."""
        self.completion_shown = False

    def mark_completion_shown(self) -> None:
        self.completion_shown = True
        self.clear_power_on()

    # ------------------------------------------------------------------
    # The power-on prompt
    # ------------------------------------------------------------------
    def open_prompt(self) -> None:
        """The card now asks the user to boot the player (no transfer yet).

        The run is already on: the prompt is part of the stage, not a pause
        before it, so the window stays locked while it is up.
        """
        self._active = True
        self._transfer_running = False
        self._prompt_pending = True

    def settle_prompt(self) -> None:
        self._prompt_pending = False

    def ask_power_on(self, model: str, *, after_firmware: bool = False) -> None:
        self._power_on_pending = True
        self._power_on_model = model or ""
        self._power_on_after_firmware = bool(after_firmware)

    def clear_power_on(self) -> None:
        self._power_on_pending = False
        self._power_on_model = ""
        self._power_on_after_firmware = False

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def note_multiple(self) -> None:
        """Two players share one serial: nothing can be addressed until one leaves."""
        self._multiple_players = True

    def clear_multiple(self) -> None:
        self._multiple_players = False

    def take_player(self) -> bool:
        """Claim the player that answered. False if one was already claimed."""
        if self._player_taken:
            return False
        self._player_taken = True
        self._multiple_players = False
        return True

    def prefer_player(self, live_count: int) -> bool:
        """Whether the player on ADB outranks a folder the user picked.

        ADB wins because it can find every ``.rockbox`` mount and unpack on the
        device; a folder choice is only a fallback for when ADB cannot help.
        """
        return live_count == 1 and not self._player_taken

    # ------------------------------------------------------------------
    # Stage start
    # ------------------------------------------------------------------
    def should_start(self, *, automatic: bool, settings) -> tuple[bool, str]:
        """Whether a stage may start, and the log line when it may not.

        An automatic start obeys the in-beta Settings switch; the Settings
        button starts the stage by hand and is never gated.
        """
        if automatic and not self._auto_install_enabled(settings):
            return False, (
                "[Rockbox Themes] Themes auto-install is off in Settings; "
                "not starting the themes stage."
            )
        return True, ""

    @staticmethod
    def _auto_install_enabled(settings) -> bool:
        from .theme_sync import themes_auto_install_enabled

        return bool(themes_auto_install_enabled(settings))

    # ------------------------------------------------------------------
    # Progress logging
    # ------------------------------------------------------------------
    def reset_progress(self) -> None:
        """A new transfer starts: log its first file again."""
        self._last_logged_progress = 0

    def progress_log_due(self, curr: int, total: int) -> bool:
        """Whether this progress tick is worth a log line.

        First file, last file, and every 25 files thereafter (20 on the
        percentage scale the pack transfer uses).
        """
        last = self._last_logged_progress
        due = (
            curr == 1
            or curr == total
            or (curr - last) >= 25
            or (total == 100 and (curr - last) >= 20)
        )
        if due:
            self._last_logged_progress = curr
        return due

    # ------------------------------------------------------------------
    # Watches
    # ------------------------------------------------------------------
    def attach_watch(self, worker) -> None:
        self.watch = worker

    def take_watch(self):
        """Hand the current watcher back to the caller to stop, and forget it."""
        worker = self.watch
        self.watch = None
        return worker

    def retire_watch(self, worker) -> None:
        """Keep a watcher that is slow to stop alive until it finishes."""
        self.retired_watches.add(worker)

    def forget_watch(self, worker) -> None:
        self.retired_watches.discard(worker)

    # ------------------------------------------------------------------
    # Folder picker
    # ------------------------------------------------------------------
    def attach_folder_dialog(self, dialog) -> None:
        self.folder_dialog = dialog

    def take_folder_dialog(self):
        """Hand the open picker back to the caller to dismiss, and forget it."""
        dialog = self.folder_dialog
        self.folder_dialog = None
        return dialog
