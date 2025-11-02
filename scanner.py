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

# configure logger and debug mode driven by config.DEBUG (set DEBUG = 1 in config.py to enable)
DEBUG_MODE = bool(getattr(config, "DEBUG", 0) == 1 or getattr(config, "DEBUG", False))

logger = logging.getLogger("asinscanner.scanner")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)

# convenience for backward-compatible calls in the file
# replace existing root-logging calls with logger.*

def get_db():
    if DEBUG_MODE:
        logger.debug("Connecting to DB host=%s port=%s db=%s user=%s", config.DB_HOST, config.DB_PORT, config.DB_NAME, config.DB_USER)
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
    if DEBUG_MODE:
        logger.debug("Fetching URL %s with headers %s", url, {k: headers[k] for k in ("User-Agent",)})
    start = time.time()
    resp = requests.get(url, headers=headers, timeout=config.HTTP_TIMEOUT)
    elapsed = time.time() - start
    if DEBUG_MODE:
        logger.debug("Fetched %s status=%s elapsed=%.2fs content-length=%s", url, resp.status_code, elapsed, resp.headers.get("Content-Length"))
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
        t = desc.get_text(" ", strip=True)
        texts.append(t)
        if DEBUG_MODE:
            logger.debug("Extracted productDescription length=%d", len(t))
        for a in desc.find_all("a", href=True):
            hrefs.append(a['href'])

    # bullet points
    bullets = soup.select_one("#feature-bullets")
    if bullets:
        t = bullets.get_text(" ", strip=True)
        texts.append(t)
        if DEBUG_MODE:
            logger.debug("Extracted feature-bullets length=%d", len(t))
        for a in bullets.find_all("a", href=True):
            hrefs.append(a['href'])

    # product details
    detail = soup.select_one("#detailBullets_feature_div")
    if detail:
        t = detail.get_text(" ", strip=True)
        texts.append(t)
        if DEBUG_MODE:
            logger.debug("Extracted detailBullets length=%d", len(t))
        for a in detail.find_all("a", href=True):
            hrefs.append(a['href'])

    # full page text fallback
    full_text = soup.get_text(" ", strip=True)
    texts.append(full_text)
    if DEBUG_MODE:
        logger.debug("Full page text length=%d, hrefs_count=%d", len(full_text), len(hrefs))

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
            if DEBUG_MODE:
                logger.debug("Compiled pattern id=%s name=%s pattern=%s flags=%s", pid, name, pat, re_flags)
        except re.error as ex:
            logger.error("Invalid regex id %s (%s): %s", pid, name, ex)
    if DEBUG_MODE:
        logger.debug("Total compiled patterns: %d", len(compiled))
    return compiled

def run_scan_for_asin(asin):
    """Scan a single ASIN once. Returns number of matches inserted."""
    logger.info("Start scan for ASIN %s", asin) if not DEBUG_MODE else logger.debug("Start scan for ASIN %s", asin)
    db = get_db()
    cur = db.cursor()
    try:
        url, html = fetch_product_html(asin)
    except Exception as e:
        logger.exception("Fehler beim Abruf für %s: %s", asin, e)
        try:
            cur.close()
            db.close()
        except Exception:
            pass
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
            logger.error("Ungültiges Pattern id %s name %s", pid, name)
    if DEBUG_MODE:
        logger.debug("Loaded %d active patterns", len(compiled_patterns))

    matches_inserted = 0

    # Search in text
    for pid, name, cre in compiled_patterns:
        pattern_matches = 0
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
            pattern_matches += 1
            if DEBUG_MODE:
                logger.debug("Inserted match for ASIN %s pattern_id=%s matched_text=%s", asin, pid, matched_text[:200])

        if DEBUG_MODE and pattern_matches == 0:
            logger.debug("No text matches for ASIN %s pattern_id=%s", asin, pid)

    # Also check hrefs (each href string)
    for href in hrefs:
        for pid, name, cre in compiled_patterns:
            href_matches = 0
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
                href_matches += 1
                if DEBUG_MODE:
                    logger.debug("Inserted href match for ASIN %s pattern_id=%s matched_text=%s", asin, pid, matched_text[:200])
            if DEBUG_MODE and href_matches == 0:
                logger.debug("No href matches for ASIN %s pattern_id=%s on href=%s", asin, pid, href[:200])

    # update last_checked timestamp
    cur.execute("UPDATE asins SET last_checked = NOW() WHERE asin = %s", (asin,))

    # ensure we have asin_id for logging
    cur.execute("SELECT id FROM asins WHERE asin=%s", (asin,))
    row = cur.fetchone()
    asin_id = row[0] if row else None

    # write scan log (always record, auch wenn 0 Treffer)
    try:
        cur.execute(
            "INSERT INTO scan_logs (asin_id, matches_count, note) VALUES (%s, %s, %s)",
            (asin_id, matches_inserted, None)
        )
    except Exception as e:
        logger.exception("Fehler beim Schreiben des Scan-Logs für %s: %s", asin, e)

    cur.close()
    db.close()

    # Neuer Log: explizit "keine Treffer" protokollieren
    if matches_inserted == 0:
        logger.info("ASIN %s: keine Treffer gefunden.", asin)
    else:
        logger.info("ASIN %s gescannt, %d Treffer.", asin, matches_inserted)

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
    if DEBUG_MODE:
        logger.debug("Starting full scan for %d asins (limit=%s)", len(rows), limit)
    for r in rows:
        asin = r[0]
        try:
            if DEBUG_MODE:
                logger.debug("Scanning ASIN %s", asin)
            matched = run_scan_for_asin(asin)
            total += matched
        except Exception as e:
            logger.exception("Fehler beim Scannen von %s: %s", asin, e)
        time.sleep(config.REQUESTS_SLEEP)
    logger.info("Full scan beendet, insgesamt %d Treffer gefunden.", total)
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
