# app.py
from flask import Flask, request, redirect, url_for, render_template, flash
import mysql.connector
from mysql.connector import errorcode
from datetime import datetime
import config
import threading
import subprocess
import os
import json
import requests
from flask import request, jsonify

# try import scanner module; fallback to subprocess execution if import fails
try:
    import scanner
except Exception:
    scanner = None

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

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

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

# ASIN list view / add / remove
@app.route('/asins', methods=['GET', 'POST'])
def asins():
    db = get_db()
    cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        asin = request.form.get('asin', '').strip()
        note = request.form.get('note', '').strip()
        if asin:
            try:
                cur.execute("INSERT INTO asins (asin, note) VALUES (%s, %s)", (asin, note))
                flash(f"ASIN {asin} hinzugefügt.", "success")
            except mysql.connector.IntegrityError:
                flash(f"ASIN {asin} existiert bereits.", "warning")
        return redirect(url_for('asins'))

    cur.execute("SELECT * FROM asins ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    db.close()
    return render_template('asin_list.html', asins=rows)

@app.route('/asins/toggle/<int:asin_id>')
def asin_toggle(asin_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE asins SET active = 1 - active WHERE id = %s", (asin_id,))
    cur.close()
    db.close()
    return redirect(url_for('asins'))

@app.route('/asins/delete/<int:asin_id>')
def asin_delete(asin_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM asins WHERE id = %s", (asin_id,))
    cur.close()
    db.close()
    flash("ASIN gelöscht.", "info")
    return redirect(url_for('asins'))

# Patterns view
@app.route('/patterns', methods=['GET', 'POST'])
def patterns():
    db = get_db()
    cur = db.cursor(dictionary=True)
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        pattern = request.form.get('pattern','').strip()
        flags = int(request.form.get('flags') or 0)
        desc = request.form.get('description','').strip()
        if name and pattern:
            cur.execute("INSERT INTO patterns (name, pattern, flags, description) VALUES (%s,%s,%s,%s)",
                        (name, pattern, flags, desc))
            flash("Pattern hinzugefügt.", "success")
        return redirect(url_for('patterns'))

    cur.execute("SELECT * FROM patterns ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    db.close()
    return render_template('patterns.html', patterns=rows)

@app.route('/patterns/toggle/<int:pid>')
def pattern_toggle(pid):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE patterns SET active = 1 - active WHERE id = %s", (pid,))
    cur.close()
    db.close()
    return redirect(url_for('patterns'))

@app.route('/patterns/delete/<int:pid>')
def pattern_delete(pid):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM patterns WHERE id = %s", (pid,))
    cur.close()
    db.close()
    flash("Pattern gelöscht.", "info")
    return redirect(url_for('patterns'))

# Results view
@app.route('/results')
def results():
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""SELECT r.*, a.asin AS asin, p.name AS pattern_name
                   FROM results r
                   JOIN asins a ON r.asin_id = a.id
                   JOIN patterns p ON r.pattern_id = p.id
                   ORDER BY r.created_at DESC LIMIT 200""")
    rows = cur.fetchall()
    cur.close()
    db.close()
    return render_template('results.html', results=rows)

# Manual run trigger (runs scan for one ASIN synchronously)
# WARNING: heavy; better use scanner.py via cron for production
@app.route('/run_one', methods=['POST'])
def run_one():
    asin = request.form.get('asin','').strip()
    if not asin:
        flash("Keine ASIN angegeben.", "warning")
        return redirect(url_for('index'))
    # Simple: call scanner logic in-process for this single ASIN
    from scanner import run_scan_for_asin
    try:
        count = run_scan_for_asin(asin)
        flash(f"Scan abgeschlossen: {count} Treffer.", "success")
    except Exception as e:
        flash(f"Fehler beim Scan: {e}", "danger")
    return redirect(url_for('index'))

@app.route("/run_scanner", methods=["POST"])
def run_scanner():
    """Startet den Scanner im Hintergrund-Thread."""
    def target():
        try:
            if scanner and hasattr(scanner, "run_full_scan"):
                # call module function (no limit)
                scanner.run_full_scan()
            else:
                # fallback: starte scanner.py als separaten Prozess
                subprocess_args = [sys.executable, os.path.join(os.path.dirname(__file__), "scanner.py")]
                subprocess.run(subprocess_args, check=False)
        except Exception as e:
            app.logger.exception("Scanner-Fehler: %s", e)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    flash("Scanner gestartet — läuft im Hintergrund.", "info")
    return redirect(url_for("index"))

@app.route("/scan_logs")
def scan_logs():
    """Zeige zuletzt gespeicherte Scan-Logs (scanned_at, asin, matches_count, note)."""
    try:
        # nutze die vorhandene DB-Helferfunktion aus scanner.py
        from scanner import get_db
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            SELECT sl.id, a.asin, sl.scanned_at, sl.matches_count, sl.note
            FROM scan_logs sl
            LEFT JOIN asins a ON sl.asin_id = a.id
            ORDER BY sl.scanned_at DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
        cur.close()
        db.close()
    except Exception as e:
        app.logger.exception("Fehler beim Laden der Scan-Logs: %s", e)
        rows = []
    return render_template("scan_logs.html", rows=rows)

HUGGINGFACE_API_TOKEN = os.environ.get("HUGGINGFACE_API_TOKEN")
USE_LOCAL_MODEL = os.environ.get("USE_LOCAL_MODEL") == "1"

def _build_prompt(positives, negatives, max_len=600):
    p = "Erzeuge eine Python-kompatible reguläre Ausdruck (ohne führende/abschließende /) der alle positiven Beispiele matched und keine der negativen Beispiele.\n\n"
    p += "Positive Beispiele:\n"
    for ex in positives:
        p += f"- {ex}\n"
    if negatives:
        p += "\nNegative Beispiele:\n"
        for ex in negatives:
            p += f"- {ex}\n"
    p += "\nAntwort als JSON mit Feldern: regex und flags (z.B. \"i\" oder \"\" wenn none). Gib nur das JSON zurück.\n"
    return p[:max_len]

def _call_hf_inference(prompt, model="google/flan-t5-large"):
    headers = {}
    if HUGGINGFACE_API_TOKEN:
        headers["Authorization"] = f"Bearer {HUGGINGFACE_API_TOKEN}"
    payload = {"inputs": prompt, "options": {"use_cache": False, "wait_for_model": True}}
    resp = requests.post(f"https://api-inference.huggingface.co/models/{model}", headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    # HF text models usually return list with 'generated_text' or plain string
    if isinstance(data, list) and "generated_text" in data[0]:
        return data[0]["generated_text"]
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"])
    if isinstance(data, str):
        return data
    # fallback to convert to str
    return json.dumps(data)

@app.route("/suggest_regex", methods=["POST"])
def suggest_regex():
    """
    Endpoint: expects JSON or form with 'positives' (newline-separated) and optional 'negatives'.
    Returns JSON: { "regex": "...", "flags": "", "error": null }
    """
    try:
        if request.is_json:
            payload = request.get_json()
            pos = payload.get("positives", [])
            neg = payload.get("negatives", [])
        else:
            pos = request.form.get("positives", "").strip().splitlines()
            neg = request.form.get("negatives", "").strip().splitlines()

        positives = [p.strip() for p in pos if p.strip()]
        negatives = [n.strip() for n in neg if n.strip()]
        if not positives:
            return jsonify({"regex": None, "flags": "", "error": "Keine positiven Beispiele angegeben"}), 400

        prompt = _build_prompt(positives, negatives)

        # Prefer HuggingFace Inference API if token present, otherwise try local transformer (optional)
        model_output = None
        try:
            model_output = _call_hf_inference(prompt)
        except Exception as e:
            # optional: try local transformers if configured
            if USE_LOCAL_MODEL:
                try:
                    from transformers import pipeline
                    pipe = pipeline("text2text-generation", model="google/flan-t5-small")
                    res = pipe(prompt, max_length=256)
                    model_output = res[0]["generated_text"]
                except Exception as e2:
                    return jsonify({"regex": None, "flags": "", "error": f"HF and local model failed: {e}; {e2}"}), 500
            else:
                return jsonify({"regex": None, "flags": "", "error": str(e)}), 500

        # try parse model_output as JSON
        regex = None
        flags = ""
        try:
            # models may return JSON or plain text; try to extract JSON first
            parsed = json.loads(model_output)
            regex = parsed.get("regex") or parsed.get("pattern") or parsed.get("regexp")
            flags = parsed.get("flags", "") or ""
        except Exception:
            # fallback: try to find pattern between first pair of backticks or quotes
            import re as _re
            m = _re.search(r"`([^`]+)`", model_output)
            if not m:
                m = _re.search(r'"([^"]+)"', model_output)
            if not m:
                m = _re.search(r"'([^']+)'", model_output)
            if m:
                regex = m.group(1)
            else:
                # last fallback: return whole text
                regex = model_output.strip()

        return jsonify({"regex": regex, "flags": flags, "error": None})
    except Exception as e:
        return jsonify({"regex": None, "flags": "", "error": str(e)}), 500
