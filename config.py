# config.py
# Konfigurationsdatei — passe hier DB-Zugangsdaten an
# für swm-db01

DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "asinscanner"
DB_PASS = "asinscanner"
DB_NAME = "asinscanner"

# Scraper / Web config
HTTP_TIMEOUT = 20
USER_AGENT = "ASINScanner/1.0 (+https://yourdomain.example)"
REQUESTS_SLEEP = 2  # Sekunden Pause zwischen Requests (verringert Load)

# Website config
SECRET_KEY = "change_this_to_something_secret_and_random"

# Optional: Cron-run path (nur für Hinweise)
PYTHON_BIN = "/usr/bin/python3"

# HuggingFace API (optional). Setze hier dein Token oder lasse es leer und setze die ENV-Variable HUGGINGFACE_API_TOKEN
HUGGINGFACE_API_TOKEN = ""  # z.B. "hf_..." — NICHT in öffentliches Repo commiten!
HF_MODEL = "google/flan-t5-large"
