"""One-off patch: guided-install failover, power-off gating, silent switches."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
MW = "src/ui/main_window.py"


def sub(rel, old, new, count=1):
    p = ROOT / rel
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    o = old.replace("\n", nl)
    n = new.replace("\n", nl)
    found = raw.count(o)
    if found != count:
        raise SystemExit(
            f"{rel}: expected {count} match(es), found {found} for:\n{o[:400]}"
        )
    p.write_bytes(raw.replace(o, n, count).encode("utf-8"))
    print(f"patched {rel}")


sub(
    MW,
    """from ..flash_service import (
    LINE_CONNECT_HINT,
    LINE_RETRY,
    METHOD_MTK_MAC,
    STEP_DETECT,
""",
    """from ..flash_service import (
    LINE_CONNECT_HINT,
    LINE_RETRY,
    METHOD_MTK,
    METHOD_MTK_MAC,
    METHOD_SP,
    STEP_DETECT,
""",
    count=1,
)

sub(
    MW,
    """    FlashService,
    classify_backend_line,
    completed_extract_dir,
    normalise_method,
)
""",
    """    FlashService,
    backend_method,
    classify_backend_line,
    completed_extract_dir,
    is_wrong_model_error,
    normalise_method,
)
""",
)

sub(
    MW,
    """        self._install_failed = False
        self._power_off_prompt = False
        self._gui_handoff_prepare = False
""",
    """        self._install_failed = False
        # True once this install has already fallen back to the other backend:
        # one automatic switch per run, so a bad cable cannot ping-pong.
        self._fallback_attempted = False
        self._power_off_prompt = False
        self._gui_handoff_prepare = False
""",
)

sub(
    MW,
    """        self._settings_page.flash_method_changed.connect(self._on_method_changed)
        self._settings_page.simulated_mac_requested.connect(
""",
    """        self._settings_page.flash_method_changed.connect(self._on_method_changed)
        # The app's own no-progress countdown, shown on the install card.
        self.service.attempt_timeout.connect(self._flash_page.set_attempt_timeout)
        self._settings_page.simulated_mac_requested.connect(
""",
)

sub(
    MW,
    """    def _on_progress_retry(self):
        \"\"\"Back to the power-off / unplug prompt. Does not start a flash.\"\"\"
""",
    """    # ------------------------------------------------------------------
    # Backend failover
    # ------------------------------------------------------------------
    def _other_method(self, method) -> str:
        \"\"\"The other backend for ``method``, or "" when there is not one.

        Only hosts that ship both engines can switch: macOS has no SP Flash
        Tool at all. This is about the guided in-app flow only — the terminal
        launcher and the SP Flash Tool GUI handoff are deliberately left to
        run the tool themselves.
        \"\"\"
        if paths.IS_MAC:
            return ""
        current = backend_method(method)
        if current == METHOD_SP:
            return METHOD_MTK
        if current == METHOD_MTK:
            return METHOD_SP
        return ""

    def _return_to_power_off_stage(self, method="") -> None:
        \"\"\"Re-arm the disconnect / power-off step; Continue starts ``method``.

        A backend's command must not start hunting for the player until the
        user has confirmed it is unplugged and powered off. SP Flash Tool's
        console search expires on its own two-minute window, so a command
        started too early reports S_TIMEOUT while nothing was actually wrong.
        \"\"\"
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        self._install_failed = False
        self._power_off_prompt = True
        self._pre_install_guided = False
        if self.sm.state in (
            FlashState.S5_FAILED,
            FlashState.S5_USB_DISCONNECTED,
            FlashState.S4_FLASHING,
            FlashState.S5_COMPLETE,
        ):
            self.sm.reset_for_retry()
        if method:
            # Session-scoped: the saved preference stays whatever the user
            # chose, so the next install starts from their pick again.
            self._flash_method = backend_method(method)
            self._flash_page.set_method(self._flash_method)
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._apply_release_icon(complete=False)
        self._flash_page.show_power_off_prompt()
        self._nav_to_page(_PAGE_FLASH)

    def _offer_backend_failover(self, error_code) -> bool:
        \"\"\"Send the user back to the power-off step to try the other backend.

        Applies to guided in-app installs on hosts with both engines. A
        wrong-model result is never retried (no backend can fix it), and only
        one switch is made per install, so an exhausted pair of engines ends
        as an explained failure instead of a loop.
        \"\"\"
        if getattr(self, "_fallback_attempted", False):
            return False
        if is_wrong_model_error(error_code):
            return False
        candidate = self._other_method(self._flash_method)
        if not candidate:
            return False
        self._fallback_attempted = True
        # Diagnostics only: the user should not have to read backend names.
        logger.info(
            "Install attempt with %s did not start (%s); offering %s",
            backend_method(self._flash_method),
            error_code,
            candidate,
        )
        self._return_to_power_off_stage(candidate)
        return True

    def _on_progress_retry(self):
        \"\"\"Back to the power-off / unplug prompt. Does not start a flash.\"\"\"
""",
)

sub(
    MW,
    """    def _handle_flash_failure(self, error_code):
        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        self._show_install_failure(str(error_code or tr(\"flash_failed\")))
""",
    """    def _handle_flash_failure(self, error_code):
        # A backend that never got going is not the end of the install on a
        # host that has the other engine: hand the user back to the power-off
        # step and start the other one from there.
        if self._offer_backend_failover(error_code):
            return
        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        self._show_install_failure(str(error_code or tr(\"flash_failed\")))
""",
)

sub(
    MW,
    """        if method in (\"sp\", \"auto\"):
            try:
                from ..sp_flash_gui import update_sp_history_ini
                update_sp_history_ini(model=self._package_model)
            except Exception as e:
                logger.debug(\"Could not update SP history.ini on method change: %s\", e)
        self._append_log(f\"Flash method changed to {_method_label(method)}; restarting search...\")
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._flash_page.set_method(method)
        pre_extracted = completed_extract_dir(self._package_path)
        self.service.start_flash(
            self._package_path, pre_extracted_dir=pre_extracted, method=method
        )
        self.service.start_device_monitor()
""",
    """        if method in (\"sp\", \"auto\"):
            try:
                from ..sp_flash_gui import update_sp_history_ini
                update_sp_history_ini(model=self._package_model)
            except Exception as e:
                logger.debug(\"Could not update SP history.ini on method change: %s\", e)
        # Kept silent for the user: the app is a front end for both engines and
        # the install flow should read the same either way. The restart is a
        # diagnostic detail, and the new command must not start until the user
        # has again confirmed the player is unplugged and powered off.
        logger.info(\"Install method changed to %s\", backend_method(method))
        self._return_to_power_off_stage(method)
""",
)

sub(
    MW,
    """        if method == METHOD_MTK_MAC:
            # The settings card already asked. This only records the choice.
            # The restart itself arrives on simulated_mac_requested.
            self._append_log(\"Install method set to MTKClient (Mac) simulation.\")
            return
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            self._append_log(f\"Install method set to {_method_label(method)}.\")
            return
""",
    """        if method == METHOD_MTK_MAC:
            # The settings card already asked. This only records the choice;
            # the restart itself arrives on simulated_mac_requested. The user
            # is not told about backends.
            logger.info(\"Install method set to the simulated macOS flow\")
            return
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            logger.info(\"Install method set to %s for the next run\", backend_method(method))
            return
""",
)

print("main_window patched")
