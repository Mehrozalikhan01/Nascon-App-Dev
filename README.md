# NaSCon '26 League & Club Operations

NaSCon '26 is a Flutter app and FastAPI demo backend for fixtures, match scoring, standings, club rosters, offline actions, and a mock merchandise checkout.

## Start the API stack

Docker Compose starts the FastAPI service, PostgreSQL 16, and Redis 7. Set a private competition salt in the shell; never commit it:

```bash
DAY1_SALT="$(openssl rand -hex 32)" docker compose up -d --build
```

Wait for `http://localhost:8080/health` to return `{"status":"ok"}`. The readiness check verifies that PostgreSQL is reachable and that the demo baseline marker has been written. The current domain services still keep their working data in process memory; PostgreSQL is not yet the source of truth for fixture, score, roster, store, or sync records.

Reset the demo state:

```bash
curl -X POST http://localhost:8080/audit/v1/reset \
  -H 'Content-Type: application/json' \
  -d '{"seed":42}'
```

## Demo sign-in

All five accounts use the competition's demo password, `Nascon@26`.

| Role | Email | Scope |
| --- | --- | --- |
| Platform Super-Admin | `admin@nascon.test` | Platform demo |
| League Organizer | `organizer@nascon.test` | `league-alpha-1` |
| Club Manager | `manager@nascon.test` | `club-alpha-1` |
| Player | `player@nascon.test` | `player-alpha-1` |
| General Viewer | `viewer@nascon.test` | Read-only demo account |

The app signs in through `/api/v1/auth/login`. Android stores the token encrypted with an Android Keystore key. These are public demo credentials, not production identities.

## Mobile app

The Flutter project is in [`mobile/`](mobile/). It includes NaSCon branding, a server-address setting, fixtures, live score refresh, standings, club information, store checkout, receipt details and Android PDF saving, roster/scoring forms, and a persistent offline action queue. On Android, cached screens and queued actions use SQLite; the access token is stored separately with Android Keystore encryption.

Use `http://10.0.2.2:8080` from the Android emulator. For a physical phone, enter the computer's LAN IP in Account. Public servers must use HTTPS. The checkout gateway is simulated and must not be used for real payment details.

Build and run from an installed Flutter SDK:

```bash
cd mobile
flutter pub get
flutter run
```

`mobile/release/app-release.apk` is retained from the repository. It has not been rebuilt for the latest source changes in this environment; verify a fresh Android build before installing or submitting it.

## Tests

```bash
./run_tests.sh
```

The suite includes endpoint checks and direct regressions. `COVERAGE.json` is the declared area coverage file; do not treat its values as verified unless the test script completed successfully on the target Python/Docker environment.

## Current implementation limits

- The Compose file currently starts only the API, PostgreSQL, and Redis. A MinIO service and a background worker are not implemented yet.
- The API's domain data is in memory. It is lost when the API process restarts, and multiple API replicas would not share state. PostgreSQL is currently used for readiness only.
- The competition audit endpoints intentionally do not require a login token except the RBAC probes. This follows the PDF's autograder contract; it is not a production authorization design.
- The UI has demo role-aware actions, but the API does not apply the five-role permission matrix to all domain endpoints.
- Android secure token storage and receipt saving are implemented. iOS token storage does not yet use Keychain.
- A fresh mobile build, on-device layout review, offline force-close walkthrough, and full Docker acceptance run still need verification on a machine with Flutter and a working Docker daemon.

This repository is a competition/demo implementation and is not ready for production data or real payments.
