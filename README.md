# NaSCon '26: Part I Multi-Sport League & Club Operations Platform

Production-style, multi-tenant sports league and club-operations platform combining rule-driven league management, fixture scheduling, club roster operations, offline-tolerant mobile synchronization, and a concurrency-safe in-app merchandise store.

## 1. Quick Start (Reproducible Docker Stack)

Brings up the entire backend platform, PostgreSQL, Redis, and workers on host port `8080`:

```bash
cp .env.example .env
# Ensure DAY1_SALT is set
docker compose up -d --build
```

Health check verification:
```bash
curl http://localhost:8080/health
# Returns: {"status":"ok"}
```

Reset state and seed baseline data:
```bash
curl -X POST http://localhost:8080/audit/v1/reset \
  -H "Content-Type: application/json" \
  -d '{"seed": 42}'
```

---

## 2. Five Seeded Demo Accounts (Section 8.3)

| Role | Email | Password | Scope / Permissions |
| :--- | :--- | :--- | :--- |
| **Platform Super-Admin** | `admin@nascon.test` | `Nascon@26` | Platform-wide administrative authority |
| **League Organizer** | `organizer@nascon.test` | `Nascon@26` | Owns `league-alpha-1` in `tenant-alpha` |
| **Club Manager** | `manager@nascon.test` | `Nascon@26` | Owns `club-alpha-1` in `tenant-alpha` |
| **Player** | `player@nascon.test` | `Nascon@26` | Bound to `player-alpha-1` in `club-alpha-1` |
| **General Viewer** | `viewer@nascon.test` | `Nascon@26` | Read-only access within `tenant-alpha` |

---

## 3. Automated Test Suite & Coverage

Run the complete automated test suite:
```bash
./run_tests.sh
```
Line coverage summary is reported in `COVERAGE.json`.

---

## 4. Mobile Application

Built Android APK is located at:
`mobile/release/app-release.apk`
Supports offline persistence (SQLite/Hive), background action queueing, and conflict synchronization.
