"""Set the private PM password interactively, revoking existing sessions.

Run inside the configured application container with an interactive terminal.
The password is never accepted as a command-line argument or printed.
"""

from getpass import getpass

from backend.access import AccessService
from backend.config import Settings
from backend.store import Store


def main():
    password = getpass("New PM password: ")
    if not 8 <= len(password) <= 256:
        raise SystemExit("Use between 8 and 256 characters.")
    if password != getpass("Repeat PM password: "):
        raise SystemExit("Passwords did not match; no change made.")
    config = Settings()
    AccessService(config, Store(config.db_path)).set_password(password)
    print("Private password hash saved; existing password sessions revoked.")


if __name__ == "__main__":
    main()
