# config.py
# Konfigurationsdatei — passe hier DB-Zugangsdaten an

DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "your_db_user"
DB_PASS = "your_db_password"
DB_NAME = "asinscanner"

# Scraper / Web config
HTTP_TIMEOUT = 20
USER_AGENT = "ASINScanner/1.0 (+https://yourdomain.example)"
REQUESTS_SLEEP = 2  # Sekunden Pause zwischen Requests (verringert Load)

# Website config
SECRET_KEY = "change_this_to_something_secret_and_random"

# Optional: Cron-run path (nur für Hinweise)
PYTHON_BIN = "/usr/bin/python3"
