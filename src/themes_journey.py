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

#: Place the bundled 360p themes and fonts (the original stage).
PURPOSE_THEMES = "themes"
#: Delete the player's old ``.rockbox`` folders (Keep my settings unticked).
PURPOSE_CLEANUP = "cleanup"


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
        self._usb_asked = False
        self._player_taken = False
        self._multiple_players = False
        self._last_logged_progress = 0
        self._apk_path = ""
        self._apk_url = ""
        self._purpose = PURPOSE_THEMES
        # Set once the completion screen has taken over from the stage.
        self.completion_shown = False
        # UI objects whose lifetime *is* journey state: the ADB presence
        # watcher (plus any still finishing a stop) and the folder picker.
        self.watch = None
        self.retired_watches: set = set()
        self.folder_dialog = None

    # ------------------------------------------------------------------
    # Keeping the host awake
    # ------------------------------------------------------------------
    @property
    def _sleep_key(self) -> str:
        """This stage's own hold name, distinct from a firmware flash's."""
        return f"themes:{id(self)}"

    def _hold_awake(self) -> None:
        try:
            from .sleep_inhibit import hold_sleep

            hold_sleep(self._sleep_key)
        except Exception:
            pass

    def _release_awake(self) -> None:
        try:
            from .sleep_inhibit import release_sleep

            release_sleep(self._sleep_key)
        except Exception:
            pass

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
    def usb_asked(self) -> bool:
        """The card has asked for the USB cable after the power-on answer.

        The two questions are the same Next chip, so the card asks which one is
        on screen here rather than keeping a step number of its own.
        """
        return self._usb_asked

    @property
    def player_taken(self) -> bool:
        """A player answered over ADB: one player, one transfer from here."""
        return self._player_taken

    @property
    def multiple_players(self) -> bool:
        """More than one player is attached, so no transfer can be addressed."""
        return self._multiple_players

    @property
    def purpose(self) -> str:
        """What this stage does: place the theme pack, or remove old settings."""
        return self._purpose

    @property
    def is_cleanup(self) -> bool:
        """This stage deletes the player's old ``.rockbox`` folders."""
        return self._purpose == PURPOSE_CLEANUP

    @property
    def apk_path(self) -> str:
        """Local destination path for an ``apk_repo`` APK stage."""
        return self._apk_path

    @property
    def apk_url(self) -> str:
        """Remote download URL for an ``apk_repo`` APK stage."""
        return self._apk_url

    @property
    def is_apk_stage(self) -> bool:
        """True when the stage installs an APK from ``apk_repo`` rather than Rockbox themes."""
        return bool(self._apk_path or self._apk_url)

    def set_apk(self, apk_path: str = "", apk_url: str = "") -> None:
        """Configure or clear the ``apk_repo`` payload for this stage."""
        self._apk_path = str(apk_path or "")
        self._apk_url = str(apk_url or "")

    def begin(
        self,
        model: str,
        *,
        automatic: bool = False,
        purpose: str = PURPOSE_THEMES,
    ) -> None:
        """Start a stage from a clean slate."""
        # From here until finish() the stage may write to the player over ADB;
        # a host that suspends mid-transfer leaves a half-placed pack.
        self._hold_awake()
        self._model = (model or "Y1")
        self._automatic = bool(automatic)
        self._purpose = PURPOSE_CLEANUP if purpose == PURPOSE_CLEANUP else PURPOSE_THEMES
        self._active = True
        self._transfer_running = True
        self._placement_failed = False
        self._prompt_pending = False
        self._power_on_pending = False
        self._power_on_model = ""
        self._power_on_after_firmware = False
        self._usb_asked = False
        self._player_taken = False
        self._multiple_players = False
        self._last_logged_progress = 0

    def finish(self) -> None:
        """The transfer left the card: completed, given up, or abandoned."""
        self._release_awake()
        self._active = False
        self._transfer_running = False
        self._prompt_pending = False

    def mark_placement_failed(self) -> None:
        self._release_awake()
        self._active = False
        self._transfer_running = False
        self._placement_failed = True

    def reset_completion(self) -> None:
        """A new run starts: its completion screen has not been shown yet."""
        self.completion_shown = False

    def mark_completion_shown(self) -> None:
        self.completion_shown = True
        self._apk_path = ""
        self._apk_url = ""
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

    def ask_usb(self) -> None:
        """The card now asks for the cable, before any ADB watch starts."""
        self._usb_asked = True

    def clear_usb(self) -> None:
        """The cable question is answered (the watch owns the step from here)."""
        self._usb_asked = False

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
        button starts the stage by hand and is never gated. APK stages are
        required by the selected ``apk_repo`` package and always start. The
        cleanup stage is part of the install's own Keep Data decision, so the
        themes switch does not gate it.
        """
        if self.is_apk_stage or self.is_cleanup:
            return True, ""
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
