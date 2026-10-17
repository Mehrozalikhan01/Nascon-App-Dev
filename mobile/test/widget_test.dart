import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/main.dart';

void main() {
  testWidgets('NasconLeagueApp smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const NasconLeagueApp());
    expect(find.text("NaSCon League & Club Ops"), findsOneWidget);
  });
}
