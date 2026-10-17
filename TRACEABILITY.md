# TRACEABILITY MATRIX

Comprehensive mapping of all Functional Requirements (FR) and Non-Functional Requirements (NFR) to implementation source files, API routes, and automated test vectors.

| Req ID | Requirement Description | Implementation File(s) | API Route / Module | Automated Test Vector(s) |
| :--- | :--- | :--- | :--- | :--- |
| **FR-1.1** | Multi-Sport Taxonomy & Event-Sourced Scoring | `backend/app/services/sports.py`<br>`backend/app/services/scoring.py` | `POST /audit/v1/sports`<br>`POST /audit/v1/events`<br>`GET /audit/v1/score/{id}` | `tests/test_sports.py`<br>`tests/test_scoring.py` |
| **FR-1.2** | League & Tournament Setup | `backend/app/services/leagues.py` | `POST /audit/v1/leagues` | `tests/test_leagues.py` |
| **FR-1.3** | Match & Fixture Scheduling (RR, Swiss, Single/Double Elim) | `backend/app/services/fixtures.py` | `POST /audit/v1/fixtures` | `tests/test_fixtures.py` |
| **FR-1.4** | Automated Standings, H2H, NRR & Deterministic Lots | `backend/app/services/standings.py` | `GET /audit/v1/standings/{id}` | `tests/test_standings.py` |
| **FR-2.1** | Club Profiles & Multi-Tenant Data Isolation | `backend/app/services/clubs.py` | `POST /audit/v1/reset`<br>`/audit/v1/rbac/*` | `tests/test_rbac.py` |
| **FR-2.2** | Roster, Transfers, Loans & Deferred Transfers | `backend/app/services/roster.py` | `POST /audit/v1/roster/*`<br>`GET /audit/v1/roster/history/{id}` | `tests/test_roster.py` |
| **FR-2.3** | Concurrency-Safe Store, Inventory & Signed PDF | `backend/app/services/store.py`<br>`backend/app/services/pdf.py` | `POST /audit/v1/checkout`<br>`GET /audit/v1/inventory/{id}` | `tests/test_store.py` |
| **FR-5.1** | 5-Tier Role-Based Access Control (RBAC) & Security | `backend/app/services/rbac.py`<br>`backend/app/services/auth.py` | `POST /audit/v1/rbac-matrix`<br>`/audit/v1/rbac/*` | `tests/test_rbac.py` |
| **NFR-3** | Concurrency Correctness (20 Parallel Checkouts on 1 item) | `backend/app/services/store.py` | `POST /audit/v1/checkout` | `tests/test_concurrency.py` |
| **NFR-4** | Offline Tolerance & Synchronization | `backend/app/services/sync.py`<br>`mobile/lib/services/offline_sync.dart` | `POST /audit/v1/offline-sync` | `tests/test_sync.py` |
| **NFR-5** | Security, Rate Limiting & Input Validation | `backend/app/middleware/security.py` | `GET /api/v1/ping`<br>`/audit/v1/*` | `tests/test_security.py` |
| **NFR-7** | Reproducibility & Docker Deployment | `docker-compose.yml`<br>`backend/Dockerfile` | `GET /health` | `tests/test_health.py` |
