"""Shared helpers for capstone stage modules."""

import time
import traceback
from playwright.sync_api import Page


def wait_for_verdict(
    page: Page,
    row_testid: str,
    timeout: int = 180,
    poll_interval: int = 5,
) -> bool:
    """Poll a session row's verdict badge until it shows 'done'.

    Args:
        page: Playwright page object.
        row_testid: The data-testid of the session row (e.g. 'session-row-0').
        timeout: Maximum seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        True if 'done' was observed before timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            badge = page.locator(
                f"[data-testid='{row_testid}'] [data-testid='session-verdict-badge']"
            )
            if badge.is_visible():
                text = (badge.text_content() or "").strip().lower()
                if "done" in text:
                    return True
        except Exception:
            pass
        time.sleep(poll_interval)
    return False


def find_session_row_by_text(
    page: Page, search_text: str
) -> str | None:
    """Find a session row's data-testid by searching its text content.

    Iterates all elements with data-testid starting with 'session-row-'
    and returns the first whose visible text contains *search_text*.

    Returns the full data-testid value (e.g. 'session-row-2') or None.
    """
    rows = page.locator("[data-testid^='session-row-']")
    count = rows.count()
    for i in range(count):
        row = rows.nth(i)
        testid = row.get_attribute("data-testid")
        if testid and search_text.lower() in (row.text_content() or "").lower():
            return testid
    return None


def find_session_row_by_backend(
    page: Page, backend_name: str
) -> str | None:
    """Find a session row by its backend tag content.

    Returns the data-testid of the containing row (e.g. 'session-row-1').
    """
    rows = page.locator("[data-testid^='session-row-']")
    count = rows.count()
    for i in range(count):
        row = rows.nth(i)
        tag = row.locator("[data-testid='session-backend-tag']")
        if tag.is_visible() and backend_name.lower() in (tag.text_content() or "").lower():
            testid = row.get_attribute("data-testid")
            return testid
    return None


def find_session_row_by_class_label(
    page: Page, label_text: str
) -> str | None:
    """Find a session row by its class label content.

    Returns the data-testid of the containing row (e.g. 'session-row-1').
    """
    rows = page.locator("[data-testid^='session-row-']")
    count = rows.count()
    for i in range(count):
        row = rows.nth(i)
        label = row.locator("[data-testid='session-class-label']")
        if label.is_visible() and label_text.lower() in (label.text_content() or "").lower():
            testid = row.get_attribute("data-testid")
            return testid
    return None


def safe_run(fn, *args, **kwargs):
    """Run a stage function, returning (True|False, error_message)."""
    try:
        result = fn(*args, **kwargs)
        if result is True:
            return True, None
        if result is False:
            return False, "Stage returned False"
        return True, None
    except AssertionError as e:
        return False, f"Assertion failed: {e}"
    except Exception as e:
        tb = traceback.format_exc()
        return False, f"{type(e).__name__}: {e}\n{tb}"
