# scanner.py
import time
import re
import requests
from bs4 import BeautifulSoup
import mysql.connector
from mysql.connector import errorcode
from datetime import datetime
import config
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

def get_db():
    return mysql.connector.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASS,
        database=config.DB_NAME,
        autocommit=True,
        charset='utf8mb4'
    )

def fetch_product_html(asin):
    # Amazon product URL (regional could vary — adapt if needed)
    url = f"https://www.amazon.de/dp/{asin}"
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9,de;q=0.8"
    }
    resp = requests.get(url, headers=headers, timeout=config.HTTP_TIMEOUT)
    resp.raise_for_status()
    return url, resp.text

def extract_text_and_hrefs(html):
    soup = BeautifulSoup(html, "lxml")
    # Try to get the product description sections — fallbacks present
    texts = []
    hrefs = []

    # product description id
    desc = soup.select_one("#productDescription")
    if desc:
        texts.append(desc.get_text(" ", strip=True))
        for a in desc.find_all("a", href=True):
            hrefs.append(a['href'])

    # bullet points
    bullets = soup.select_one("#feature-bullets")
    if bullets:
        texts.append(bullets.get_text(" ", strip=True))
        for a in bullets.find_all("a", href=True):
            hrefs.append(a['href'])

    # product details
    detail = soup.select_one("#detailBullets_feature_div")
    if detail:
        texts.append(detail.get_text(" ", strip=True))
        for a in detail.find_all("a", href=True):
            hrefs.append(a['href'])

    # full page text fallback
    texts.append(soup.get_text(" ", strip=True))

    joined_text = "\n".join(texts)
    return joined_text, hrefs

def load_active_patterns(cursor):
    cursor.execute("SELECT id, name, pattern, flags FROM patterns WHERE active=1")
    entries = cursor.fetchall()
    compiled = []
    for e in entries:
        pid, name, pat, flags = e
        # flags is stored as integer bitmask of python re flags
        re_flags = 0
        if flags:
            re_flags = flags
        try:
            compiled_re = re.compile(pat, re_flags)
            compiled.append((pid, name, compiled_re))
        except re.error as ex:
            logging.error("Invalid regex id %s (%s): %s", pid, name, ex)
    return compiled

def run_scan_for_asin(asin):
    """Scan a single ASIN once. Returns number of matches inserted."""
    db = get_db()
    cur = db.cursor()
    try:
        url, html = fetch_product_html(asin)
    except Exception as e:
        logging.exception("Fehler beim Abruf für %s: %s", asin, e)
        cur.close()
        db.close()
        raise

    text, hrefs = extract_text_and_hrefs(html)

    # load patterns
    cur.execute("SELECT id, name, pattern, flags FROM patterns WHERE active=1")
    patterns = cur.fetchall()
    compiled_patterns = []
    for pid, name, pat, flags in patterns:
        try:
            compiled_patterns.append((pid, name, re.compile(pat, flags or 0)))
        except re.error:
            logging.error("Ungültiges Pattern id %s name %s", pid, name)

    matches_inserted = 0

    # Search in text
    for pid, name, cre in compiled_patterns:
        for m in cre.finditer(text):
            matched_text = m.group(0) if m.groups() == () else m.group(0)
            matched_group = None
            if m.groups():
                try:
                    matched_group = m.group(1)
                except Exception:
                    matched_group = None
            # insert result: find or create asin id
            cur.execute("SELECT id FROM asins WHERE asin=%s", (asin,))
            row = cur.fetchone()
            if row:
                asin_id = row[0]
            else:
                cur.execute("INSERT INTO asins (asin) VALUES (%s)", (asin,))
                asin_id = cur.lastrowid
            cur.execute("""
                INSERT INTO results (asin_id, pattern_id, matched_text, matched_group, source_url)
                VALUES (%s,%s,%s,%s,%s)
            """, (asin_id, pid, matched_text, matched_group, url))
            matches_inserted += 1

    # Also check hrefs (each href string)
    for href in hrefs:
        for pid, name, cre in compiled_patterns:
            for m in cre.finditer(href):
                matched_text = m.group(0)
                matched_group = None
                if m.groups():
                    try:
                        matched_group = m.group(1)
                    except Exception:
                        matched_group = None
                # ensure asin id
                cur.execute("SELECT id FROM asins WHERE asin=%s", (asin,))
                row = cur.fetchone()
                if row:
                    asin_id = row[0]
                else:
                    cur.execute("INSERT INTO asins (asin) VALUES (%s)", (asin,))
                    asin_id = cur.lastrowid
                cur.execute("""
                    INSERT INTO results (asin_id, pattern_id, matched_text, matched_group, source_url)
                    VALUES (%s,%s,%s,%s,%s)
                """, (asin_id, pid, matched_text, matched_group, url))
                matches_inserted += 1

    # update last_checked timestamp
    cur.execute("UPDATE asins SET last_checked = NOW() WHERE asin = %s", (asin,))

    cur.close()
    db.close()

    # Neuer Log: explizit "keine Treffer" protokollieren
    if matches_inserted == 0:
        logging.info("ASIN %s: keine Treffer gefunden.", asin)
    else:
        logging.info("ASIN %s gescannt, %d Treffer.", asin, matches_inserted)

    return matches_inserted

def run_full_scan(limit=None):
    """Scans all active ASINs. limit optional for testing."""
    db = get_db()
    cur = db.cursor()
    q = "SELECT asin FROM asins WHERE active=1 ORDER BY id"
    if limit:
        q += " LIMIT %s"
        cur.execute(q, (limit,))
    else:
        cur.execute(q)
    rows = cur.fetchall()
    cur.close()
    db.close()

    total = 0
    for r in rows:
        asin = r[0]
        try:
            matched = run_scan_for_asin(asin)
            total += matched
        except Exception as e:
            logging.exception("Fehler beim Scannen von %s: %s", asin, e)
        time.sleep(config.REQUESTS_SLEEP)
    logging.info("Full scan beendet, insgesamt %d Treffer gefunden.", total)
    return total

# If invoked as script, run full scan
if __name__ == "__main__":
    # optional: pass limit as CLI arg
    arg_limit = None
    if len(sys.argv) > 1:
        try:
            arg_limit = int(sys.argv[1])
        except ValueError:
            arg_limit = None
    run_full_scan(limit=arg_limit)
