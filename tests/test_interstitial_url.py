"""Off-site pages go through the thank-you or leaving interstitial."""

from urllib.parse import unquote

from src.browser import interstitial_url


def test_donation_host_is_thanked_before_the_payment_page():
    wrapped = interstitial_url("https://ko-fi.com/teamslide")
    assert wrapped.startswith("https://innioasis.app/donate.html?to=")
    assert unquote(wrapped.split("to=", 1)[1]) == "https://ko-fi.com/teamslide"


def test_post_install2_uses_the_thank_you_page_even_when_the_host_is_not_a_donor_site():
    guide = "https://example.com/after-install"
    wrapped = interstitial_url(guide, thank=True)
    assert wrapped.startswith("https://innioasis.app/donate.html?to=")
    assert unquote(wrapped.split("to=", 1)[1]) == guide


def test_other_external_pages_use_the_leaving_page():
    wrapped = interstitial_url("https://github.com/y1-community/updater-ce-neo/discussions")
    assert wrapped.startswith("https://innioasis.app/leaving.html?to=")


def test_theme_and_firmware_downloads_stay_direct():
    theme = "https://example.com/packs/ocean.theme"
    firmware = "https://example.com/builds/player.zip"
    assert interstitial_url(theme) == theme
    assert interstitial_url(firmware) == firmware


def test_pages_already_on_the_site_are_not_wrapped_again():
    page = "https://innioasis.app/donate.html?method=kofi"
    assert interstitial_url(page) == page
    archive = "https://themes.innioasis.app/index.html"
    assert interstitial_url(archive) == archive
