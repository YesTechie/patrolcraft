import os

# Tests sign in with a throwaway login; the app itself has no built-in default.
os.environ.setdefault("APP_USER", "admin")
os.environ.setdefault("APP_PASS", "admin")
os.environ.setdefault("APP_SECRET", "test-only-secret")
