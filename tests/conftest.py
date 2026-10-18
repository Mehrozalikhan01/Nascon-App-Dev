import os

# The API requires a grader-provided signing salt. Unit tests use an isolated
# value when the caller has not supplied one; this is never used by the app.
os.environ.setdefault("DAY1_SALT", "nascon-test-salt-only")
os.environ.setdefault("DAY2_SALT", "nascon-day2-test-salt")
