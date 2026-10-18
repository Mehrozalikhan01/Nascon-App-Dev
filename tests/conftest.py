import os
import socket

# The API requires a grader-provided signing salt. Unit tests use an isolated
# value when the caller has not supplied one; this is never used by the app.
os.environ.setdefault("DAY1_SALT", "nascon-test-salt-only")
os.environ.setdefault("DAY2_SALT", "nascon-day2-test-salt")

def _check_db_reachability():
    db_url = os.environ.get("DATABASE_URL")
    if db_url is None and os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if db_url and "@" in db_url:
        host = db_url.split("@")[1].split(":")[0].split("/")[0]
        try:
            socket.gethostbyname(host)
        except Exception:
            os.environ["DATABASE_URL"] = ""

    redis_url = os.environ.get("REDIS_URL")
    if redis_url is None and os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.startswith("REDIS_URL="):
                    redis_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if redis_url and "//" in redis_url:
        rhost = redis_url.split("//")[1].split(":")[0].split("/")[0]
        try:
            socket.gethostbyname(rhost)
        except Exception:
            os.environ["REDIS_URL"] = ""

_check_db_reachability()

