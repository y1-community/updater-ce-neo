import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.catalog import FirmwarePackage
from src import keep_data, i18n


def test_firmware_package_keep_data_on_first_install_field():
    pkg_default = FirmwarePackage(
        slug="test-reborn",
        name="Reborn",
        model="Y2",
        repo="y1-community/y2-reborn",
        package_name="rom.zip",
        description="Test",
        install_first="y1-community/y1-stock-rom",
    )
    assert pkg_default.keep_data_on_first_install is False

    pkg_allowed = FirmwarePackage(
        slug="test-custom",
        name="Custom ROM",
        model="Y2",
        repo="y1-community/y2-custom",
        package_name="rom.zip",
        description="Test",
        install_first="y1-community/y1-stock-rom",
        keep_data_on_first_install=True,
    )
    assert pkg_allowed.keep_data_on_first_install is True


def test_install_first_allows_keep_data_scenarios(monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "test.ini"), QSettings.IniFormat)

    # 1. Package without install_first allows normal keep_data evaluation
    pkg_plain = FirmwarePackage(
        slug="plain-rom",
        name="Plain ROM",
        model="Y2",
        repo="y1-community/plain-rom",
        package_name="rom.zip",
        description="Plain",
    )
    assert keep_data.install_first_allows_keep_data("Y2", pkg_plain, settings=settings) is True

    # 2. Package with install_first and keep_data_on_first_install=False
    # and untracked device -> Disallows keep data
    pkg_reborn = FirmwarePackage(
        slug="reborn-y2",
        name="Reborn",
        model="Y2",
        repo="y1-community/reborn",
        package_name="rom.zip",
        description="Reborn",
        install_first="y1-community/y1-stock-rom",
        keep_data_on_first_install=False,
    )
    assert keep_data.install_first_allows_keep_data("Y2", pkg_reborn, settings=settings) is False
    assert keep_data.keep_data_available("Y2", pkg_reborn, settings=settings) is False
    assert keep_data.incompatible_with_current("Y2", pkg_reborn, settings=settings) is False
    assert keep_data.needs_confirmation("Y2", pkg_reborn, settings=settings) is False

    # 3. Package with install_first and keep_data_on_first_install=True -> Allowed
    pkg_allowed = FirmwarePackage(
        slug="reborn-allowed",
        name="Reborn Allowed",
        model="Y2",
        repo="y1-community/reborn-allowed",
        package_name="rom.zip",
        description="Reborn Allowed",
        install_first="y1-community/y1-stock-rom",
        keep_data_on_first_install=True,
    )
    assert keep_data.install_first_allows_keep_data("Y2", pkg_allowed, settings=settings) is True

    # 4. Updating a newer release of the firmware already used -> Allowed
    monkeypatch.setattr(keep_data, "tracked_is_same", lambda model, pkg, **kwargs: True)
    assert keep_data.install_first_allows_keep_data("Y2", pkg_reborn, settings=settings) is True

    # 5. Derivative relationship between tracked firmware and target firmware -> Allowed
    monkeypatch.setattr(keep_data, "tracked_is_same", lambda model, pkg, **kwargs: False)
    monkeypatch.setattr(
        keep_data,
        "tracked_installs",
        lambda model, settings, hardware_type="": [{"software_name": "Base ROM", "package_slug": "base-rom"}],
    )
    base_pkg = FirmwarePackage(
        slug="base-rom",
        name="Base ROM",
        model="Y2",
        repo="y1-community/base-rom",
        package_name="rom.zip",
        description="Base",
        derivative="y1-community/root-rom",
    )
    target_derivative = FirmwarePackage(
        slug="deriv-rom",
        name="Deriv ROM",
        model="Y2",
        repo="y1-community/deriv-rom",
        package_name="rom.zip",
        description="Deriv",
        install_first="y1-community/base-rom",
        derivative="y1-community/root-rom",
        keep_data_on_first_install=False,
    )
    monkeypatch.setattr(keep_data, "_package_for_record", lambda model, record: base_pkg)
    monkeypatch.setattr(keep_data, "_model_packages", lambda model: [base_pkg, target_derivative])
    assert keep_data.install_first_allows_keep_data("Y2", target_derivative, settings=settings) is True


def test_install_first_prompts_do_not_tell_users_to_keep_connected():
    for key in ("install_first_need_body", "install_first_when_body", "install_first_when_body_unnamed"):
        translations = i18n._STRINGS.get(key, {})
        for lang, text in translations.items():
            assert "Keep the player connected" not in text
            assert "unplug" not in text
            assert "ne le débranchez pas" not in text
            assert "no lo desenchufes" not in text
            assert "ziehe ihn nicht ab" not in text
            assert "不要拔出" not in text
            assert "プレーヤーを接続したまま" not in text


def test_onboarding_nav_lock_restores_buttons(qapp):
    from src.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    qapp.processEvents()

    select_btn = w._nav_buttons["nav_select_package"][0]
    support_btn = w._support_btn

    assert select_btn.isEnabled() is True
    assert support_btn.isEnabled() is True

    # Start onboarding:
    w.start_onboarding()
    assert getattr(w, "_onboarding_active", False) is True
    assert select_btn.isEnabled() is False
    assert support_btn.isEnabled() is False

    # Calling start_onboarding again must not corrupt the saved nav state (double lock protection)
    w._set_onboarding_nav_lock(True)

    # Finish / skip onboarding:
    w.finish_onboarding()
    assert getattr(w, "_onboarding_active", False) is False
    assert select_btn.isEnabled() is True
    assert select_btn.isVisible() is True
    assert support_btn.isEnabled() is True
    assert support_btn.isVisible() is True

    w.close()
