import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/services/api_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() async {
    debugDefaultTargetPlatformOverride = TargetPlatform.linux;
    SharedPreferences.setMockInitialValues({});
    ApiService.baseUrl = ApiService.defaultBaseUrl;
  });

  tearDown(() {
    debugDefaultTargetPlatformOverride = null;
  });

  test('keeps the Android emulator address after app initialization', () async {
    SharedPreferences.setMockInitialValues({
      'server_url': 'http://10.0.2.2:8080',
    });

    await ApiService.init();

    expect(ApiService.baseUrl, 'http://10.0.2.2:8080');
  });

  test('keeps a physical-device LAN address after app initialization', () async {
    await ApiService.setServerUrl('http://192.168.1.42:8080');
    ApiService.baseUrl = ApiService.defaultBaseUrl;

    await ApiService.init();

    expect(ApiService.baseUrl, 'http://192.168.1.42:8080');
  });
}
