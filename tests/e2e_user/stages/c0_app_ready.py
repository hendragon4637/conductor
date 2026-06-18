"""C0 — App ready.

Navigate to the Conductor UI and verify:
- The settings page loads.
- AionUi and Hermes Agent rows show "connected".
- Active execution backend row is visible.
"""

from playwright.sync_api import Page

UI_BASE_URL = "http://localhost:3090"


def run(page: Page) -> bool:
    """Verify the Conductor UI is ready with expected backends connected."""
    page.goto(f"{UI_BASE_URL}/#/settings")
    page.wait_for_selector("[data-testid='settings-table']", timeout=15000)

    # Check AionUi shows connected
    aionui_row = page.locator("[data-testid='settings-row-AionUi']")
    assert aionui_row.is_visible(), "AionUi row not visible"
    content = (aionui_row.text_content() or "").lower()
    assert "connected" in content, f"AionUi row does not show 'connected': {content}"

    # Check Hermes Agent shows connected
    hermes_row = page.locator("[data-testid='settings-row-Hermes Agent']")
    assert hermes_row.is_visible(), "Hermes Agent row not visible"
    content = (hermes_row.text_content() or "").lower()
    assert "connected" in content, f"Hermes row does not show 'connected': {content}"

    # Check Active execution backend row is visible
    backend_row = page.locator(
        "[data-testid='settings-row-Active execution backend']"
    )
    assert backend_row.is_visible(), "Active execution backend row not visible"

    return True
