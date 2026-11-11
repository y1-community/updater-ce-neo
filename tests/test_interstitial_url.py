"""Off-site pages go through the thank-you or leaving interstitial."""

from urllib.parse import unquote

from src.browser import interstitial_url


def test_donation_host_is_thanked_before_the_payment_page():
    wrapped = interstitial_url("https://ko-fi.com/teamslide")
    assert wrapped.startswith("https://innioasis.app/donate.html?")
    assert "from=updater-ce" in wrapped
    assert unquote(wrapped.split("to=", 1)[1]) == "https://ko-fi.com/teamslide"


def test_post_install2_uses_the_thank_you_page_even_when_the_host_is_not_a_donor_site():
    guide = "https://example.com/after-install"
    wrapped = interstitial_url(guide, thank=True)
    assert "https://innioasis.app/donate.html?" in wrapped
    assert "from=updater-ce" in wrapped
    assert unquote(wrapped.split("to=", 1)[1]) == guide


def test_other_external_pages_use_the_leaving_page():
    wrapped = interstitial_url("https://github.com/y1-community/updater-ce-neo/discussions")
    assert wrapped.startswith("https://innioasis.app/leaving.html?")
    assert "from=updater-ce" in wrapped


def test_theme_and_firmware_downloads_stay_direct():
    theme = "https://example.com/packs/ocean.theme"
    firmware = "https://example.com/builds/player.zip"
    assert interstitial_url(theme) == theme
    assert interstitial_url(firmware) == firmware


def test_login_payment_and_auth_urls_are_not_placed_on_our_redirect():
    login = "https://example.com/account/login"
    payment = "https://shop.example/checkout?payment=1"
    auth = "https://example.com/oauth/author"
    assert interstitial_url(login) == login
    assert interstitial_url(payment) == payment
    assert interstitial_url(auth, thank=True) == auth


def test_pages_already_on_the_site_are_not_wrapped_again():
    page = "https://innioasis.app/donate.html?method=kofi"
    assert interstitial_url(page) == page
    archive = "https://themes.innioasis.app/index.html"
    assert interstitial_url(archive) == archive
