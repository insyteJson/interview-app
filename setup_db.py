"""Connect the app to Supabase in one step.

Run in the app folder (with the .venv active):   python setup_db.py

You paste the "Session pooler" connection string from Supabase and type your database password (hidden). The script
- checks that it is the session pooler (IPv4, port 5432), not the direct connection (IPv6 only) or the transaction pooler,
- builds the URL the app needs (postgresql+psycopg2://, password safely encoded),
- connects once and creates the tables,
- writes DATABASE_URL into .streamlit/secrets.toml (other settings are kept),
- fills in the database line in consent.md.
Nothing is sent anywhere except the test connection to your own database.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, ".streamlit", "secrets.toml")
CONSENT = os.path.join(HERE, "consent.md")
CONSENT_PLACEHOLDER = "[CONFIRM: provider and region, e.g. Supabase, EU (Frankfurt)]"


def build_url(pasted: str, password: str) -> str:
    """Turn the Supabase session-pooler string into the SQLAlchemy URL. Raises ValueError with a readable message."""
    s = pasted.strip().strip('"').strip("'")
    if not re.match(r"^postgres(ql)?://", s):
        raise ValueError("This does not look like a connection string. It must start with postgresql://")
    # Parsed by hand: the pasted string contains "[YOUR-PASSWORD]", which urllib reads as an IPv6 address.
    rest = re.sub(r"^postgres(ql)?://", "", s)
    userinfo, _, hostpart = rest.rpartition("@")
    user = userinfo.split(":", 1)[0]
    m = re.match(r"^([^:/?]+)(?::(\d+))?(?:/([^?]*))?", hostpart)
    if not m:
        raise ValueError("Could not read the host from the connection string. Copy it again, unchanged.")
    host, port, db = m.group(1), int(m.group(2)) if m.group(2) else None, (m.group(3) or "postgres")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        raise ValueError("This is the DIRECT connection (IPv6 only, will not work from Streamlit Cloud). "
                         "In the Connect dialog choose 'Session pooler' instead.")
    if "pooler.supabase.com" not in host:
        raise ValueError(f"Unexpected host '{host}'. Copy the 'Session pooler' string from Supabase's Connect dialog.")
    if port == 6543:
        raise ValueError("Port 6543 is the TRANSACTION pooler. Choose 'Session pooler' (port 5432) instead.")
    if port != 5432:
        raise ValueError(f"Expected port 5432 (session pooler), found {port}.")
    if not user.startswith("postgres."):
        raise ValueError(f"Expected a user like 'postgres.<project-ref>', found '{user}'. Copy the string unchanged.")
    if not password:
        raise ValueError("The password is empty.")
    return f"postgresql+psycopg2://{user}:{quote(password, safe='')}@{host}:{port}/{db}"


REGIONS = {"eu-central-1": "EU (Frankfurt)", "eu-central-2": "EU (Zurich)", "eu-west-1": "EU (Ireland)",
           "eu-west-2": "EU (London)", "eu-west-3": "EU (Paris)", "eu-north-1": "EU (Stockholm)"}


def region_label(url: str) -> str:
    m = re.search(r"aws-\d+-([a-z]+-[a-z]+-\d+)\.pooler", url)
    return REGIONS.get(m.group(1), m.group(1)) if m else "region unknown"


def update_secrets(text: str, url: str) -> str:
    lines = [ln for ln in text.splitlines() if not re.match(r"^\s*#?\s*DATABASE_URL\s*=", ln)]
    lines.append(f'DATABASE_URL = "{url}"')
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    print(__doc__.split("\n\n")[0], "\n")
    pasted = input("1) Paste the Session pooler connection string and press Enter:\n> ")
    password = getpass.getpass("2) Type your Supabase DATABASE password (input is hidden) and press Enter:\n> ")
    try:
        url = build_url(pasted, password)
    except ValueError as exc:
        print(f"\nStopped: {exc}")
        return 1

    try:
        import sqlalchemy  # noqa: F401
        import psycopg2  # noqa: F401
    except ImportError:
        print("\nStopped: the app's Python environment is not active (sqlalchemy/psycopg2 missing).")
        print("Activate it first, then run the script again:")
        print("  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass")
        print("  & <path-to-your-.venv>\\Scripts\\Activate.ps1")
        return 1
    print("\nConnecting to Supabase and creating the tables ...")
    try:
        sys.path.insert(0, HERE)
        from storage import Store
        store = Store(url)
        n = len(store.list_sessions())
    except Exception as exc:  # show the essence, not a long traceback
        msg = str(exc).splitlines()[0][:300]
        print(f"\nConnection failed: {msg}")
        if "password authentication failed" in msg:
            print("-> Wrong database password. Reset it in Supabase: Project Settings > Database > Reset database password.")
        elif "not found" in msg or "tenant" in msg:
            print("-> The project reference is wrong. Copy the string again from the Connect dialog, unchanged.")
        elif "timeout" in msg.lower() or "could not translate host name" in msg:
            print("-> Network problem or the project is paused. Check that the project shows 'Healthy' in Supabase.")
        return 1
    print(f"Connected. Tables are ready ({n} sessions stored so far).")

    os.makedirs(os.path.dirname(SECRETS), exist_ok=True)
    old = open(SECRETS, encoding="utf-8").read() if os.path.exists(SECRETS) else ""
    with open(SECRETS, "w", encoding="utf-8") as f:
        f.write(update_secrets(old, url))
    print(f"Saved DATABASE_URL to {SECRETS}")

    if os.path.exists(CONSENT):
        c = open(CONSENT, encoding="utf-8").read()
        if CONSENT_PLACEHOLDER in c:
            with open(CONSENT, "w", encoding="utf-8") as f:
                f.write(c.replace(CONSENT_PLACEHOLDER, f"Supabase, {region_label(url)}"))
            print(f"Filled in the database line in consent.md (Supabase, {region_label(url)}).")

    print("\nDone. Restart the app (Ctrl+C, then: streamlit run app.py) and open http://localhost:8501/?admin=1")
    print("For Streamlit Cloud, paste the whole .streamlit/secrets.toml into the app's Secrets field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
