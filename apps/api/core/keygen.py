"""`uv run python -m core.keygen`: prints fresh values for the calendar secrets.

Paste the two lines into `.env` (or the host's environment). To rotate, put the new
`CALENDAR_TOKEN_KEYS` entry FIRST and keep the old one after it: old tokens still
decrypt, and each is re-sealed with the new key the next time that connection's status
is successfully checked against Google. Drop the old key only once every row has moved
(`calendar_connections.token_key_id`).
"""

import base64
import secrets


def main() -> None:
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    print(f"CALENDAR_TOKEN_KEYS=k1:{key}")
    print(f"OAUTH_STATE_SECRET={secrets.token_urlsafe(48)}")


if __name__ == "__main__":
    main()
