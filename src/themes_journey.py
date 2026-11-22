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
        self.payload_zip_path = None
        self.is_standalone = False
        self.is_custom_theme = False
        self.custom_theme_count = 0
        self.custom_theme_name = ""
        self.custom_theme_paths: list = []
        self.rb_themes_path_fix = None
        # USB mass-storage details from slidia_manifest.xml. Non-ADB Rockbox
        # builds use the drive scan instead of the ADB watch.
        self.rockbox_folder_path = ""
        self.rockbox_no_themes = False
        self.mass_storage = False
        self.adb_reconnect_tried = False
        # Standalone software-picker state (Settings / Ctrl+T / drag-and-drop).
        self.device_prompt_pending = False
        self.manual_target = None
        self.manual_target_is_240p = False
        self.manual_prompt_choices: list = []
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

    def set_manifest_options(
        self,
        *,
        mass_storage: bool = False,
        rockbox_folder_path: str = "",
        rockbox_no_themes: bool = False,
    ) -> None:
        """Apply Rockbox manifest transport/layout options for this stage."""
        self.mass_storage = bool(mass_storage)
        self.rockbox_folder_path = str(rockbox_folder_path or "")
        self.rockbox_no_themes = bool(rockbox_no_themes)

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
        self.adb_reconnect_tried = False
        self.device_prompt_pending = False

    def finish(self) -> None:
        """The transfer left the card: completed, given up, or abandoned."""
        self._release_awake()
        self._active = False
        self._transfer_running = False
        self._prompt_pending = False
        self.payload_zip_path = None
        self.is_standalone = False
        self.is_custom_theme = False
        self.custom_theme_count = 0
        self.custom_theme_name = ""
        self.custom_theme_paths = []
        self.rb_themes_path_fix = None
        self.rockbox_folder_path = ""
        self.rockbox_no_themes = False
        self.mass_storage = False
        self.adb_reconnect_tried = False
        self.device_prompt_pending = False
        self.manual_target = None
        self.manual_target_is_240p = False
        self.manual_prompt_choices = []

    def mark_placement_failed(self) -> None:
        self._release_awake()
        self._active = False
        self._transfer_running = False
        self._placement_failed = True

    def reset_completion(self) -> None:
        """A new run starts: its completion screen has not been shown yet."""
        self.completion_shown = False
        if not getattr(self, "is_custom_theme", False):
            self.payload_zip_path = None
            self.custom_theme_paths = []
            self.is_standalone = False
            self.rb_themes_path_fix = None

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

    def reset_player(self) -> None:
        """Clear the player claim (e.g. after a failed push or when entering fallback)."""
        self._player_taken = False

    def prefer_player(self, live_count: int) -> bool:
        """Whether the player on ADB outranks a folder the user picked.

        ADB wins because it can find every ``.rockbox`` mount and unpack on the
        device; a folder choice is only a fallback for when ADB cannot help.
        Once an ADB push has failed (adb_attempted), the manual pick is honoured.
        """
        if getattr(self, "adb_attempted", False):
            return False
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


def resolve_manual_themes_choice(
    *,
    adb_model: str | None,
    tracked_supported_models: list[str],
    rockbox_installed_models: list[str] | None = None,
    supported_models: list[str] | tuple[str, ...] = ("Y1", "Y2"),
    fallback_default: str = "",
) -> tuple[str | None, list[str], str]:
    """Decide the target model (or dropdown choices) for a standalone themes run.

    Used by Settings ("Install Themes Now"), ``Ctrl+T``/``Cmd+T``, and
    drag-and-dropped theme installs — never by an install journey that already
    knows the flashed target model.

    Returns ``(auto_model, choices, default_model)``:
    - When ``auto_model`` is not ``None``, no user prompt is needed: either the
      connected player was identified over ADB, or exactly one Rockbox-theme-
      capable model is tracked.
    - When ``auto_model`` is ``None``, the user must be asked to pick from
      ``choices`` (pre-selecting ``default_model``): either multiple models
      that support Rockbox theme installation are tracked, or the user's
      Rockbox-capable device is unknown (no supported model tracked).
    """
    supported = list(dict.fromkeys(str(m).strip().upper() for m in supported_models if str(m).strip()))
    if not supported:
        supported = ["Y1", "Y2"]

    adb_norm = str(adb_model or "").strip().upper()
    if adb_norm and adb_norm in supported:
        return adb_norm, [adb_norm], adb_norm

    tracked_set = {str(m).strip().upper() for m in (tracked_supported_models or []) if str(m).strip()}
    tracked = [m for m in supported if m in tracked_set]

    if len(tracked) == 1:
        return tracked[0], tracked, tracked[0]

    rb_set = {str(m).strip().upper() for m in (rockbox_installed_models or []) if str(m).strip()}
    rb_models = [m for m in supported if m in rb_set]
    fb_norm = str(fallback_default or "").strip().upper()

    if len(tracked) > 1:
        choices = tracked
    else:
        choices = list(supported)

    if len(rb_models) == 1 and rb_models[0] in choices:
        default_model = rb_models[0]
    elif fb_norm in choices:
        default_model = fb_norm
    else:
        default_model = choices[0]

    return None, choices, default_model


