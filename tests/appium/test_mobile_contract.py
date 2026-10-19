"""Headless fallback checks for the Android deep-link contract."""

import xml.etree.ElementTree as ET
from pathlib import Path


ANDROID = "{http://schemas.android.com/apk/res/android}"
ROOT = Path(__file__).resolve().parents[2]


def test_android_manifest_exposes_browsable_nascon_deep_links():
    manifest_path = ROOT / "mobile/android/app/src/main/AndroidManifest.xml"
    manifest = ET.parse(manifest_path).getroot()
    activity = manifest.find(".//activity")
    assert activity is not None
    assert activity.get(ANDROID + "exported") == "true"
    filters = activity.findall("intent-filter")
    assert any(
        {item.get(ANDROID + "name") for item in filter_element.findall("category")}
        >= {"android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"}
        and any(data.get(ANDROID + "scheme") == "nascon" for data in filter_element.findall("data"))
        for filter_element in filters
    )


def test_flutter_routes_match_the_appium_deep_link_scenarios():
    source = (ROOT / "mobile/lib/main.dart").read_text()
    assert "MethodChannel('nascon.deep_links')" in source
    assert "scanned.host == 'entity'" in source
    assert "scanned.host == 'stream'" in source
    assert "Match Live" in source
    assert "Live chat" in source
