import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/main.dart';

void main() {
  testWidgets('NasconLeagueApp smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const NasconLeagueApp());
    await tester.pumpAndSettle();
    expect(find.text("Welcome back"), findsOneWidget);
  });
}
