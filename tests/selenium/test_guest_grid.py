import os
import time
import uuid

import pytest

selenium = pytest.importorskip("selenium")
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
import httpx

REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://localhost:4444")
APP_URL = os.getenv("SELENIUM_APP_BASE_URL", "http://api:8080")
API_URL = os.getenv("API_BASE_URL", "http://localhost:8080")


@pytest.fixture
def driver():
    capabilities = {"browserName": os.getenv("SELENIUM_BROWSER", "chrome")}
    from selenium.webdriver import ChromeOptions, FirefoxOptions
    options = FirefoxOptions() if capabilities["browserName"] == "firefox" else ChromeOptions()
    options.set_capability("browserName", capabilities["browserName"])
    try:
        instance = webdriver.Remote(command_executor=REMOTE_URL, options=options)
    except Exception as error:
        pytest.skip(f"Selenium Grid is not ready at {REMOTE_URL}: {error}")
    instance.set_window_size(1280, 900)
    yield instance
    instance.quit()


def test_qr_resolution_loads_without_login(driver):
    driver.get(f"{APP_URL}/q/m1-live")
    WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.CSS_SELECTOR, '[data-testid="guest-video-player"]')))
    assert "/web/matches/match-m1/live" in driver.current_url
    assert driver.find_element(By.CSS_SELECTOR, '[data-testid="live-score-banner"]').is_displayed()
    assert driver.find_element(By.CSS_SELECTOR, '[data-testid="progressive-auth-modal"]').get_attribute("aria-hidden") == "true"


def test_guest_chat_write_is_blocked_and_opens_progressive_modal(driver):
    driver.get(f"{APP_URL}/web/matches/match-m1/live")
    before = driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-message-list"]').text
    field = driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-input-field"]')
    field.send_keys("Guest must sign in to post")
    driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-send-btn"]').click()
    WebDriverWait(driver, 3).until(lambda browser: browser.find_element(By.CSS_SELECTOR, '[data-testid="progressive-auth-modal"]').get_attribute("aria-hidden") == "false")
    assert driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-message-list"]').text == before


def test_guest_cart_and_score_event_sync_within_500ms(driver):
    driver.get(f"{APP_URL}/web/matches/match-m1/live")
    driver.find_element(By.CSS_SELECTOR, '[data-testid="add-to-guest-cart-btn"]').click()
    assert driver.find_element(By.CSS_SELECTOR, '[data-testid="guest-cart-count"]').text == "1"
    initial = driver.find_element(By.CSS_SELECTOR, '[data-testid="live-score-banner"]').text
    result = httpx.post(f"{API_URL}/audit/v1/events", json={
        "event_id": f"selenium-{uuid.uuid4().hex[:12]}",
        "match_id": "match-m1", "type": "score",
        "payload": {"team_id": "club-alpha-2", "event_type": "goal"},
    }, timeout=3)
    assert result.status_code == 201
    started = time.perf_counter()
    WebDriverWait(driver, 0.49, poll_frequency=0.02).until(
        lambda browser: browser.find_element(By.CSS_SELECTOR, '[data-testid="live-score-banner"]').text != initial
    )
    assert time.perf_counter() - started < 0.5
