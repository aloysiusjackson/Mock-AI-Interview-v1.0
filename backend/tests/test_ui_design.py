"""
Design-system tests.

These do not attempt pixel comparison. They lock down the *contract* the visual
layer depends on, so a future edit cannot silently detach the styling:

* every page is served and links the design system last (so it still wins);
* the theme tokens that define the look are present;
* the cascade traps that were fixed once stay fixed (primary buttons, the
  view-title gradient, the legacy blue aliases);
* the markup hooks the styling and JS rely on still exist.
"""

from __future__ import annotations

import os
import re
import unittest

from support import FRONTEND_DIR, ApiTestCase, fetch_text, flask_app, token_from_link

PAGES = [
    "landing.html",
    "index.html",
    "login.html",
    "reset-password.html",
    "privacy.html",
    "terms.html",
    "contact.html",
    "404.html",
]

# Pages that pull in one of the older, blue-palette stylesheets.
LEGACY_SHEETS = ("css/styles.css", "css/login.css", "css/legal.css")

THEME_PATH = os.path.join(FRONTEND_DIR, "css", "theme.css")


def read_page(name: str) -> str:
    with open(os.path.join(FRONTEND_DIR, name), "r", encoding="utf-8") as handle:
        return handle.read()


def read_theme() -> str:
    with open(THEME_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def head_of(html: str) -> str:
    match = re.search(r"<head[^>]*>(.*?)</head>", html, re.S | re.I)
    assert match, "page has no <head>"
    return match.group(1)


def extract_element(html: str, marker: str, closing: str = "</button>") -> str:
    """Slice out the element starting at `marker` (useful for markup assertions)."""
    start = html.find(marker)
    assert start != -1, f"{marker} not found"
    end = html.find(closing, start)
    assert end != -1, f"{marker} is never closed"
    return html[start:end]


class PageAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.client = flask_app.test_client()

    def test_root_serves_the_landing_page(self):
        response, body = fetch_text(self.client, "/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Practice Smarter", body)

    def test_every_page_is_served(self):
        for page in PAGES:
            with self.subTest(page=page):
                response, body = fetch_text(self.client, f"/{page}")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(body.strip())

    def test_unknown_path_returns_the_designed_404(self):
        response, body = fetch_text(self.client, "/no-such-page")
        self.assertEqual(response.status_code, 404)
        self.assertIn("didn't make the cut", body)

    def test_theme_stylesheet_is_served(self):
        response, body = fetch_text(self.client, "/css/theme.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["Content-Type"])
        self.assertIn("--bg-base: #0b0b0f", body)

    def test_favicon_is_served(self):
        response, _ = fetch_text(self.client, "/favicon.svg")
        self.assertEqual(response.status_code, 200)


class StylesheetOrderTests(unittest.TestCase):
    """theme.css must load *after* every legacy sheet, or the blue palette wins."""

    def test_every_page_links_the_design_system(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.assertIn("css/theme.css", head_of(read_page(page)))

    def test_design_system_loads_last(self):
        for page in PAGES:
            with self.subTest(page=page):
                head = head_of(read_page(page))
                theme_at = head.find("css/theme.css")

                for sheet in LEGACY_SHEETS:
                    sheet_at = head.find(sheet)
                    if sheet_at != -1:
                        self.assertGreater(
                            theme_at, sheet_at,
                            f"{page}: theme.css must come after {sheet}",
                        )

                inline_style_end = head.find("</style>")
                if inline_style_end != -1:
                    self.assertGreater(
                        theme_at, inline_style_end,
                        f"{page}: theme.css must come after the inline <style> block",
                    )

    def test_every_page_declares_a_favicon(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.assertIn("/favicon.svg", head_of(read_page(page)))


class DesignTokenTests(unittest.TestCase):
    def setUp(self):
        self.theme = read_theme()

    def test_canvas_and_surface_tokens(self):
        self.assertIn("--bg-base: #0b0b0f", self.theme)

    def test_the_orange_and_purple_glow_layers_are_defined(self):
        self.assertIn("rgba(217, 119, 54", self.theme, "orange glow")
        self.assertIn("rgba(107, 56, 251", self.theme, "purple glow")

    def test_glass_surfaces_are_defined(self):
        self.assertIn("rgba(255, 255, 255, 0.02)", self.theme)
        self.assertIn("backdrop-filter", self.theme)

    def test_primary_action_is_a_purple_to_indigo_gradient(self):
        self.assertIn("#9333ea", self.theme)
        self.assertIn("#4f46e5", self.theme)
        self.assertIn("--btn-gradient:", self.theme)

    def test_accent_text_is_purple(self):
        self.assertIn("--accent-text: #c084fc", self.theme)

    def test_light_theme_has_its_own_values(self):
        self.assertIn('[data-theme="light"]', self.theme)
        light_block = self.theme.split('[data-theme="light"]', 1)[1][:2000]
        self.assertIn("--bg-base:", light_block)
        self.assertIn("--text-primary:", light_block)

    def test_typesheet_is_structurally_valid(self):
        self.assertEqual(self.theme.count("{"), self.theme.count("}"), "unbalanced braces")
        self.assertNotIn("undefined", self.theme)
        self.assertNotIn("null;", self.theme)

    def test_reduced_motion_is_respected(self):
        self.assertIn("prefers-reduced-motion", self.theme)

    def test_legacy_blue_alias_variables_are_repointed(self):
        # legal.css / 404.html / login.css declared their own blue aliases.
        for alias in ("--cyan:", "--ice:", "--olive:", "--sage:"):
            with self.subTest(alias=alias):
                self.assertIn(alias, self.theme)

    def test_content_column_no_longer_paints_the_legacy_blue(self):
        self.assertIn(".main-content", self.theme)
        main_rule = self.theme.split(".main-content", 1)[1][:400]
        self.assertIn("transparent", main_rule)


class CascadeRegressionTests(unittest.TestCase):
    """Guards for the cascade bugs that were fixed by hand."""

    def setUp(self):
        self.theme = read_theme()

    def test_primary_button_rule_comes_after_the_glass_base_rule(self):
        """`.btn:not(.primary)` once outranked `.btn-primary`, greying out buttons."""
        base_at = self.theme.find(".btn,\n.btn-secondary")
        self.assertNotEqual(base_at, -1, "glass .btn base rule is missing")

        primary_at = self.theme.find(".btn.btn-primary")
        self.assertNotEqual(primary_at, -1, "specific primary-button rule is missing")
        self.assertGreater(primary_at, base_at, "primary buttons would lose to the glass base rule")

    def test_legacy_sel_not_selector_is_gone_from_the_button_base(self):
        self.assertNotIn(".btn:not(.primary)", self.theme)

    def test_view_title_keeps_a_readable_gradient(self):
        self.assertIn(".page-title h1", self.theme)
        rule = self.theme.split(".page-title h1", 1)[1][:400]
        self.assertIn("text-fill-color", rule)

    def test_skeleton_loader_is_recoloured(self):
        skeleton_rule = self.theme.split(".skeleton:not(.is-loaded)", 1)[1][:200]
        self.assertIn("rgba(147, 51, 234", skeleton_rule)

    def test_mobile_account_button_is_a_circular_user_icon(self):
        rule = self.theme.split(".floating-nav-btn {", 1)[1][:400]
        self.assertIn("border-radius: 50%", rule)
        self.assertIn("--accent-gradient", rule)


class MarkupHookTests(unittest.TestCase):
    """The styling and the JS both depend on these nodes existing."""

    def setUp(self):
        self.html = read_page("index.html")

    def test_sidebar_collapse_control_exists(self):
        self.assertIn('id="sidebar-collapse-btn"', self.html)

    def test_nav_labels_exist_for_the_collapsed_state(self):
        self.assertGreaterEqual(self.html.count('class="nav-label"'), 7)

    def test_skeleton_targets_exist(self):
        self.assertIn('class="metric-val skeleton"', self.html)
        self.assertGreaterEqual(self.html.count("chart-skeleton"), 3)

    def test_mobile_trigger_is_a_user_icon_not_a_hamburger(self):
        button = extract_element(self.html, 'id="floating-nav-btn"')
        self.assertIn("nav-user-icon", button)
        self.assertIn("floating-user-initial", button)
        self.assertNotIn("<line", button, "the hamburger lines must be gone")

    def test_sidebar_account_button_uses_the_user_glyph(self):
        button = extract_element(self.html, 'id="mobile-menu-btn"')
        self.assertIn("M12 12c2.21", button, "expected the person glyph path")
        self.assertNotIn("M3 6h18v2H3z", button, "the hamburger path must be gone")

    def test_mobile_account_menu_still_carries_the_navigation_tools(self):
        panel = extract_element(self.html, 'id="mobile-nav-panel"', closing="<!-- Resume Import Modal -->")
        for tool in ("Dashboard", "Practice Logs", "Score Board", "Skill Assessment",
                     "Resources & Tips", "Achievements", "Settings"):
            with self.subTest(tool=tool):
                self.assertIn(tool, panel)
        self.assertIn("user-avatar-tag-mobile", panel)
        self.assertIn("mobile-sidebar-auth-btns", panel)

    def test_theme_dependent_ids_are_present(self):
        for element_id in (
            "topbar-view-title",
            "dashboard-empty-state",
            "user-avatar-tag",
            "mobile-nav-panel",
            "floating-theme-btn",
        ):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', self.html)


class ServerRenderedPageTests(ApiTestCase):
    """Email links land on pages Flask renders itself, not on the static app.

    Those screens still have to look like the same product, so the design
    tokens are asserted here just like for the static pages -- the verification
    page used to be the one screen still wearing the old blue palette.
    """

    THEME_MARKERS = ("#0b0b0f", "glow-orange", "glow-purple", "9333ea", "4f46e5", "brand-tile")
    LEGACY_BLUE = ("#f0f2f8", "667eea", "6366f1")

    def assert_themed(self, body):
        for marker in self.THEME_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(marker, body)
        for legacy in self.LEGACY_BLUE:
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, body)

    def test_successful_verification_is_a_themed_page(self):
        response, _, _ = self.signup()
        token = token_from_link(response.get_json()["verification_link"])

        verified = self.client.get(f"/api/auth/verify-email?token={token}")
        self.assertEqual(verified.status_code, 200)
        body = verified.get_data(as_text=True)
        self.assertIn("Email Verified", body)
        self.assert_themed(body)

    def test_expired_token_page_is_themed(self):
        response = self.client.get("/api/auth/verify-email?token=not-a-real-token")
        self.assertEqual(response.status_code, 400)
        body = response.get_data(as_text=True)
        self.assertIn("Invalid or Expired Link", body)
        self.assert_themed(body)

    def test_missing_token_page_is_themed_html_not_json(self):
        response = self.client.get("/api/auth/verify-email")
        self.assertEqual(response.status_code, 400)
        self.assertIn("text/html", response.headers["Content-Type"])
        self.assert_themed(response.get_data(as_text=True))

    def test_verification_pages_reuse_the_shared_theme_tokens(self):
        import app as app_module

        style = app_module._AUTH_PAGE_STYLE
        self.assertIn("#0b0b0f", style)
        self.assertIn("radial-gradient", style)
        self.assertIn("linear-gradient(90deg, #9333ea, #4f46e5)", style)


class AccessibilityTests(unittest.TestCase):
    def test_pages_declare_language_and_viewport(self):
        for page in PAGES:
            with self.subTest(page=page):
                html = read_page(page)
                self.assertRegex(html, r'<html[^>]*lang="en"')
                self.assertIn("name=\"viewport\"", html)
                self.assertIsNotNone(re.search(r"<title>.+?</title>", html), "missing <title>")

    def test_icon_only_controls_have_accessible_names(self):
        html = read_page("index.html")
        for marker in ('id="floating-nav-btn"', 'id="sidebar-collapse-btn"', 'id="mobile-menu-btn"',
                       'id="mobile-nav-close-btn"', 'id="floating-theme-btn"'):
            with self.subTest(control=marker):
                element = extract_element(html, marker)
                self.assertRegex(element, r"aria-label=")

    def test_account_menu_announces_itself_as_a_popup(self):
        button = extract_element(read_page("index.html"), 'id="floating-nav-btn"')
        self.assertIn('aria-haspopup="true"', button)

    def test_switch_controls_are_labelled(self):
        html = read_page("index.html")
        self.assertIn('id="theme-status-text"', html)


class ContentHonestyTests(unittest.TestCase):
    """The marketing copy must not overstate what the product does."""

    def setUp(self):
        self.landing = read_page("landing.html")

    def test_data_claims_link_to_the_real_policy(self):
        self.assertIn("/privacy.html", self.landing)

    def test_no_unqualified_end_to_end_encryption_claim(self):
        lowered = self.landing.lower()
        self.assertNotIn("never shared", lowered)
        self.assertNotIn("end-to-end encrypted", lowered.replace("not end-to-end encrypted", ""))

    def test_fake_testimonials_are_not_presented_as_reviews(self):
        lowered = self.landing.lower()
        self.assertNotIn("what users say", lowered)


if __name__ == "__main__":
    unittest.main()