def resolve_manual_themes_target(
    *,
    adb_model: str | None,
    targets: list,
    tracked_installs: dict | None = None,
    fallback_model: str = "",
    fallback_software: str = "",
) -> tuple[object | None, list, object | None, bool]:
    """Resolve the manifest-backed Rockbox software/model target for standalone runs.

    Returns ``(auto_target, choices, default_target, tracked_is_240p)``:
    - ``auto_target`` is non-None when no wizard dropdown is needed (ADB detected
      an attached Y1/Y2, or exactly one Rockbox-capable software/model is tracked).
    - ``choices`` is the ordered list of ``RockboxThemeTarget`` entries for the
      in-GUI dropdown (e.g. ``Rockbox on Y1``, ``Rockbox on Y2``, ``Nugget on Y1``),
      strictly filtered to models/firmwares declared in ``slidia_manifest.xml``.
    - ``default_target`` is the pre-selected item when prompting.
    - ``tracked_is_240p`` is True when the resolved target or its tracked install
      record uses 240p themes (e.g. Nugget, or a tracked 240p Rockbox build).
    """
    from .player_update_check import tracked_record_is_240p

    all_targets = list(targets or [])
    if not all_targets:
        return None, [], None, False

    adb_norm = str(adb_model or "").strip().upper()
    if adb_norm:
        # Prefer the Android-port Rockbox entry for the ADB-detected model.
        adb_matches = [t for t in all_targets if t.model.upper() == adb_norm]
        android_match = next((t for t in adb_matches if t.is_android_port), None)
        chosen = android_match or (adb_matches[0] if adb_matches else None)
        if chosen is not None:
            return chosen, [chosen], chosen, bool(chosen.is_240p_themes)

    installs = tracked_installs or {}
    supported_models = {t.model.upper() for t in all_targets}
    tracked_models: list[str] = []
    matched_rb_targets: list[tuple[object, bool]] = []

    def _record_matches_target(rec: dict, target) -> bool:
        rec_model = str(rec.get("model") or "").strip().upper()
        if rec_model != target.model.upper():
            return False
        sw_low = str(rec.get("software_name") or "").strip().casefold()
        slug_low = str(rec.get("package_slug") or "").strip().casefold()
        if target.package_slug and slug_low and slug_low == target.package_slug.casefold():
            return True
        short_low = target.software_name.casefold()
        full_low = target.full_name.casefold()
        if sw_low and (sw_low == short_low or sw_low == full_low or short_low in sw_low):
            return True
        if target.is_android_port and not target.is_240p_themes and "rockbox" in sw_low and "nugget" not in sw_low:
            return True
        return False

    for key, rec in installs.items():
        if not isinstance(rec, dict):
            continue
        base_model = str(rec.get("model") or str(key).split("_")[0] or "").strip().upper()
        if base_model not in supported_models:
            continue
        if base_model not in tracked_models:
            tracked_models.append(base_model)
        rec_copy = dict(rec, model=base_model)
        for target in all_targets:
            if _record_matches_target(rec_copy, target):
                is_240 = tracked_record_is_240p(rec_copy, target)
                if not any(existing is target for existing, _ in matched_rb_targets):
                    matched_rb_targets.append((target, is_240))
                elif is_240:
                    matched_rb_targets = [
                        (t, True if t is target else flag) for t, flag in matched_rb_targets
                    ]

    def _pick_default(pool: list):
        if len(matched_rb_targets) == 1 and matched_rb_targets[0][0] in pool:
            return matched_rb_targets[0][0]
        fb_m = str(fallback_model or "").strip().upper()
        fb_sw = str(fallback_software or "").strip().casefold()
        if fb_m and fb_sw:
            for t in pool:
                if t.model.upper() == fb_m and (
                    t.software_name.casefold() in fb_sw or fb_sw in t.full_name.casefold()
                ):
                    return t
        if fb_m:
            for t in pool:
                if t.model.upper() == fb_m:
                    return t
        return pool[0]

    # Single tracked model that supports Rockbox themes:
    if len(tracked_models) == 1:
        only_model = tracked_models[0]
        model_targets = [t for t in all_targets if t.model.upper() == only_model]
        # Exactly one tracked Rockbox-family software on this single model -> auto!
        if len(matched_rb_targets) == 1:
            t, is_240 = matched_rb_targets[0]
            return t, [t], t, is_240
        # Only one Rockbox-family firmware exists for this model (e.g. Y2 -> Rockbox on Y2) -> auto!
        if len(model_targets) == 1:
            t = model_targets[0]
            return t, [t], t, bool(t.is_240p_themes)
        # Multiple tracked Rockbox-family softwares on this model (or only non-Rockbox
        # tracked on a model that has both Rockbox and Nugget) -> ask among model's targets.
        choices = [t for t, _ in matched_rb_targets] if len(matched_rb_targets) > 1 else model_targets
        return None, choices, _pick_default(choices), False

    # Multiple tracked Rockbox-capable models -> ask among targets for those models.
    if len(tracked_models) > 1:
        choices = [t for t in all_targets if t.model.upper() in tracked_models]
        if not choices:
            choices = list(all_targets)
        return None, choices, _pick_default(choices), False

    # Unknown devices (no tracked Rockbox-capable models) -> ask among all manifest targets.
    return None, list(all_targets), _pick_default(all_targets), False
