import os
import subprocess
import time
import uuid

import pytest
import requests

pytest.importorskip("appium")
from appium import webdriver
from appium.options.android import UiAutomator2Options

APPIUM_URL = os.getenv("APPIUM_SERVER_URL", "http://localhost:4723")
APP_PACKAGE = os.getenv("ANDROID_APP_PACKAGE", "com.nascon.league.mobile_app")
APP_ACTIVITY = os.getenv("ANDROID_APP_ACTIVITY", ".MainActivity")


@pytest.fixture(scope="module")
def phone():
    try:
        status = requests.get(f"{APPIUM_URL}/status", timeout=2)
        ready = status.ok and status.json().get("value", {}).get("ready") is True
    except Exception:
        ready = False
    if not ready:
        pytest.skip(f"Appium 2 is not ready at {APPIUM_URL}")
    for port in (8080, 8888, 8889):
        subprocess.run(["adb", "reverse", f"tcp:{port}", f"tcp:{port}"], check=False, capture_output=True)
    options = UiAutomator2Options().load_capabilities({
        "platformName": "Android", "automationName": "UiAutomator2",
        "deviceName": os.getenv("ANDROID_DEVICE_NAME", "NaSCon Android"),
        "appPackage": APP_PACKAGE, "appActivity": APP_ACTIVITY,
        "noReset": True, "newCommandTimeout": 180,
        "uiautomator2ServerLaunchTimeout": 60000,
        "uiautomator2ServerInstallTimeout": 60000,
    })
    driver = webdriver.Remote(APPIUM_URL, options=options)
    yield driver
    driver.quit()


def _open_link(phone, url, expected_text):
    phone.execute_script("mobile: deepLink", {"url": url, "package": APP_PACKAGE})
    deadline = time.perf_counter() + 8.0
    while time.perf_counter() < deadline:
        if expected_text in phone.page_source:
            return
        time.sleep(0.1)
    pytest.fail(f"{url} did not open the expected native screen within deadline")


def test_match_entity_deep_link_opens_live_screen(phone):
    _open_link(phone, "nascon://entity/match-m1", "Match Live")


def test_costream_deep_link_opens_chat_screen(phone):
    _open_link(phone, "nascon://stream/cs-session-101", "Live chat")


def test_cross_surface_chat_reaches_phone_and_guest_browser(phone):
    import httpx
    from selenium import webdriver as selenium_webdriver
    from selenium.webdriver import ChromeOptions
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    api_url = os.getenv("API_BASE_URL", "http://localhost:8080")
    browser_url = os.getenv("SELENIUM_APP_BASE_URL", "http://api:8080")
    session_id = f"cs-session-{uuid.uuid4().hex[:8]}"
    session = httpx.post(f"{api_url}/audit/v1/costream/session", json={
        "session_id": session_id, "host_player_id": "player-alpha-1",
        "participants": ["player-alpha-1", "player-alpha-2"], "layout": "grid_2",
    }, timeout=5)
    assert session.status_code == 201
    _open_link(phone, f"nascon://stream/{session_id}", "Live chat")

    options = ChromeOptions()
    browser = selenium_webdriver.Remote(command_executor=os.getenv("SELENIUM_REMOTE_URL", "http://localhost:4444"), options=options)
    browser.get(f"{browser_url}/web/costream/{session_id}")
    login = httpx.post(f"{api_url}/api/v1/auth/login", json={"email": "viewer@nascon.test", "password": "Nascon@26"}, timeout=5)
    assert login.status_code == 200
    token = login.json()["access_token"]
    message = f"sync-{uuid.uuid4().hex[:8]}"
    sent = httpx.post(f"{api_url}/api/v1/chat", headers={"Authorization": f"Bearer {token}"}, json={
        "chat_room_id": f"chat-{session_id}", "text": message,
    }, timeout=5)
    assert sent.status_code == 200
    started = time.perf_counter()
    WebDriverWait(browser, 3).until(lambda driver: message in driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-message-list"]').text)
    deadline = started + 3
    while time.perf_counter() < deadline and message not in phone.page_source:
        time.sleep(0.1)
    browser.quit()
    assert message in phone.page_source, "The Appium client did not receive the browser's chat message within 3 seconds"
    assert time.perf_counter() - started <= 3
