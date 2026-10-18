# NaSCon '26 League & Club Operations

NaSCon '26 is a Flutter app and FastAPI demo backend for fixtures, match scoring, standings, club rosters, offline actions, and a mock merchandise checkout.

## Start the API stack

Docker Compose starts FastAPI, PostgreSQL 16, Redis 7, MinIO, and the order-event worker. Put your exact `DAY1_SALT` from the NaSCon team slip in your local, untracked `.env` file. Do not generate a substitute competition salt or commit it. A separate `POSTGRES_PASSWORD` is optional; when blank, Compose uses `DAY1_SALT` for the internal database connection. Leave `DATABASE_URL` blank to use the database service in this Compose stack:

```bash
DATABASE_URL='' docker compose up -d --build
```

`DATABASE_URL` is optional. Leave it blank to connect the API to the Compose PostgreSQL service. If an existing local `.env` sets a different database URL, clear that value for this command or set it to the Compose database URL.

The API port binds to `127.0.0.1` by default. Wait for `http://localhost:8080/health` to return `{"status":"ok"}`. Readiness verifies PostgreSQL and the MinIO receipt bucket. PostgreSQL stores a versioned JSONB snapshot of the domain data after successful mutations and restores it on API startup. Redis queues paid-order events; the worker archives signed receipt PDFs in MinIO. The API remains a single-process demo design: the snapshot is not a substitute for relational transactions or safe multi-replica checkout.

For a physical phone on the same trusted LAN, bind the API to the host network interface and use the computer's LAN address in the app:

```bash
DATABASE_URL='' BIND_ADDRESS=0.0.0.0 docker compose up -d --build
```

Use this only on a trusted network and close the port when the demo is finished. Public deployment requires HTTPS, private secrets, and API authorization on all domain routes.

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

The Flutter project is in [`mobile/`](mobile/). It includes NaSCon branding, a server-address setting, fixture status and competition filters, live score refresh and match start/end controls, standings with head-to-head details, tenant-aware super-admin views, club profiles and roster history, self-service player profile updates, store checkout, receipt details and Android PDF saving, and a persistent per-account offline action queue with sync history. Android uses SQLite for local app data and Android Keystore for access tokens. iOS stores access tokens in Keychain; unsupported platforms keep tokens in memory only.

Use `http://10.0.2.2:8080` from the Android emulator. For a physical phone over Wi-Fi, enter the computer's LAN IP in Account. For a USB-connected phone with an authorized ADB connection, `adb reverse tcp:8080 tcp:8080` lets the app use `http://127.0.0.1:8080`. Public servers must use HTTPS. The checkout gateway is simulated and must not be used for real payment details.

Build and run from an installed Flutter SDK:

```bash
cd mobile
flutter pub get
flutter run
```

`mobile/release/app-release.apk` was rebuilt from the current Flutter source as an Android ARM64 release. Its package metadata reports minimum Android API 24 and target API 36. The build succeeded locally; installation and on-device review still require an authorized Android Debug Bridge device.

## Tests

```bash
./run_tests.sh
```

The suite includes endpoint checks and direct regressions. `COVERAGE.json` reports line coverage for the six backend service areas, measured with the current 18-test suite in the Python 3.12 API image. The measured values are below the specification's 85% coverage bonus threshold; expand the unit tests and regenerate the file before submission if pursuing that bonus.

## Current implementation limits

- Domain services use memory for request-time operations and persist a full JSONB snapshot after successful mutations. Startup restore and single-process restart survival were verified. Multi-replica concurrency and database-backed inventory transactions are not implemented.
- The order worker consumes Redis events idempotently and stores successful checkout PDFs in MinIO. Fulfilment and order shipping-state workflows are outside the current implementation.
- The Compose MinIO image is a pinned, source-built community image of the upstream release because upstream no longer publishes its community container. Keep this local to the demo network and review the image source and update cadence before any production deployment.
- The competition audit endpoints intentionally do not require a login token except the RBAC probes. This follows the PDF's autograder contract; it is not a production authorization design.
- The five-role authorization matrix is implemented for its dedicated RBAC probes; the mobile overview enforces tenant, league, and club scope, and player profile edits require an authorized account. Other audit endpoints remain open to preserve the competition contract.
- Android and iOS token storage use platform secure stores. Receipt PDF saving is implemented for Android; other platforms can view receipt details but do not have the native save-and-open flow.
- The Android ARM64 release APK builds successfully. This environment can identify a USB-connected phone but does not expose an authorized ADB device, so install, on-device layout review, and the offline force-close walkthrough remain unverified.
- Compose provides the PDF's PostgreSQL, Redis, MinIO, API, and worker services. A clean-machine one-command acceptance run still needs independent verification.
- Live score changes refresh by polling from the mobile app. Server-pushed WebSocket/SSE events and live pinned-product updates are not implemented.

This repository is a competition/demo implementation and is not ready for production data or real payments.
