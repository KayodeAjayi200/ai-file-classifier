#!/usr/bin/env python3
"""AI File Classifier — full media management with tags and mobile transfer."""

import os, sqlite3, subprocess, sys, tempfile, json, socket, re, io as _io, threading, time, base64
from pathlib import Path
from flask import Flask, request, jsonify, send_file, abort, Response

try:
    from send2trash import send2trash as _send2trash
    HAS_TRASH = True
except ImportError:
    HAS_TRASH = False

try:
    import qrcode as _qrcode, io as _io
    HAS_QR = True
except ImportError:
    HAS_QR = False

DB_PATH      = Path(__file__).parent / "classifier.db"
PROJ_ROOT    = Path(__file__).parent
THUMB_CACHE  = PROJ_ROOT / "thumb_cache"
THUMB_CACHE.mkdir(exist_ok=True)
APP_VERSION  = "1.260510.11"   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor   # Major.YYMMDD.Minor
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024

PORT = 5050
TAG_COLORS = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#ec4899','#14b8a6','#f97316','#06b6d4','#84cc16']

DEFAULT_CATEGORIES = [
    ('selfie',          'Selfie',          'Memories',    'person taking photo of themselves'),
    ('group_photo',     'Group Photo',     'Memories',    'multiple people together'),
    ('family',          'Family',          'Memories',    'family moments/gatherings'),
    ('friends',         'Friends',         'Memories',    'social moments with friends'),
    ('celebration',     'Celebration',     'Memories',    'birthday, party, wedding, graduation, event'),
    ('travel',          'Travel',          'Memories',    'holiday, trip, sightseeing, landmarks'),
    ('food',            'Food',            'Personal',    'food, drinks, restaurants'),
    ('pets',            'Pets',            'Personal',    'animals, dogs, cats'),
    ('outdoors',        'Outdoors',        'Personal',    'nature, parks, scenery, streets'),
    ('home',            'Home',            'Personal',    'inside the house, furniture, rooms'),
    ('other',           'Other',           'Personal',    "anything that doesn't fit other categories"),
    ('screenshot',      'Screenshot',      'Screenshots', 'screenshot of a phone or computer screen'),
    ('screen_recording','Screen Recording','Screenshots', 'recorded screen content'),
    ('document',        'Document',        'Documents',   'ID, passport, form, letter, certificate'),
    ('receipt',         'Receipt',         'Documents',   'receipt, invoice, bill'),
    ('meme',            'Meme',            'Junk',        'meme or joke image'),
    ('whatsapp_junk',   'WhatsApp Junk',   'Junk',        'forwarded image, chain message'),
    ('social_save',     'Social Save',     'Junk',        'saved from Instagram, TikTok, Twitter etc'),
    ('wallpaper',       'Wallpaper',       'Junk',        'wallpaper or stock image'),
    ('junk',            'Junk',            'Junk',        'accidental, empty, blurry, or useless'),
]

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

LOCAL_IP = get_local_ip()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_set(path, **kwargs):
    conn = get_db()
    fields = [f"{k}=?" for k in kwargs]
    vals   = list(kwargs.values()) + [path]
    if fields:
        conn.execute(f"UPDATE files SET {', '.join(fields)} WHERE path=?", vals)
        conn.commit()
    conn.close()

def resolve_path(db_path):
    p = Path(db_path)
    if p.exists():
        return p
    conn = get_db()
    row  = conn.execute("SELECT moved_to FROM files WHERE path=?", (db_path,)).fetchone()
    conn.close()
    if row and row['moved_to']:
        m = Path(row['moved_to'])
        if m.exists():
            return m
    return None

def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS folders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            path         TEXT UNIQUE NOT NULL,
            display_name TEXT,
            created_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS folder_categories (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id     INTEGER REFERENCES folders(id) ON DELETE CASCADE,
            slug          TEXT NOT NULL,
            label         TEXT NOT NULL,
            output_folder TEXT NOT NULL,
            description   TEXT DEFAULT '',
            is_builtin    INTEGER DEFAULT 0,
            created_at    TEXT,
            UNIQUE(folder_id, slug)
        );
        CREATE TABLE IF NOT EXISTS tags (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT UNIQUE NOT NULL,
            color      TEXT DEFAULT '#3b82f6',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS file_tags (
            file_path TEXT NOT NULL,
            tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (file_path, tag_id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    for col, typ in [('source_folder','TEXT'),('file_size','INTEGER'),('device_status','TEXT'),
                      ('upload_source','TEXT'),('ai_caption','TEXT'),('ai_description','TEXT'),
                      ('ai_quality','TEXT'),('ai_processed','INTEGER DEFAULT 0'),
                      ('ocr_text','TEXT')]:
        try:
            conn.execute(f"ALTER TABLE files ADD COLUMN {col} {typ}")
        except Exception:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT UNIQUE NOT NULL,
            status      TEXT DEFAULT 'pending',
            priority    INTEGER DEFAULT 5,
            queued_at   TEXT DEFAULT (datetime('now')),
            started_at  TEXT,
            completed_at TEXT,
            error       TEXT,
            processing_ms INTEGER
        )
    """)
    # Add processing_ms column if missing (migration)
    try:
        conn.execute("ALTER TABLE ai_queue ADD COLUMN processing_ms INTEGER")
    except Exception:
        pass
    conn.commit()

def _seed_folder(conn, folder_path):
    row = conn.execute("SELECT id FROM folders WHERE path=?", (folder_path,)).fetchone()
    if row:
        fid = row[0]
    else:
        conn.execute(
            "INSERT INTO folders (path, display_name, created_at) VALUES (?, ?, datetime('now'))",
            (folder_path, Path(folder_path).name)
        )
        fid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for slug, label, ofol, desc in DEFAULT_CATEGORIES:
        conn.execute("""
            INSERT OR IGNORE INTO folder_categories
              (folder_id, slug, label, output_folder, description, is_builtin, created_at)
            VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
        """, (fid, slug, label, ofol, desc))
    conn.commit()
    return fid

def _auto_seed(conn):
    import os.path as osp
    rows = conn.execute("SELECT path, moved_to FROM files WHERE status='analyzed'").fetchall()
    if not rows:
        return
    clean = []
    for r in rows:
        p = str(r[0]) if r[0] else ""
        if p and "AI Review" not in p:
            clean.append(p)
        elif r[1] and "AI Review" in str(r[1]):
            mt  = str(r[1])
            idx = mt.find("AI Review")
            if idx > 0:
                cand = mt[:idx].rstrip("/\\")
                if cand not in clean:
                    clean.append(cand)
    if not clean:
        return
    common = clean[0]
    for p in clean[1:]:
        try:
            common = osp.commonpath([common, p])
        except ValueError:
            pass
    _seed_folder(conn, common)
    conn.execute("UPDATE files SET source_folder=? WHERE source_folder IS NULL", (common,))
    conn.commit()

def init_app():
    conn = get_db()
    _ensure_schema(conn)
    _auto_seed(conn)
    conn.close()

init_app()

# ─── AI TAGGING WORKER ────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434"
VISION_MODEL = "qwen3-vl:4b"
_AI_SUPPORTED_IMG = {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.tiff','.tif','.heic','.heif','.avif'}
_AI_SUPPORTED_VID = {'.mp4','.mov','.avi','.mkv','.wmv','.m4v','.3gp','.ts','.mts','.mxf','.flv','.webm'}

# Shared state for live queue progress
_ai_current_job = {'id': None, 'file': None, 'started': None}

def _enqueue_file(path: str, priority: int = 5):
    """Add a file path to the AI tagging queue (idempotent)."""
    try:
        conn = get_db()
        conn.execute("""INSERT OR IGNORE INTO ai_queue (file_path, status, priority, queued_at)
                        VALUES (?, 'pending', ?, datetime('now'))""", (path, priority))
        conn.commit()
        conn.close()
    except Exception:
        pass

def _extract_frame(video_path: str) -> bytes | None:
    """Extract a single representative frame from a video, return as JPEG bytes."""
    try:
        tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        tmp.close()
        subprocess.run([
            'ffmpeg', '-y', '-ss', '00:00:03', '-i', video_path,
            '-frames:v', '1', '-q:v', '2', tmp.name
        ], capture_output=True, timeout=30)
        with open(tmp.name, 'rb') as f:
            data = f.read()
        os.unlink(tmp.name)
        return data if len(data) > 100 else None
    except Exception:
        return None

_AI_PROMPT = """Analyse this image carefully and respond with ONLY valid JSON (no markdown, no extra text).
Return exactly this structure:
{
  "caption": "One concise sentence describing the scene",
  "description": "2-3 sentence detailed description for search",
  "tags": ["tag1", "tag2", ...],
  "quality": "excellent|good|fair|poor",
  "scene": "indoor|outdoor|screenshot|document",
  "people_count": 0,
  "colors": ["color1", "color2"],
  "mood": "happy|sad|calm|exciting|neutral",
  "objects": ["object1", "object2"],
  "location_type": "home|park|beach|city|office|restaurant|vehicle|unknown",
  "text_content": "Any readable text found in the image, transcribed verbatim. Empty string if none."
}
Tags should be specific and useful for search: include objects, activities, location, people descriptors, colors, occasions. Aim for 8-15 tags.
For text_content: transcribe ALL readable text including signs, labels, documents, receipts, messages, captions, overlays, watermarks, and on-screen text. Preserve the original wording."""

def _call_ollama_vision(image_bytes: bytes) -> dict:
    """Send image to Ollama vision model and return parsed JSON response."""
    import urllib.request
    b64 = base64.b64encode(image_bytes).decode()
    payload = json.dumps({
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": _AI_PROMPT, "images": [b64]}],
        "stream": False,
        "options": {"temperature": 0.1}
    }).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat",
                                  data=payload,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = json.loads(resp.read())
    text = raw.get("message", {}).get("content", "")
    # Strip markdown fences if present
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.I)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)

def _process_one(job: dict) -> bool:
    """Process a single AI queue job. Returns True on success."""
    path = job['file_path']
    ext  = Path(path).suffix.lower()
    _ai_current_job['id'] = job['id']
    _ai_current_job['file'] = Path(path).name
    _ai_current_job['started'] = time.time()
    conn = get_db()
    t0 = time.time()
    try:
        # Skip gracefully if file was moved/deleted since queuing
        if not os.path.exists(path):
            conn.execute("UPDATE ai_queue SET status='skipped', error='File not found on disk (may have been moved or deleted)', completed_at=datetime('now') WHERE id=?",
                         (job['id'],))
            conn.execute("DELETE FROM files WHERE path=?", (path,))
            conn.commit(); conn.close()
            _ai_current_job['id'] = None; _ai_current_job['file'] = None
            return True

        conn.execute("UPDATE ai_queue SET status='processing', started_at=datetime('now') WHERE id=?",
                     (job['id'],))
        conn.commit()

        # Load image bytes
        if ext in _AI_SUPPORTED_IMG:
            with open(path, 'rb') as f:
                img_bytes = f.read()
        elif ext in _AI_SUPPORTED_VID:
            img_bytes = _extract_frame(path)
        else:
            img_bytes = None

        if not img_bytes:
            conn.execute("UPDATE ai_queue SET status='skipped', completed_at=datetime('now') WHERE id=?",
                         (job['id'],))
            conn.execute("UPDATE files SET ai_processed=1 WHERE path=?", (path,))
            conn.commit(); conn.close()
            return True

        result = _call_ollama_vision(img_bytes)

        caption     = result.get('caption', '')[:500]
        description = result.get('description', '')[:1000]
        quality     = result.get('quality', 'unknown')[:20]
        ocr_text    = (result.get('text_content') or '').strip()[:4000]

        # Build tag list from multiple fields
        all_tags = []
        for field in ('tags', 'colors', 'objects'):
            all_tags.extend(result.get(field) or [])
        for field in ('scene', 'mood', 'location_type'):
            v = result.get(field, '')
            if v and v not in ('unknown', 'neutral'):
                all_tags.append(v)
        pc = result.get('people_count', 0)
        if pc == 1: all_tags.append('solo')
        elif pc == 2: all_tags.append('couple')
        elif pc and pc > 2: all_tags.append('group')

        # Deduplicate + clean
        seen = set(); clean_tags = []
        for t in all_tags:
            t = re.sub(r'[^a-z0-9 _-]', '', str(t).lower().strip())[:40]
            if t and t not in seen and len(t) > 1:
                seen.add(t); clean_tags.append(t)

        # Upsert tags and link to file
        used_colors = [c for i,c in enumerate(
            ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
             '#ec4899','#14b8a6','#f97316','#06b6d4','#84cc16']
        ) for _ in [1]] * 10  # enough colours
        TAG_COLORS_CYCLE = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
                            '#ec4899','#14b8a6','#f97316','#06b6d4','#84cc16']
        for i, tname in enumerate(clean_tags[:20]):
            color = TAG_COLORS_CYCLE[i % len(TAG_COLORS_CYCLE)]
            conn.execute("""INSERT OR IGNORE INTO tags (name, color, created_at)
                            VALUES (?, ?, datetime('now'))""", (tname, color))
            row = conn.execute("SELECT id FROM tags WHERE name=?", (tname,)).fetchone()
            if row:
                conn.execute("INSERT OR IGNORE INTO file_tags (file_path, tag_id) VALUES (?,?)",
                             (path, row[0]))

        conn.execute("""UPDATE files SET ai_caption=?, ai_description=?, ai_quality=?, ocr_text=?, ai_processed=1
                        WHERE path=?""", (caption, description, quality, ocr_text or None, path))
        conn.execute("UPDATE ai_queue SET status='done', completed_at=datetime('now'), processing_ms=? WHERE id=?",
                     (int((time.time()-t0)*1000), job['id'],))
        conn.commit(); conn.close()
        _ai_current_job['id'] = None; _ai_current_job['file'] = None
        return True

    except Exception as e:
        try:
            conn.execute("UPDATE ai_queue SET status='error', error=?, completed_at=datetime('now'), processing_ms=? WHERE id=?",
                         (str(e)[:300], int((time.time()-t0)*1000), job['id']))
            conn.commit()
        except Exception:
            pass
        conn.close()
        _ai_current_job['id'] = None; _ai_current_job['file'] = None
        return False

def _ai_worker_loop():
    """Daemon thread: continuously pick pending jobs and process them."""
    time.sleep(5)  # Let Flask fully start
    while True:
        try:
            conn = get_db()
            job  = conn.execute(
                "SELECT * FROM ai_queue WHERE status='pending' ORDER BY priority ASC, id ASC LIMIT 1"
            ).fetchone()
            conn.close()
            if job:
                _process_one(dict(job))
            else:
                time.sleep(8)
        except Exception:
            time.sleep(15)

def _start_ai_worker():
    t = threading.Thread(target=_ai_worker_loop, daemon=True, name='ai-tagger')
    t.start()

def _ai_scheduler_loop():
    """Daemon thread: check every 60s if a scheduled AI run should fire."""
    import datetime as _dt
    last_fired = None
    while True:
        try:
            conn = get_db()
            rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'ai_sched%'").fetchall()
            conn.close()
            cfg = {r['key']: r['value'] for r in rows}
            if cfg.get('ai_sched_enabled') == '1':
                now = _dt.datetime.now()
                h = int(cfg.get('ai_sched_hour', 2))
                m = int(cfg.get('ai_sched_minute', 0))
                day_key = now.strftime('%Y-%m-%d')
                if now.hour == h and now.minute == m and last_fired != day_key:
                    last_fired = day_key
                    # Enqueue all unprocessed files
                    c2 = get_db()
                    unproc = c2.execute(
                        "SELECT path FROM files WHERE ai_processed IS NULL OR ai_processed=0"
                    ).fetchall()
                    c2.close()
                    for r in unproc:
                        _enqueue_file(r['path'], priority=9)
        except Exception:
            pass
        time.sleep(60)

def _start_ai_scheduler():
    t = threading.Thread(target=_ai_scheduler_loop, daemon=True, name='ai-scheduler')
    t.start()

_start_ai_worker()
_start_ai_scheduler()

# ─── ROUTES ──────────────────────────────────────────────────────────────────

@app.route('/api/search')
def search():
    q        = request.args.get('q','').strip()
    category = request.args.get('category','')
    action   = request.args.get('action','')
    folder   = request.args.get('folder','')
    tag      = request.args.get('tag','')
    sort_by  = request.args.get('sort','recent')
    page     = int(request.args.get('page',1))
    per_page = int(request.args.get('per_page',60))
    uploaded = request.args.get('uploaded','')

    where, args = ["f.status='analyzed'"], []
    if uploaded:
        where.append("f.upload_source IS NOT NULL AND f.upload_source != ''")
    if category:
        where.append("f.category=?"); args.append(category)
    if action:
        where.append("f.action=?"); args.append(action)
    if folder:
        where.append("f.source_folder=?"); args.append(folder)
    if tag:
        where.append("EXISTS(SELECT 1 FROM file_tags ft JOIN tags t ON ft.tag_id=t.id WHERE ft.file_path=f.path AND t.name=?)")
        args.append(tag)
    if q:
        tag_hits  = re.findall(r'#(\w+)', q)
        plain_q   = re.sub(r'#\w+','',q).strip()
        for tm in tag_hits:
            where.append("EXISTS(SELECT 1 FROM file_tags ft JOIN tags t ON ft.tag_id=t.id WHERE ft.file_path=f.path AND LOWER(t.name)=LOWER(?))")
            args.append(tm)
        if plain_q:
            # Natural language: extract date hints
            year_m   = re.search(r'\b(20\d{2})\b', plain_q)
            month_m  = re.search(r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b', plain_q, re.I)
            season_m = re.search(r'\b(spring|summer|autumn|fall|winter)\b', plain_q, re.I)

            # Keyword expansion map
            _NL_MAP = {
                'dog':'dog pet animal puppy','cat':'cat pet animal kitten','baby':'baby child infant toddler',
                'birthday':'birthday celebration cake party','wedding':'wedding celebration bride groom',
                'beach':'beach ocean sea sand coast water',
                'food':'food eating meal restaurant drink',
                'sunset':'sunset sunrise sky clouds golden',
                'selfie':'selfie portrait face solo',
                'travel':'travel trip holiday vacation journey',
                'car':'car vehicle road drive street',
                'family':'family home kids children','friends':'friends group social party',
                'nature':'nature outdoor trees green grass forest','night':'night dark evening city lights',
                'snow':'snow winter cold ice white','rain':'rain wet outdoor weather',
                'screenshot':'screenshot screen document text','blur':'blurry low_quality poor',
                'me':'selfie solo portrait face',
            }
            expanded_terms = set()
            for word in plain_q.lower().split():
                expanded_terms.add(word)
                if word in _NL_MAP:
                    expanded_terms.update(_NL_MAP[word].split())

            lq = f"%{plain_q.lower()}%"
            conds = ["LOWER(f.path) LIKE ?","LOWER(f.category) LIKE ?",
                     "LOWER(COALESCE(f.ai_caption,'')) LIKE ?",
                     "LOWER(COALESCE(f.ai_description,'')) LIKE ?",
                     "LOWER(COALESCE(f.issues,'')) LIKE ?",
                     "LOWER(COALESCE(f.ocr_text,'')) LIKE ?",
                     "EXISTS(SELECT 1 FROM file_tags ft2 JOIN tags t2 ON ft2.tag_id=t2.id WHERE ft2.file_path=f.path AND LOWER(t2.name) LIKE ?)"]
            args_block = [lq]*7

            # Additional expanded tag checks
            for term in list(expanded_terms)[:8]:
                tl = f"%{term}%"
                conds.append("(LOWER(COALESCE(f.ai_caption,'')) LIKE ? OR EXISTS(SELECT 1 FROM file_tags ft3 JOIN tags t3 ON ft3.tag_id=t3.id WHERE ft3.file_path=f.path AND LOWER(t3.name) LIKE ?))")
                args_block += [tl, tl]

            where.append(f"({' OR '.join(conds)})")
            args.extend(args_block)

            # Date filters
            if year_m:
                where.append("f.path LIKE ?"); args.append(f"%{year_m.group(1)}%")
            if month_m:
                MONTHS = {'jan':'January','feb':'February','mar':'March','apr':'April',
                          'may':'May','jun':'June','jul':'July','aug':'August',
                          'sep':'September','oct':'October','nov':'November','dec':'December'}
                ml = month_m.group(1).lower()[:3]
                where.append("LOWER(f.path) LIKE ?"); args.append(f"%{MONTHS.get(ml,ml).lower()}%")
            if season_m:
                s = season_m.group(1).lower()
                season_months = {'spring':['March','April','May'],'summer':['June','July','August'],
                                 'autumn':['September','October','November'],'fall':['September','October','November'],
                                 'winter':['December','January','February']}
                sm = season_months.get(s,[])
                if sm:
                    month_or = " OR ".join(["LOWER(f.path) LIKE ?"]*len(sm))
                    where.append(f"({month_or})")
                    args.extend([f"%{m.lower()}%" for m in sm])

    where_str = " AND ".join(where)
    order = {'recent':'f.rowid DESC','oldest':'f.rowid ASC','name_az':'f.path ASC','name_za':'f.path DESC',
             'size_big':'f.file_size DESC','size_sml':'f.file_size ASC','conf':'CAST(f.confidence AS REAL) DESC'
             }.get(sort_by,'f.rowid DESC')

    conn  = get_db()
    total = conn.execute(f"SELECT COUNT(*) FROM files f WHERE {where_str}", args).fetchone()[0]
    rows  = conn.execute(f"""
        SELECT f.*,
               GROUP_CONCAT(t.name, '|||') AS tag_names,
               GROUP_CONCAT(t.color,'|||') AS tag_colors,
               GROUP_CONCAT(t.id,   '|||') AS tag_ids
        FROM files f
        LEFT JOIN file_tags ft ON ft.file_path = f.path
        LEFT JOIN tags t ON ft.tag_id = t.id
        WHERE {where_str}
        GROUP BY f.path ORDER BY {order}
        LIMIT ? OFFSET ?
    """, args+[per_page,(page-1)*per_page]).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        if d.get('tag_names'):
            ns  = d['tag_names'].split('|||')
            cs  = (d['tag_colors'] or '').split('|||')
            ids = (d['tag_ids']    or '').split('|||')
            d['tags'] = [{'name':n,'color':c,'id':int(i)} for n,c,i in zip(ns,cs,ids) if n]
        else:
            d['tags'] = []
        d.pop('tag_names',None); d.pop('tag_colors',None); d.pop('tag_ids',None)
        results.append(d)
    return jsonify({'total':total,'page':page,'per_page':per_page,'results':results})


@app.route('/api/categories')
def categories():
    folder = request.args.get('folder','')
    conn = get_db()
    if folder:
        rows = conn.execute("""
            SELECT fc.id, fc.slug AS category, fc.label, fc.output_folder,
                   fc.description, fc.is_builtin, COUNT(fi.rowid) AS n
            FROM folder_categories fc
            JOIN folders fo ON fc.folder_id=fo.id AND fo.path=?
            LEFT JOIN files fi ON fi.category=fc.slug AND fi.source_folder=fo.path AND fi.status='analyzed'
            GROUP BY fc.id ORDER BY fc.is_builtin DESC, n DESC, fc.label
        """, (folder,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM files WHERE status='analyzed' GROUP BY category ORDER BY n DESC"
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/tags', methods=['GET'])
def list_tags():
    conn = get_db()
    rows = conn.execute("""
        SELECT t.id, t.name, t.color, COUNT(ft.file_path) AS n
        FROM tags t LEFT JOIN file_tags ft ON ft.tag_id=t.id
        GROUP BY t.id ORDER BY n DESC, t.name
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/tags/popular')
def popular_tags():
    limit = int(request.args.get('limit', 30))
    conn = get_db()
    rows = conn.execute("""
        SELECT t.id, t.name, t.color, COUNT(ft.file_path) AS n
        FROM tags t JOIN file_tags ft ON ft.tag_id=t.id
        GROUP BY t.id HAVING n > 0
        ORDER BY n DESC, t.name
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/tags', methods=['POST'])
def create_tag():
    data  = request.get_json(force=True)
    name  = data.get('name','').strip().lower().replace(' ','_')
    color = data.get('color','')
    if not name:
        return jsonify({'ok':False,'error':'name required'}), 400
    conn = get_db()
    cnt  = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    if not color:
        color = TAG_COLORS[cnt % len(TAG_COLORS)]
    try:
        conn.execute("INSERT INTO tags (name,color,created_at) VALUES (?,?,datetime('now'))", (name,color))
        conn.commit()
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({'ok':True,'id':nid,'name':name,'color':color})
    except Exception as e:
        conn.close()
        return jsonify({'ok':False,'error':str(e)}), 400


@app.route('/api/tags/<int:tid>', methods=['DELETE'])
def delete_tag(tid):
    conn = get_db()
    conn.execute("DELETE FROM tags WHERE id=?", (tid,))
    conn.commit(); conn.close()
    return jsonify({'ok':True})


@app.route('/api/file-tags', methods=['POST'])
def add_file_tags():
    data      = request.get_json(force=True)
    paths     = data.get('paths') or ([data['path']] if data.get('path') else [])
    tag_ids   = list(data.get('tag_ids',[]))
    tag_names = data.get('tag_names',[])
    conn = get_db()
    for name in tag_names:
        name = name.strip().lower().replace(' ','_')
        if not name: continue
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            tag_ids.append(row[0])
        else:
            cnt   = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            color = TAG_COLORS[cnt % len(TAG_COLORS)]
            conn.execute("INSERT INTO tags (name,color,created_at) VALUES (?,?,datetime('now'))", (name,color))
            conn.commit()
            tag_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    for path in paths:
        for tid in tag_ids:
            try:
                conn.execute("INSERT OR IGNORE INTO file_tags (file_path,tag_id) VALUES (?,?)", (path,tid))
            except Exception:
                pass
    conn.commit(); conn.close()
    return jsonify({'ok':True})


@app.route('/api/file-tags', methods=['DELETE'])
def remove_file_tags():
    data  = request.get_json(force=True)
    paths = data.get('paths') or ([data['path']] if data.get('path') else [])
    tid   = data.get('tag_id')
    conn  = get_db()
    for path in paths:
        if tid:
            conn.execute("DELETE FROM file_tags WHERE file_path=? AND tag_id=?", (path,tid))
        else:
            conn.execute("DELETE FROM file_tags WHERE file_path=?", (path,))
    conn.commit(); conn.close()
    return jsonify({'ok':True})


@app.route('/api/stats')
def stats():
    folder = request.args.get('folder','')
    conn   = get_db()
    base   = "FROM files WHERE status='analyzed'"
    extra, eargs = (" AND source_folder=?", [folder]) if folder else ("", [])
    if folder:
        total  = conn.execute(f"SELECT COUNT(*) {base}{extra}", eargs).fetchone()[0]
        by_act = dict(conn.execute(f"SELECT action, COUNT(*) {base}{extra} GROUP BY action", eargs).fetchall())
    else:
        total  = conn.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
        by_act = dict(conn.execute(f"SELECT action, COUNT(*) {base} GROUP BY action").fetchall())
    ntags    = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    nfolders = conn.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
    IMG_LIKE = " OR ".join(f"LOWER(path) LIKE '%.{e}'" for e in ['jpg','jpeg','png','gif','bmp','webp','tiff','tif','heic','heif','avif'])
    VID_LIKE = " OR ".join(f"LOWER(path) LIKE '%.{e}'" for e in ['mp4','mov','avi','mkv','wmv','m4v','3gp','ts','mts','mxf','flv','webm'])
    nimages = conn.execute(f"SELECT COUNT(*) FROM files WHERE status='analyzed' AND ({IMG_LIKE})").fetchone()[0]
    nvideos = conn.execute(f"SELECT COUNT(*) FROM files WHERE status='analyzed' AND ({VID_LIKE})").fetchone()[0]
    conn.close()
    return jsonify({'total':total,'tags':ntags,'tag_count':ntags,'folders':nfolders,'images':nimages,'videos':nvideos,**by_act})


@app.route('/api/folders', methods=['GET'])
def list_folders():
    conn = get_db()
    rows = conn.execute("""
        SELECT f.id, f.path, f.display_name, COUNT(fi.rowid) AS n
        FROM folders f LEFT JOIN files fi ON fi.source_folder=f.path AND fi.status='analyzed'
        GROUP BY f.id ORDER BY f.created_at
    """).fetchall()
    conn.close()
    result = []
    for r in rows:
        p      = Path(r['path'])
        avail  = p.exists()
        drive  = str(p)[:3].upper() if len(str(p)) >= 3 else str(p)
        # Heuristic: non-C drive → likely external/removable
        is_ext = drive[0].upper() not in ('C',) if drive else False
        # Count subfolders (categories/month dirs) — just 1 level
        try:
            subs = [x.name for x in p.iterdir() if x.is_dir()] if avail else []
        except Exception:
            subs = []
        result.append({**dict(r), 'available': avail, 'is_external': is_ext,
                       'drive': drive, 'subfolders': subs[:20]})
    return jsonify(result)


@app.route('/api/folder-tree')
def folder_tree():
    """Return folder children for the folder-tree view on mobile."""
    path = request.args.get('path','').strip()
    conn = get_db()
    if not path:
        # Return registered root folders with has_children + file count
        rows = conn.execute("""
            SELECT f.path, f.display_name, COUNT(fi.rowid) AS n
            FROM folders f LEFT JOIN files fi ON fi.source_folder=f.path AND fi.status='analyzed'
            GROUP BY f.id ORDER BY f.path
        """).fetchall()
        conn.close()
        out = []
        for r in rows:
            p = Path(r['path'])
            try:
                hc = p.exists() and any(x.is_dir() for x in p.iterdir())
            except Exception:
                hc = False
            out.append({'path': r['path'], 'name': r['display_name'] or p.name,
                        'file_count': r['n'], 'has_children': hc})
        return jsonify(out)
    # Subdirectories of a given path
    p = Path(path)
    if not p.exists() or not p.is_dir():
        conn.close()
        return jsonify([])
    try:
        dirs = sorted([x for x in p.iterdir() if x.is_dir() and not x.name.startswith('.')],
                      key=lambda x: x.name.lower())
    except Exception:
        conn.close()
        return jsonify([])
    out = []
    for d in dirs[:100]:
        sp_fwd = str(d).replace('\\', '/')
        sp_bwd = str(d).replace('/', '\\')
        cnt = conn.execute(
            "SELECT COUNT(*) FROM files WHERE (path LIKE ? OR path LIKE ?) AND status='analyzed'",
            (sp_fwd + '/%', sp_bwd + '\\%')
        ).fetchone()[0]
        try:
            hc = any(x.is_dir() for x in d.iterdir())
        except Exception:
            hc = False
        out.append({'path': str(d), 'name': d.name, 'file_count': cnt, 'has_children': hc})
    conn.close()
    return jsonify(out)


@app.route('/api/folders', methods=['POST'])
def create_folder():
    data = request.get_json(force=True)
    path = data.get('path','').strip()
    if not path:
        return jsonify({'ok':False,'error':'path required'}), 400
    conn = get_db()
    _ensure_schema(conn)
    fid = _seed_folder(conn, path)
    conn.close()
    return jsonify({'ok':True,'id':fid})


@app.route('/api/folders/<int:fid>/categories', methods=['GET'])
def get_folder_cats(fid):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM folder_categories WHERE folder_id=? ORDER BY is_builtin DESC, label", (fid,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/folders/<int:fid>/categories', methods=['POST'])
def add_folder_cat(fid):
    data  = request.get_json(force=True)
    label = data.get('label','').strip()
    slug  = data.get('slug','').strip().lower().replace(' ','_')
    ofol  = data.get('output_folder','').strip()
    desc  = data.get('description','').strip()
    if not label or not slug or not ofol:
        return jsonify({'ok':False,'error':'label, slug, output_folder required'}), 400
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO folder_categories
              (folder_id,slug,label,output_folder,description,is_builtin,created_at)
            VALUES (?,?,?,?,?,0,datetime('now'))
        """, (fid,slug,label,ofol,desc))
        conn.commit()
        nid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return jsonify({'ok':True,'id':nid,'slug':slug,'label':label})
    except Exception as e:
        conn.close()
        return jsonify({'ok':False,'error':str(e)}), 400


@app.route('/api/categories/<int:cid>', methods=['DELETE'])
def delete_cat(cid):
    conn = get_db()
    row  = conn.execute("SELECT is_builtin FROM folder_categories WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close(); return jsonify({'ok':False,'error':'Not found'}), 404
    if row['is_builtin']:
        conn.close(); return jsonify({'ok':False,'error':'Cannot delete a built-in category'}), 400
    conn.execute("DELETE FROM folder_categories WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return jsonify({'ok':True})


@app.route('/api/update', methods=['POST'])
def update_files():
    data     = request.get_json(force=True)
    paths    = data.get('paths') or ([data['path']] if data.get('path') else [])
    category = data.get('category')
    action   = data.get('action')
    move     = data.get('move', False)
    if not paths:
        return jsonify({'ok':False,'error':'No paths'}), 400
    for path in paths:
        kwargs = {}
        if category is not None: kwargs['category'] = category
        if action   is not None: kwargs['action']   = action
        if kwargs: db_set(path, **kwargs)
    if move and len(paths)==1:
        try:
            sys.path.insert(0, str(PROJ_ROOT))
            from src.organizer import Organizer
            from src.database  import Database
            db  = Database(str(PROJ_ROOT/"classifier.db"))
            rec = db.get_result(paths[0])
            if rec:
                moved = rec.get('moved_to') or ''
                output_root = str(Path(moved).parent.parent) if moved else str(Path(paths[0]).parent/"AI Review")
                org = Organizer(output_root, dry_run=False)
                new_path = org.organize_one(rec)
                if new_path:
                    db.update_moved_to(paths[0], new_path)
                    return jsonify({'ok':True,'moved_to':new_path})
        except Exception as e:
            return jsonify({'ok':False,'error':str(e)}), 500
    return jsonify({'ok':True,'count':len(paths)})


@app.route('/api/delete', methods=['POST'])
def delete_files():
    data      = request.get_json(force=True)
    paths     = data.get('paths') or ([data['path']] if data.get('path') else [])
    permanent = data.get('permanent', False)
    results   = []
    for path in paths:
        p = resolve_path(path)
        try:
            if p:
                if permanent:   p.unlink()
                elif HAS_TRASH: _send2trash(str(p))
                else:
                    td = p.parent.parent / "Deleted"
                    td.mkdir(parents=True, exist_ok=True)
                    p.rename(td / p.name)
            db_set(path, status='deleted', action='deleted')
            results.append({'path':path,'ok':True})
        except Exception as e:
            results.append({'path':path,'ok':False,'error':str(e)})
    return jsonify({'results':results,'deleted':sum(1 for r in results if r['ok'])})


@app.route('/api/device-status', methods=['POST'])
def device_status():
    data   = request.get_json(force=True)
    paths  = data.get('paths',[])
    status = data.get('status','')
    if not paths or not status:
        return jsonify({'ok':False,'error':'paths and status required'}), 400
    conn = get_db()
    for p in paths: conn.execute("UPDATE files SET device_status=? WHERE path=?", (status,p))
    conn.commit(); conn.close()
    return jsonify({'ok':True,'count':len(paths)})


@app.route('/api/open')
def open_path():
    path = request.args.get('path','')
    typ  = request.args.get('type','file')
    if not path: return jsonify({'ok':False,'error':'No path'}), 400
    p = resolve_path(path)
    if not p: return jsonify({'ok':False,'error':'File not found'}), 404
    try:
        if typ=='reveal': subprocess.Popen(['explorer','/select,',str(p)])
        elif typ=='folder': os.startfile(str(p if p.is_dir() else p.parent))
        else: os.startfile(str(p))
        return jsonify({'ok':True})
    except Exception as e:
        return jsonify({'ok':False,'error':str(e)}), 500


TAILSCALE_EXE = r'C:\Program Files\Tailscale\tailscale.exe'

@app.route('/api/health')
def health():
    return jsonify({'ok': True})


@app.route('/api/network')
def network_info():
    tailscale_ip = None
    for ts_cmd in [TAILSCALE_EXE, 'tailscale']:
        try:
            r = subprocess.run([ts_cmd,'ip','-4'], capture_output=True, text=True, timeout=3)
            if r.returncode==0:
                tailscale_ip = r.stdout.strip()
                break
        except Exception:
            continue
    return jsonify({
        'local_ip':      LOCAL_IP,
        'port':          PORT,
        'local_url':     f'http://{LOCAL_IP}:{PORT}',
        'mobile_url':    f'http://{LOCAL_IP}:{PORT}/mobile',
        'tailscale_ip':  tailscale_ip,
        'tailscale_url': f'http://{tailscale_ip}:{PORT}' if tailscale_ip else None,
        'has_qr':        HAS_QR,
    })


@app.route('/qr.png')
def qr_code():
    if not HAS_QR: abort(404)
    url = request.args.get('url', f'http://{LOCAL_IP}:{PORT}/mobile')
    qr = _qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = _io.BytesIO()
    img.save(buf,'PNG')
    buf.seek(0)
    return Response(buf.read(), mimetype='image/png')


def _extract_media_date(path: Path):
    """Return a datetime for the media file using EXIF, ffprobe, filename, or mtime."""
    import datetime as _dt
    EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime
    VID_EXTS = {'.mp4','.mov','.avi','.mkv','.webm','.m4v','.wmv','.flv','.3gp','.ts','.mts','.m2ts'}

    # 1. EXIF for images
    if path.suffix.lower() not in VID_EXTS:
        try:
            from PIL import Image, ExifTags
            with Image.open(str(path)) as img:
                exif = img._getexif() or {}
                for tag in EXIF_DATE_TAGS:
                    val = exif.get(tag)
                    if val:
                        return _dt.datetime.strptime(val[:19], '%Y:%m:%d %H:%M:%S')
        except Exception:
            pass

    # 2. ffprobe for videos (and fallback for images without EXIF)
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_entries', 'format_tags=creation_time', str(path)],
            capture_output=True, text=True, timeout=10)
        data = json.loads(r.stdout or '{}')
        ct = (data.get('format',{}).get('tags') or {}).get('creation_time','')
        if ct:
            return _dt.datetime.fromisoformat(ct.replace('Z','+00:00').split('+')[0])
    except Exception:
        pass

    # 3. Filename patterns: IMG_20260510_..., VID_20260510, Screenshot_20260510-..., 2026-05-10...
    m = re.search(r'(\d{4})[_\-](\d{2})[_\-](\d{2})', path.name)
    if m:
        try:
            return _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 4. File modification time (last resort)
    return _dt.datetime.fromtimestamp(path.stat().st_mtime)


@app.route('/upload', methods=['POST'])
def upload_file():
    import datetime as _dt
    conn    = get_db()
    setting = conn.execute("SELECT value FROM settings WHERE key='upload_folder'").fetchone()
    first   = conn.execute("SELECT path FROM folders ORDER BY id LIMIT 1").fetchone()
    conn.close()

    if setting:
        base_upload = Path(setting['value'])
    elif first:
        base_upload = Path(first['path']) / "Phone Uploads"
    else:
        base_upload = PROJ_ROOT / "uploads"

    # Device / person name (sanitised for use as a folder name)
    raw_device  = request.form.get('device_name', '').strip() or 'Phone'
    device_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', raw_device)[:64]

    if 'file' not in request.files:
        return jsonify({'ok':False,'error':'No file field'}), 400

    uploaded = []
    for f in request.files.getlist('file'):
        if not f.filename: continue
        fname = re.sub(r'[<>:"/\\|?*]','_', f.filename)

        # Save to a temp location first so we can read its metadata
        tmp = base_upload / device_name / 'tmp'
        tmp.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp / fname
        f.save(str(tmp_path))

        # Determine date folder from metadata
        media_dt  = _extract_media_date(tmp_path)
        month_dir = media_dt.strftime('%B %Y')   # e.g. "May 2026"
        year_str  = str(media_dt.year)

        # Sub-folder by media type within the month folder
        _IMAGE_EXTS = {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.tiff','.tif','.heic','.heif','.avif'}
        _VIDEO_EXTS = {'.mp4','.mov','.avi','.mkv','.wmv','.m4v','.3gp','.ts','.mts','.mxf','.flv','.webm'}
        _ext = tmp_path.suffix.lower()
        if _ext in _IMAGE_EXTS:
            media_type_dir = 'Photos'
        elif _ext in _VIDEO_EXTS:
            media_type_dir = 'Videos'
        else:
            media_type_dir = 'Files'

        dest_dir = base_upload / device_name / year_str / month_dir / media_type_dir
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / fname
        ctr  = 1
        while dest.exists():
            dest = dest_dir / f"{tmp_path.stem}_{ctr}{tmp_path.suffix}"; ctr+=1
        tmp_path.rename(dest)

        # Clean up empty tmp dir if possible
        try: tmp.rmdir()
        except Exception: pass

        size  = dest.stat().st_size
        conn2 = get_db()
        conn2.execute("""
            INSERT OR IGNORE INTO files
              (path, status, action, file_type, file_size, upload_source, category)
            VALUES (?, 'analyzed', 'review', ?, ?, ?, 'other')
        """, (str(dest), dest.suffix.lower().lstrip('.'), size, device_name))
        conn2.commit()
        if first:
            conn2.execute("UPDATE files SET source_folder=? WHERE path=?", (first['path'], str(dest)))
            conn2.commit()
        conn2.close()
        _enqueue_file(str(dest), priority=1)  # high priority for fresh uploads
        uploaded.append({'name': fname, 'path': str(dest), 'size': size,
                         'device': device_name, 'month': month_dir})

    return jsonify({'ok':True,'uploaded':uploaded,'count':len(uploaded)})


@app.route('/api/ai-queue', methods=['GET'])
def ai_queue_status():
    conn = get_db()
    row = conn.execute("""
        SELECT
          COUNT(*) as total,
          SUM(status='pending')    as pending,
          SUM(status='processing') as processing,
          SUM(status='done')       as done,
          SUM(status='error')      as errors,
          SUM(status='skipped')    as skipped
        FROM ai_queue
    """).fetchone()
    next_job = conn.execute(
        "SELECT file_path FROM ai_queue WHERE status='pending' ORDER BY priority ASC, id ASC LIMIT 1"
    ).fetchone()
    conn.close()
    d = dict(row)
    d['next'] = next_job['file_path'].split('\\')[-1] if next_job else None
    # Live current job info
    cur = _ai_current_job
    if cur['file']:
        elapsed = int((time.time() - cur['started']) * 1000) if cur['started'] else 0
        d['current_file'] = cur['file']
        d['current_elapsed_ms'] = elapsed
    else:
        d['current_file'] = None
        d['current_elapsed_ms'] = 0
    return jsonify(d)


@app.route('/api/ai-queue/enqueue', methods=['POST'])
def ai_queue_enqueue():
    """Manually enqueue a file or all unprocessed files."""
    data = request.json or {}
    path = data.get('path')
    conn = get_db()
    if path:
        _enqueue_file(path, priority=2)
        queued = 1
    else:
        rows = conn.execute(
            "SELECT path FROM files WHERE ai_processed IS NULL OR ai_processed=0"
        ).fetchall()
        for r in rows:
            _enqueue_file(r['path'], priority=5)
        queued = len(rows)
    conn.close()
    return jsonify({'ok': True, 'queued': queued})


@app.route('/api/check-duplicates', methods=['POST'])
def check_duplicates():
    files = request.json or []
    conn = get_db()
    duplicates = []
    for item in files:
        name = item.get('name', '')
        size = int(item.get('size', -1))
        if not name or size < 0:
            continue
        rows = conn.execute(
            "SELECT path, file_size FROM files WHERE file_size=?", (size,)
        ).fetchall()
        matches = [dict(r) for r in rows
                   if r['path'].replace('\\', '/').split('/')[-1] == name]
        if matches:
            duplicates.append({'name': name, 'size': size, 'matches': matches})
    conn.close()
    return jsonify({'duplicates': duplicates})


@app.route('/api/folders/<int:fid>', methods=['DELETE'])
def delete_folder(fid):
    conn = get_db()
    conn.execute("DELETE FROM folders WHERE id=?", (fid,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/api/ollama/status')
def ollama_status():
    import urllib.request as _ur
    from config import OLLAMA_HOST, DEFAULT_MODEL
    host = OLLAMA_HOST
    try:
        r = _ur.urlopen(f"{host}/api/tags", timeout=3)
        data = json.loads(r.read())
        raw_models = data.get('models') or []
        models = [{'name': m['name'], 'active': m['name'] == DEFAULT_MODEL} for m in raw_models]
        version = None
        try:
            rv = _ur.urlopen(f"{host}/api/version", timeout=2)
            version = json.loads(rv.read()).get('version')
        except Exception:
            pass
        return jsonify({'ok': True, 'connected': True, 'models': models, 'version': version, 'current_model': DEFAULT_MODEL})
    except Exception as e:
        return jsonify({'ok': False, 'connected': False, 'models': [], 'error': str(e)})


@app.route('/api/activity')
def activity_log():
    limit = int(request.args.get('limit', 50))
    conn  = get_db()
    rows  = conn.execute("""
        SELECT path, category, action, file_type, ai_processed, ai_caption, file_size,
               upload_source, device_status
        FROM files WHERE status='analyzed'
        ORDER BY rowid DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ai-queue/clear-errors', methods=['POST'])
def ai_queue_clear_errors():
    conn = get_db()
    conn.execute("DELETE FROM ai_queue WHERE status='error'")
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/api/ai-queue/enqueue-folder', methods=['POST'])
def ai_queue_enqueue_folder():
    """Enqueue all unprocessed files in a specific folder."""
    data = request.json or {}
    folder_id = data.get('folder_id')
    folder_path = data.get('folder_path')
    conn = get_db()
    if folder_id:
        row = conn.execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
        folder_path = row['path'] if row else None
    if not folder_path:
        conn.close()
        return jsonify({'ok': False, 'error': 'Folder not found'}), 404
    # Get all files in this folder not yet AI-processed
    rows = conn.execute(
        "SELECT path FROM files WHERE (ai_processed IS NULL OR ai_processed=0) AND source_folder=?",
        (folder_path,)
    ).fetchall()
    if not rows:
        # Fallback: files whose path starts with folder_path
        rows = conn.execute(
            "SELECT path FROM files WHERE (ai_processed IS NULL OR ai_processed=0) AND path LIKE ?",
            (folder_path.replace('%','%%') + '%',)
        ).fetchall()
    conn.close()
    for r in rows:
        _enqueue_file(r['path'], priority=3)
    return jsonify({'ok': True, 'queued': len(rows), 'folder': folder_path})


@app.route('/api/ai-analytics')
def ai_analytics():
    conn = get_db()
    # Overall stats
    stats = conn.execute("""
        SELECT
          SUM(status='done') as done,
          SUM(status='error') as errors,
          SUM(status='skipped') as skipped,
          AVG(CASE WHEN status='done' AND processing_ms IS NOT NULL THEN processing_ms END) as avg_ms,
          MAX(CASE WHEN status='done' AND processing_ms IS NOT NULL THEN processing_ms END) as max_ms,
          MIN(CASE WHEN status='done' AND processing_ms IS NOT NULL THEN processing_ms END) as min_ms,
          COUNT(*) as total
        FROM ai_queue
    """).fetchone()
    # Per-day counts (last 14 days)
    days = conn.execute("""
        SELECT DATE(completed_at) as day, COUNT(*) as n
        FROM ai_queue
        WHERE status='done' AND completed_at IS NOT NULL
          AND completed_at >= DATE('now','-14 days')
        GROUP BY day ORDER BY day
    """).fetchall()
    # Top categories
    cats = conn.execute("""
        SELECT category, COUNT(*) as n FROM files
        WHERE category IS NOT NULL AND category != 'uncategorized' AND ai_processed=1
        GROUP BY category ORDER BY n DESC LIMIT 8
    """).fetchall()
    # Recent errors
    errors = conn.execute("""
        SELECT file_path, error, queued_at FROM ai_queue
        WHERE status='error' ORDER BY id DESC LIMIT 10
    """).fetchall()
    conn.close()
    d = dict(stats)
    d['per_day'] = [{'day': r['day'], 'n': r['n']} for r in days]
    d['top_categories'] = [{'cat': r['category'], 'n': r['n']} for r in cats]
    d['recent_errors'] = [{'file': r['file_path'].split('\\')[-1].split('/')[-1],
                            'error': r['error'], 'at': r['queued_at']} for r in errors]
    return jsonify(d)


@app.route('/api/ai-schedule', methods=['GET'])
def get_ai_schedule():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'ai_sched%'").fetchall()
    conn.close()
    d = {r['key']: r['value'] for r in rows}
    return jsonify({
        'enabled': d.get('ai_sched_enabled', '0') == '1',
        'hour': int(d.get('ai_sched_hour', '2')),
        'minute': int(d.get('ai_sched_minute', '0')),
        'mode': d.get('ai_sched_mode', 'daily'),
    })


@app.route('/api/ai-schedule', methods=['POST'])
def save_ai_schedule():
    data = request.json or {}
    conn = get_db()
    pairs = [
        ('ai_sched_enabled', '1' if data.get('enabled') else '0'),
        ('ai_sched_hour', str(data.get('hour', 2))),
        ('ai_sched_minute', str(data.get('minute', 0))),
        ('ai_sched_mode', data.get('mode', 'daily')),
    ]
    for k, v in pairs:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/api/ai-queue/jobs')
def ai_queue_jobs():
    limit  = int(request.args.get('limit', 50))
    status = request.args.get('status', '')
    conn   = get_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM ai_queue WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ai_queue ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/scan', methods=['POST'])
def scan_folder_route():
    from config import SUPPORTED_IMAGES, SUPPORTED_VIDEOS
    data = request.get_json(force=True) or {}
    folder_id = data.get('folder_id')
    conn = get_db()
    if folder_id:
        row = conn.execute("SELECT path FROM folders WHERE id=?", (folder_id,)).fetchone()
        folders = [row['path']] if row else []
    else:
        folders = [r['path'] for r in conn.execute("SELECT path FROM folders").fetchall()]
    conn.close()
    total_new = 0
    exts = SUPPORTED_IMAGES | SUPPORTED_VIDEOS | {'.pdf','.docx','.txt','.zip'}
    for folder in folders:
        p = Path(folder)
        if not p.exists():
            continue
        for f in p.rglob('*'):
            if f.is_file() and f.suffix.lower() in exts:
                conn2 = get_db()
                existing = conn2.execute("SELECT id FROM files WHERE path=?", (str(f),)).fetchone()
                if not existing:
                    conn2.execute(
                        """INSERT INTO files (path, filename, file_type, size_bytes, status, category, action, source_folder)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (str(f), f.name, f.suffix.lower().lstrip('.') or 'file',
                         f.stat().st_size, 'analyzed', 'uncategorized', 'uploaded',
                         folder)
                    )
                    conn2.commit()
                    total_new += 1
                conn2.close()
    # Clean up stale DB entries (files that no longer exist on disk) within scanned folders
    conn3 = get_db()
    stale_removed = 0
    for folder in folders:
        rows_all = conn3.execute("SELECT path FROM files WHERE path LIKE ?",
                                 (folder.replace('%','%%') + '%',)).fetchall()
        for row in rows_all:
            if not os.path.exists(row['path']):
                conn3.execute("DELETE FROM files WHERE path=?", (row['path'],))
                stale_removed += 1
    if stale_removed:
        conn3.commit()
    conn3.close()
    return jsonify({'ok': True, 'scanned': total_new, 'stale_removed': stale_removed})


@app.route('/api/clean-stale', methods=['POST'])
def clean_stale_files():
    """Remove DB entries and queue jobs for files that no longer exist on disk."""
    conn = get_db()
    all_paths = conn.execute("SELECT path FROM files").fetchall()
    removed = 0
    for row in all_paths:
        if not os.path.exists(row['path']):
            conn.execute("DELETE FROM files WHERE path=?", (row['path'],))
            conn.execute("DELETE FROM ai_queue WHERE file_path=?", (row['path'],))
            removed += 1
    # Also remove orphaned queue entries with no matching files row
    conn.execute("""DELETE FROM ai_queue WHERE status='error'
                    AND file_path NOT IN (SELECT path FROM files)""")
    if removed:
        conn.commit()
    conn.close()
    return jsonify({'ok': True, 'removed': removed})



def delete_tag_by_id(tid):
    conn = get_db()
    conn.execute("DELETE FROM file_tags WHERE tag_id=?", (tid,))
    conn.execute("DELETE FROM tags WHERE id=?", (tid,))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/api/tags/<int:tid>', methods=['PUT'])
def update_tag(tid):
    data  = request.get_json(force=True)
    name  = data.get('name','').strip().lower()
    color = data.get('color','').strip()
    conn  = get_db()
    if name:
        conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tid))
    if color:
        conn.execute("UPDATE tags SET color=? WHERE id=?", (color, tid))
    conn.commit(); conn.close()
    return jsonify({'ok': True})


@app.route('/download')
def download_file():
    path = request.args.get('path','')
    p    = resolve_path(path)
    if not p or not p.is_file(): abort(404)
    # Auto-mark as loaded on device when downloaded
    conn = get_db()
    conn.execute("UPDATE files SET device_status='loaded' WHERE path=? AND (device_status IS NULL OR device_status='offloaded')", (path,))
    conn.commit(); conn.close()
    return send_file(str(p), as_attachment=True, download_name=p.name)


@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return jsonify({r['key']:r['value'] for r in rows})


@app.route('/api/settings', methods=['POST'])
def save_settings_route():
    data = request.get_json(force=True)
    conn = get_db()
    for k, v in data.items():
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k,v))
    conn.commit(); conn.close()
    return jsonify({'ok':True})


@app.route('/stream')
def stream_file():
    """Byte-range aware media streaming — required for video playback on mobile."""
    path = request.args.get('path', '')
    p = resolve_path(path)
    if not p or not p.is_file():
        abort(404)
    ext_lower = p.suffix.lower()
    mime_map = {
        '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime',
        '.avi': 'video/x-msvideo', '.mkv': 'video/x-matroska',
        '.webm': 'video/webm', '.flv': 'video/x-flv', '.wmv': 'video/x-ms-wmv',
        '.3gp': 'video/3gpp', '.ts': 'video/mp2t', '.mts': 'video/mp2t',
        '.m2ts': 'video/mp2t', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4',
        '.aac': 'audio/aac', '.wav': 'audio/wav', '.ogg': 'audio/ogg',
        '.flac': 'audio/flac',
    }
    mime = mime_map.get(ext_lower, 'application/octet-stream')
    file_size = p.stat().st_size
    range_header = request.headers.get('Range', '')
    if not range_header:
        headers = {'Content-Length': str(file_size), 'Accept-Ranges': 'bytes',
                   'Content-Type': mime}
        def _full():
            with open(str(p), 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk: break
                    yield chunk
        return Response(_full(), 200, headers=headers, mimetype=mime)
    # Parse Range: bytes=start-end
    try:
        byte_range = range_header.replace('bytes=', '')
        parts = byte_range.split('-')
        start = int(parts[0]) if parts[0] else 0
        end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
    except Exception:
        abort(416)
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        abort(416)
    length = end - start + 1
    def _partial():
        with open(str(p), 'rb') as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(65536, remaining))
                if not data: break
                remaining -= len(data)
                yield data
    return Response(
        _partial(), 206, mimetype=mime,
        headers={
            'Content-Range': f'bytes {start}-{end}/{file_size}',
            'Content-Length': str(length),
            'Accept-Ranges': 'bytes',
        }
    )


@app.route('/img')
def serve_image():
    path = request.args.get('path','')
    p    = resolve_path(path)
    if not p or not p.is_file(): abort(404)
    mime = {'.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png','.gif':'image/gif',
            '.bmp':'image/bmp','.webp':'image/webp','.tiff':'image/tiff','.tif':'image/tiff',
            '.heic':'image/heic','.heif':'image/heif','.avif':'image/avif'
            }.get(p.suffix.lower(),'application/octet-stream')
    return send_file(str(p), mimetype=mime)


_THUMB_IMG_EXTS = {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.tiff','.tif','.heic','.heif','.avif'}

@app.route('/thumb')
def media_thumb():
    """Return a cached 400px-wide JPEG thumbnail for any image or video."""
    path = request.args.get('path','')
    p    = resolve_path(path)
    if not p or not p.is_file(): abort(404)
    import hashlib as _hl
    cache_key = _hl.md5(str(p).encode()).hexdigest()
    cached = THUMB_CACHE / f"{cache_key}.jpg"
    if cached.exists() and cached.stat().st_size > 0:
        return send_file(str(cached), mimetype='image/jpeg')
    ext = p.suffix.lower()
    if ext in _THUMB_IMG_EXTS:
        # Image: resize with Pillow — much faster than FFmpeg
        try:
            from PIL import Image, ImageOps
            with Image.open(str(p)) as im:
                im = ImageOps.exif_transpose(im)   # respect EXIF rotation
                im = im.convert('RGB')
                im.thumbnail((400, 400), Image.LANCZOS)
                im.save(str(cached), 'JPEG', quality=72, optimize=True)
            return send_file(str(cached), mimetype='image/jpeg')
        except Exception:
            abort(404)
    else:
        # Video: extract first frame with FFmpeg
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(['ffmpeg','-ss','00:00:01','-i',str(p),'-vframes','1',
                            '-q:v','4','-vf','scale=400:-1',tmp_path,'-y'],
                           capture_output=True, timeout=20)
            tp = Path(tmp_path)
            if tp.exists() and tp.stat().st_size > 0:
                import shutil
                shutil.copy2(tmp_path, str(cached))
                return send_file(str(cached), mimetype='image/jpeg')
        except Exception: pass
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
        abort(404)


@app.route('/mobile')
def mobile_page():
    from flask import make_response, request as freq
    import hashlib
    html = _MOBILE_HTML.replace('__APP_VER__', APP_VERSION)
    etag = hashlib.md5(html.encode()).hexdigest()[:16]
    if freq.headers.get('If-None-Match') == etag:
        return Response(status=304)
    resp = make_response(html)
    resp.headers['ETag'] = etag
    resp.headers['Cache-Control'] = 'no-cache'  # validate with server; SW handles offline
    return resp


@app.route('/admin')
def admin_page():
    return _ADMIN_HTML.replace('__APP_VER__', APP_VERSION)


@app.route('/')
def index():
    return _HTML.replace('__APP_VER__', APP_VERSION)


@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "AI File Classifier",
        "short_name": "AI Files",
        "description": "Local AI-powered media manager",
        "start_url": "/mobile",
        "display": "standalone",
        "background_color": "#0f0f13",
        "theme_color": "#0f0f13",
        "orientation": "portrait",
        "categories": ["photo", "utilities"],
        "icons": [
            {"src": "/icons/72.png",  "sizes": "72x72",   "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/96.png",  "sizes": "96x96",   "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/128.png", "sizes": "128x128", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/144.png", "sizes": "144x144", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/152.png", "sizes": "152x152", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/384.png", "sizes": "384x384", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icons/512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "screenshots": [],
        "shortcuts": [
            {"name": "Upload to PC", "url": "/mobile#upload", "description": "Send files from phone"},
            {"name": "Browse PC",    "url": "/mobile#browse", "description": "Browse and download files"},
        ],
    })


@app.route('/sw.js')
def service_worker():
    import hashlib
    version = hashlib.md5(_MOBILE_HTML.replace('__APP_VER__', APP_VERSION).encode()).hexdigest()[:12]
    js = r"""
const CACHE='ai-files-__VER__';
const SHELL=['/mobile','/manifest.json','/icons/192.png'];

self.addEventListener('install',e=>{
  e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));
});
self.addEventListener('activate',e=>{
  e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener('fetch',e=>{
  const url=new URL(e.request.url);
  const p=url.pathname;
  if(p.startsWith('/api/')||p==='/upload'||p==='/download'||p.startsWith('/thumb/')||p.startsWith('/media/')){
    e.respondWith(fetch(e.request).catch(()=>
      p.startsWith('/api/')
        ? new Response(JSON.stringify({ok:false,error:'offline'}),{headers:{'Content-Type':'application/json'}})
        : new Response('Offline',{status:503})
    ));
    return;
  }
  if(SHELL.includes(p)){
    e.respondWith(fetch(e.request).then(res=>{
      if(res.ok){const cl=res.clone();caches.open(CACHE).then(c=>c.put(e.request,cl));}
      return res;
    }).catch(()=>caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(cached=>{
    if(cached) return cached;
    return fetch(e.request).then(res=>{
      if(res.ok&&e.request.method==='GET'){const cl=res.clone();caches.open(CACHE).then(c=>c.put(e.request,cl));}
      return res;
    });
  }));
});
""".replace('__VER__', version)
    return Response(js, mimetype='application/javascript',
                    headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'})


@app.route('/icons/<int:size>.png')
def app_icon(size):
    if size > 512: abort(404)
    try:
        from PIL import Image, ImageDraw
        img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r    = int(size * 0.22)

        # Deep dark background
        draw.rounded_rectangle([0, 0, size-1, size-1], radius=r, fill=(15, 15, 19, 255))

        # Accent glow blob (soft purple circle behind icon)
        cx, cy = size//2, size//2
        glow_r = int(size * 0.30)
        glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([cx-glow_r, cy-glow_r, cx+glow_r, cy+glow_r], fill=(99, 102, 241, 60))
        img = Image.alpha_composite(img, glow)
        draw = ImageDraw.Draw(img)

        # Clean folder shape
        pad  = int(size * 0.18)
        fw   = size - pad * 2
        fh   = int(fw * 0.72)
        fx   = pad
        fy   = (size - fh) // 2 + int(size * 0.04)
        tab_w = int(fw * 0.38)
        tab_h = int(fh * 0.16)
        fr   = int(size * 0.06)

        # Folder body
        draw.rounded_rectangle([fx, fy + tab_h, fx + fw, fy + fh], radius=fr, fill=(99, 102, 241, 240))
        # Folder tab (top-left)
        draw.rounded_rectangle([fx, fy, fx + tab_w, fy + tab_h + fr], radius=fr, fill=(99, 102, 241, 240))

        # Inner lighter panel (gives depth)
        inner_pad = int(size * 0.055)
        draw.rounded_rectangle(
            [fx + inner_pad, fy + tab_h + inner_pad, fx + fw - inner_pad, fy + fh - inner_pad],
            radius=max(fr - 3, 2), fill=(129, 140, 248, 60))

        # Three horizontal lines (file list feel)
        line_x1 = fx + int(fw * 0.18)
        line_x2 = fx + int(fw * 0.78)
        line_col = (200, 210, 255, 180)
        line_w = max(2, int(size * 0.025))
        for i, ratio in enumerate([0.38, 0.53, 0.68]):
            ly = fy + int(fh * ratio)
            draw.rounded_rectangle([line_x1, ly, line_x2, ly + line_w], radius=line_w//2, fill=line_col)
            if i == 0: line_x2 = fx + int(fw * 0.55)  # shorter second line
            if i == 1: line_x2 = fx + int(fw * 0.65)

        # Sparkle dot (top-right of folder) — accent yellow
        sp = int(size * 0.095)
        sx = fx + fw - int(size * 0.12)
        sy = fy + tab_h - int(size * 0.06)
        draw.ellipse([sx - sp//2, sy - sp//2, sx + sp//2, sy + sp//2], fill=(251, 191, 36, 255))
        # Tiny inner white dot
        ti = sp // 3
        draw.ellipse([sx - ti//2, sy - ti//2, sx + ti//2, sy + ti//2], fill=(255, 255, 255, 200))

        buf = _io.BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return Response(buf.read(), mimetype='image/png',
                        headers={'Cache-Control': 'public, max-age=86400'})
    except Exception:
        try:
            from PIL import Image
            img = Image.new('RGB', (size, size), (99, 102, 241))
            buf = _io.BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
            return Response(buf.read(), mimetype='image/png')
        except Exception:
            abort(404)

# ─── ADMIN HTML ───────────────────────────────────────────────────────────────

_ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Classifier — Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f13;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
:root{--brand:#818cf8;--surface:#1a1a24;--surface2:#23232f;--surface3:#2d2d3d;--text1:#e2e8f0;--text2:#94a3b8;--text3:#64748b;--red:#f87171;--green:#4ade80;--yellow:#facc15;--blue:#60a5fa}
a{color:inherit;text-decoration:none}

/* ── TOP BAR (matches main app) ── */
.topbar{display:flex;align-items:center;gap:10px;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--surface3);flex-shrink:0;flex-wrap:wrap}
.logo{font-size:1.05rem;font-weight:700;color:var(--brand);white-space:nowrap;display:flex;align-items:center;gap:8px}
.logo-sep{color:var(--text3);font-weight:300;font-size:1.1rem}
.logo-sub{font-size:.85rem;font-weight:500;color:var(--text2)}
.topbar-nav{display:flex;align-items:center;gap:2px;flex:1;flex-wrap:wrap}
.nav-pill{display:flex;align-items:center;gap:7px;padding:6px 13px;border-radius:8px;cursor:pointer;color:var(--text2);font-size:.83rem;font-weight:500;border:none;background:transparent;transition:all .15s}
.nav-pill:hover{background:var(--surface2);color:var(--text1)}
.nav-pill.active{background:#2d2d4d;color:var(--brand)}
.nav-pill svg{flex-shrink:0;opacity:.75}
.nav-pill.active svg{opacity:1}
.topbar-right{display:flex;align-items:center;gap:8px;margin-left:auto;flex-shrink:0}

/* ── LAYOUT ── */
.layout{display:flex;flex:1;overflow:hidden}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.content{flex:1;overflow-y:auto;padding:24px;display:none}
.content.active{display:block}

/* ── STAT CARDS ── */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.stat-card{background:var(--surface2);border:1px solid var(--surface3);border-radius:12px;padding:16px}
.stat-card .val{font-size:1.8rem;font-weight:700;color:var(--brand);line-height:1}
.stat-card .lbl{font-size:.72rem;color:var(--text2);margin-top:6px;text-transform:uppercase;letter-spacing:.5px}
.stat-card .sub{font-size:.72rem;color:var(--text3);margin-top:2px}

/* ── SECTION ── */
.section{background:var(--surface2);border:1px solid var(--surface3);border-radius:12px;margin-bottom:20px;overflow:hidden}
.section-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--surface3)}
.section-head h2{font-size:.88rem;font-weight:600;color:var(--text1)}
.section-body{padding:0}

/* ── TABLE ── */
table{width:100%;border-collapse:collapse}
th{text-align:left;padding:10px 18px;font-size:.7rem;color:var(--text3);text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--surface3)}
td{padding:10px 18px;font-size:.82rem;color:var(--text2);border-bottom:1px solid var(--surface3)}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surface3);color:var(--text1)}
.td-main{color:var(--text1);font-weight:500}

/* ── BADGES ── */
.badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.68rem;font-weight:600}
.badge-ok{background:#14532d;color:var(--green)}
.badge-err{background:#450a0a;color:var(--red)}
.badge-warn{background:#422006;color:var(--yellow)}
.badge-info{background:#1e3a5f;color:var(--blue)}
.badge-gray{background:var(--surface3);color:var(--text2)}

/* ── BUTTONS ── */
.btn{padding:7px 14px;border-radius:8px;border:none;cursor:pointer;font-size:.82rem;font-weight:600;transition:all .15s}
.btn-primary{background:var(--brand);color:#fff}.btn-primary:hover{opacity:.85}
.btn-danger{background:#450a0a;color:var(--red);border:1px solid #7f1d1d}.btn-danger:hover{background:#7f1d1d}
.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--surface3)}.btn-ghost:hover{background:var(--surface3);color:var(--text1)}
.btn-sm{padding:4px 10px;font-size:.75rem}

/* ── FORM ── */
input,select{background:var(--surface3);color:var(--text1);border:1px solid var(--surface3);border-radius:8px;padding:8px 12px;font-size:.85rem;outline:none;transition:border-color .15s}
input:focus,select:focus{border-color:var(--brand)}
input::placeholder{color:var(--text3)}
.form-row{display:flex;gap:10px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--surface3);flex-wrap:wrap}
.form-row label{font-size:.82rem;color:var(--text2);min-width:130px}
.form-row input,.form-row select{flex:1;min-width:180px}

/* ── OLLAMA STATUS ── */
.ollama-dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot-ok{background:var(--green);box-shadow:0 0 5px var(--green)}
.dot-err{background:var(--red)}
.dot-loading{background:var(--yellow);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* ── ITEMS ── */
.folder-item{padding:14px 18px;border-bottom:1px solid var(--surface3);display:flex;align-items:center;gap:12px}
.folder-item:last-child{border-bottom:none}
.folder-path{font-size:.82rem;color:var(--text1);font-weight:500;word-break:break-all}
.folder-meta{font-size:.72rem;color:var(--text3);margin-top:2px}
.folder-actions{margin-left:auto;display:flex;gap:6px;flex-shrink:0}
.tag-item{padding:12px 18px;border-bottom:1px solid var(--surface3);display:flex;align-items:center;gap:12px}
.tag-item:last-child{border-bottom:none}
.tag-chip{padding:4px 12px;border-radius:99px;font-size:.78rem;font-weight:600;flex-shrink:0}
.tag-count{font-size:.75rem;color:var(--text3);margin-left:auto}
.job-item{padding:12px 18px;border-bottom:1px solid var(--surface3);display:flex;align-items:center;gap:12px}
.job-item:last-child{border-bottom:none}
.job-path{font-size:.82rem;color:var(--text1);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job-status{font-size:.72rem;flex-shrink:0}

/* ── MISC ── */
.model-option{padding:10px 18px;cursor:pointer;border-bottom:1px solid var(--surface3);font-size:.82rem;color:var(--text2);display:flex;align-items:center;gap:10px}
.model-option:hover{background:var(--surface3)}
.model-option.selected{color:var(--brand)}
.inline-edit{display:none;gap:6px;align-items:center}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--surface3);border-radius:99px}
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <div class="logo">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
    <a href="/" style="color:var(--brand)">AI Classifier</a>
    <span class="logo-sep">/</span>
    <span class="logo-sub">Admin</span>
    <span style="font-size:.65rem;color:var(--text3);letter-spacing:.04em;margin-left:4px">v__APP_VER__</span>
  </div>

  <!-- Inline nav pills -->
  <div class="topbar-nav">
    <button class="nav-pill active" onclick="showPanel('dashboard')" id="nav-dashboard">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      Dashboard
    </button>
    <button class="nav-pill" onclick="showPanel('folders')" id="nav-folders">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      Folders
    </button>
    <button class="nav-pill" onclick="showPanel('tags')" id="nav-tags">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
      Tags
    </button>
    <button class="nav-pill" onclick="showPanel('aiqueue')" id="nav-aiqueue">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
      AI Queue
    </button>
    <button class="nav-pill" onclick="showPanel('settings')" id="nav-settings">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
      Settings
    </button>
  </div>

  <div class="topbar-right">
    <span id="ollamaDot" class="ollama-dot dot-loading"></span>
    <span id="ollamaStatus" style="font-size:.78rem;color:var(--text2);margin-right:4px">Checking…</span>
    <button class="btn btn-ghost btn-sm" onclick="refreshPanel()">↺ Refresh</button>
    <a href="/mobile" target="_blank" class="btn btn-ghost btn-sm">📱 Mobile</a>
  </div>
</div>

<!-- CONTENT -->
<div class="main">

  <!-- DASHBOARD -->
  <div class="content active" id="panel-dashboard">
    <div class="stat-grid" id="statGrid">
      <div class="stat-card"><div class="val" id="st-files">—</div><div class="lbl">Total Files</div><div class="sub" id="st-files-sub"></div></div>
      <div class="stat-card"><div class="val" id="st-tags">—</div><div class="lbl">Tags</div></div>
      <div class="stat-card"><div class="val" id="st-queue">—</div><div class="lbl">AI Queue</div><div class="sub" id="st-queue-sub"></div></div>
      <div class="stat-card"><div class="val" id="st-folders">—</div><div class="lbl">Folders</div></div>
    </div>
    <div class="section">
      <div class="section-head"><h2>Recent Activity</h2><span style="font-size:.72rem;color:var(--text3)">Last 50 events</span></div>
      <div class="section-body">
        <table>
          <thead><tr><th>File</th><th>Category</th><th>Action</th><th>AI Caption</th></tr></thead>
          <tbody id="activityTbody"><tr><td colspan="4" style="text-align:center;color:var(--text3);padding:20px">Loading…</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- FOLDERS -->
  <div class="content" id="panel-folders">
    <div class="section" style="margin-bottom:20px">
      <div class="section-head"><h2>Add Folder</h2></div>
      <div class="form-row">
        <label>Folder Path</label>
        <input id="newFolderPath" placeholder="C:\Users\you\Pictures" style="flex:1">
        <button class="btn btn-primary" onclick="addFolder()">Add Folder</button>
      </div>
    </div>
    <div class="section">
      <div class="section-head"><h2>Tracked Folders</h2><button class="btn btn-ghost btn-sm" onclick="loadFolders()">↺</button></div>
      <div class="section-body" id="folderList"><div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div></div>
    </div>
  </div>

  <!-- TAGS -->
  <div class="content" id="panel-tags">
    <div class="section">
      <div class="section-head">
        <h2>All Tags</h2>
        <div style="display:flex;gap:8px">
          <input id="tagSearch" placeholder="Search tags…" style="width:180px" oninput="filterTagList()">
          <button class="btn btn-ghost btn-sm" onclick="loadTags()">↺</button>
        </div>
      </div>
      <div class="section-body" id="tagList"><div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div></div>
    </div>
  </div>

  <!-- AI QUEUE -->
  <div class="content" id="panel-aiqueue">
    <!-- ── Status summary ── -->
    <div class="stat-grid" id="queueStats">
      <div class="stat-card"><div class="val" id="q-pending">—</div><div class="lbl">Pending</div></div>
      <div class="stat-card"><div class="val" id="q-processing">—</div><div class="lbl">Processing</div></div>
      <div class="stat-card"><div class="val" id="q-done">—</div><div class="lbl">Done</div></div>
      <div class="stat-card"><div class="val" id="q-error">—</div><div class="lbl">Errors</div></div>
    </div>

    <!-- ── Live progress ── -->
    <div class="section" id="queueProgressSection" style="display:none">
      <div class="section-head"><h2>Now Processing</h2><span class="badge badge-info" style="animation:pulse 1.5s infinite">Live</span></div>
      <div class="section-body" style="padding:16px 20px">
        <div style="font-weight:600;margin-bottom:8px" id="qCurrentFile">—</div>
        <div style="background:var(--bg3);border-radius:4px;overflow:hidden;height:6px;margin-bottom:6px">
          <div id="qProgressBar" style="height:6px;background:var(--accent);width:0%;transition:width .4s;border-radius:4px"></div>
        </div>
        <div style="font-size:.75rem;color:var(--text3)" id="qElapsed">—</div>
      </div>
    </div>

    <!-- ── Run AI ── -->
    <div class="section">
      <div class="section-head"><h2>Run AI Tagging</h2></div>
      <div class="section-body" style="padding:16px 20px;display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <select id="runFolderSelect" style="flex:1;min-width:160px"><option value="">— Select folder —</option></select>
          <button class="btn btn-primary" onclick="enqueueFolder()">Enqueue Folder</button>
          <button class="btn btn-ghost" onclick="enqueueAllUnprocessed()">Enqueue All Unprocessed</button>
        </div>
        <div style="font-size:.8rem;color:var(--text3)">Only files without AI tags will be queued. The AI worker runs automatically in the background.</div>
      </div>
    </div>

    <!-- ── Schedule ── -->
    <div class="section">
      <div class="section-head"><h2>Auto-Schedule</h2><button class="btn btn-ghost btn-sm" onclick="loadAiSchedule()">↺</button></div>
      <div class="section-body" style="padding:16px 20px;display:flex;flex-direction:column;gap:12px" id="scheduleBody">
        <div style="display:flex;align-items:center;gap:12px">
          <label style="font-weight:600">Enabled</label>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="checkbox" id="schedEnabled" style="width:16px;height:16px">
            <span style="font-size:.875rem">Run daily at scheduled time</span>
          </label>
        </div>
        <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <label>Time</label>
          <input type="number" id="schedHour" min="0" max="23" value="2" style="width:64px"> :
          <input type="number" id="schedMinute" min="0" max="59" value="0" style="width:64px">
          <button class="btn btn-primary" onclick="saveAiSchedule()">Save Schedule</button>
        </div>
        <div style="font-size:.8rem;color:var(--text3)" id="schedStatus">—</div>
      </div>
    </div>

    <!-- ── Queue jobs ── -->
    <div class="section">
      <div class="section-head">
        <h2>Queue Jobs</h2>
        <div style="display:flex;gap:8px">
          <select id="queueStatusFilter" onchange="loadQueueJobs()"><option value="">All</option><option value="pending">Pending</option><option value="processing">Processing</option><option value="done">Done</option><option value="error">Error</option></select>
          <button class="btn btn-danger btn-sm" onclick="clearQueueErrors()">Clear Errors</button>
          <button class="btn btn-ghost btn-sm" title="Remove DB entries for files that no longer exist on disk, then re-scan" onclick="cleanStaleFiles()">🧹 Clean Stale</button>
          <button class="btn btn-ghost btn-sm" onclick="loadQueueJobs()">↺</button>
        </div>
      </div>
      <div class="section-body" id="queueJobList"><div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div></div>
    </div>

    <!-- ── Analytics ── -->
    <div class="section">
      <div class="section-head"><h2>Analytics</h2><button class="btn btn-ghost btn-sm" onclick="loadAiAnalytics()">↺</button></div>
      <div class="section-body" style="padding:16px 20px" id="analyticsBody">
        <div style="text-align:center;color:var(--text3)">Loading…</div>
      </div>
    </div>
  </div>

  <!-- SETTINGS -->
  <div class="content" id="panel-settings">
    <div class="section" style="margin-bottom:20px">
      <div class="section-head"><h2>Ollama Connection</h2></div>
      <div class="form-row">
        <label>Ollama Host</label>
        <input id="settingOllamaHost" placeholder="http://localhost:11434">
        <button class="btn btn-primary" onclick="saveSetting('ollama_host',document.getElementById('settingOllamaHost').value)">Save</button>
      </div>
      <div class="form-row">
        <label>AI Model</label>
        <select id="settingModel" style="flex:1"><option>Loading…</option></select>
        <button class="btn btn-primary" onclick="saveSetting('model',document.getElementById('settingModel').value)">Save</button>
      </div>
    </div>
    <div class="section">
      <div class="section-head"><h2>AI Models Available</h2><button class="btn btn-ghost btn-sm" onclick="loadOllamaStatus()">↺ Check</button></div>
      <div class="section-body" id="modelList"><div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div></div>
    </div>
    <div style="padding:24px 0 8px;text-align:center">
      <span style="font-size:.65rem;color:var(--text3);letter-spacing:.05em;user-select:none">AI File Classifier&nbsp;&nbsp;v__APP_VER__</span>
    </div>
  </div>
</div>

<script>
'use strict';
let _curPanel='dashboard';
let _allTags=[];

function showPanel(name){
  document.querySelectorAll('.content').forEach(c=>c.classList.remove('active'));
  document.querySelectorAll('.nav-pill').forEach(n=>n.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
  document.getElementById('nav-'+name).classList.add('active');
  _curPanel=name;
  loadPanel(name);
}

function loadPanel(name){
  if(name==='dashboard') loadDashboard();
  else if(name==='folders') { loadFolders(); loadAiRunFolders(); }
  else if(name==='tags') loadTags();
  else if(name==='aiqueue') { loadQueueJobs(); loadAiSchedule(); loadAiAnalytics(); loadAiRunFolders(); }
  else if(name==='settings') loadSettings();
}

function refreshPanel(){ loadPanel(_curPanel); }

// ── Dashboard ──
async function loadDashboard(){
  const [stats, q, act] = await Promise.all([
    fetch('/api/stats').then(r=>r.json()).catch(()=>null),
    fetch('/api/ai-queue').then(r=>r.json()).catch(()=>({pending:0,processing:0})),
    fetch('/api/activity').then(r=>r.json()).catch(()=>[])
  ]);
  if(stats){
    document.getElementById('st-files').textContent=stats.total||0;
    document.getElementById('st-files-sub').textContent=`${stats.images||0} photos, ${stats.videos||0} videos`;
    document.getElementById('st-tags').textContent=stats.tags||0;
    document.getElementById('st-folders').textContent=stats.folders||0;
  }
  document.getElementById('st-queue').textContent=(q.pending||0)+(q.processing||0);
  document.getElementById('st-queue-sub').textContent=q.pending?`${q.pending} pending`:'All done';
  // activity table
  const tbody=document.getElementById('activityTbody');
  if(!act.length){ tbody.innerHTML='<tr><td colspan="4" style="text-align:center;color:var(--text3);padding:20px">No activity yet</td></tr>'; return; }
  tbody.innerHTML=act.map(f=>{
    const name=f.path.split(/[\\/]/).pop();
    return `<tr>
      <td class="td-main" title="${f.path}" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</td>
      <td><span class="badge badge-info">${f.category||'—'}</span></td>
      <td><span class="badge badge-gray">${f.action||'—'}</span></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:italic;color:var(--text3)">${f.ai_caption||''}</td>
    </tr>`;
  }).join('');
}

// ── Ollama status ──
async function loadOllamaStatus(){
  document.getElementById('ollamaDot').className='ollama-dot dot-loading';
  document.getElementById('ollamaStatus').textContent='Checking…';
  try{
    const d=await fetch('/api/ollama/status').then(r=>r.json());
    if(d.ok){
      document.getElementById('ollamaDot').className='ollama-dot dot-ok';
      document.getElementById('ollamaStatus').textContent=`Ollama ${d.version||'online'}`;
      // populate model selector
      const sel=document.getElementById('settingModel');
      if(sel && d.models){
        sel.innerHTML=d.models.map(m=>`<option value="${m.name}" ${m.active?'selected':''}>${m.name}</option>`).join('');
      }
      // model list
      const ml=document.getElementById('modelList');
      if(ml && d.models){
        ml.innerHTML=d.models.map(m=>`
          <div class="model-option ${m.active?'selected':''}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            <span style="flex:1">${m.name}</span>
            ${m.active?'<span class="badge badge-ok">Active</span>':''}
          </div>`).join('');
      }
    } else {
      document.getElementById('ollamaDot').className='ollama-dot dot-err';
      document.getElementById('ollamaStatus').textContent='Ollama offline';
      const ml=document.getElementById('modelList');
      if(ml) ml.innerHTML='<div style="padding:16px;color:var(--red)">Cannot reach Ollama. Make sure it is running.</div>';
    }
  }catch(e){
    document.getElementById('ollamaDot').className='ollama-dot dot-err';
    document.getElementById('ollamaStatus').textContent='Error';
  }
}

// ── Folders ──
async function loadFolders(){
  const el=document.getElementById('folderList');
  el.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div>';
  const data=await fetch('/api/folders').then(r=>r.json()).catch(()=>[]);
  if(!data.length){ el.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3)">No folders tracked yet. Add one above.</div>'; return; }
  el.innerHTML=data.map(f=>{
    const avail=f.available!==false;
    const name=f.display_name||(f.path.split(/[\\/]/).pop());
    return `<div class="folder-item">
      <div style="width:10px;height:10px;border-radius:50%;flex-shrink:0;background:${avail?'var(--green)':'var(--red)'};${avail?'box-shadow:0 0 5px var(--green)':''}"></div>
      <div style="flex:1;min-width:0">
        <div class="folder-path">${name}</div>
        <div class="folder-meta">${f.path}</div>
        <div class="folder-meta">${f.file_count||0} files · ${f.is_external?'External drive':'Local folder'} · ${avail?'Available':'Not connected'}</div>
      </div>
      <div class="folder-actions">
        <button class="btn btn-ghost btn-sm" onclick="scanFolder(${f.id})">↺ Scan</button>
        <button class="btn btn-danger btn-sm" onclick="deleteFolder(${f.id},'${name.replace(/'/g,"\\'")}')">Remove</button>
      </div>
    </div>`;
  }).join('');
}

async function addFolder(){
  const path=document.getElementById('newFolderPath').value.trim();
  if(!path){ alert('Enter a folder path'); return; }
  const r=await fetch('/api/folders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();
  if(d.id){ document.getElementById('newFolderPath').value=''; loadFolders(); }
  else alert(d.error||'Failed to add folder');
}

async function deleteFolder(id, name){
  if(!confirm(`Remove folder "${name}"?\n\nFiles already classified will remain in the database.`)) return;
  const r=await fetch(`/api/folders/${id}`,{method:'DELETE'});
  const d=await r.json();
  if(d.ok) loadFolders();
  else alert(d.error||'Failed to remove folder');
}

async function scanFolder(id){
  const r=await fetch(`/api/scan`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder_id:id})});
  const d=await r.json().catch(()=>({ok:false}));
  if(d.ok||d.scanned!=null) { alert(`Scan complete: ${d.scanned||0} new files found`); loadFolders(); }
  else alert(d.error||'Scan failed');
}

// ── Tags ──
async function loadTags(){
  const el=document.getElementById('tagList');
  el.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3)">Loading…</div>';
  _allTags=await fetch('/api/tags').then(r=>r.json()).catch(()=>[]);
  renderTagList(_allTags);
}

function filterTagList(){
  const q=document.getElementById('tagSearch').value.toLowerCase();
  renderTagList(_allTags.filter(t=>t.name.toLowerCase().includes(q)));
}

function renderTagList(tags){
  const el=document.getElementById('tagList');
  if(!tags.length){ el.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3)">No tags found</div>'; return; }
  el.innerHTML=tags.map(t=>`
    <div class="tag-item" id="tag-row-${t.id}">
      <div class="tag-chip" style="background:${t.color}22;color:${t.color};border:1px solid ${t.color}55">${t.name}</div>
      <span style="font-size:.75rem;color:var(--text3)">${t.n||0} files</span>
      <span class="tag-count"></span>
      <div class="inline-edit" id="tag-edit-${t.id}" style="display:none;gap:6px">
        <input id="tag-name-${t.id}" value="${t.name}" style="width:130px">
        <input type="color" id="tag-color-${t.id}" value="${t.color}" style="width:42px;padding:3px">
        <button class="btn btn-primary btn-sm" onclick="saveTag(${t.id})">Save</button>
        <button class="btn btn-ghost btn-sm" onclick="cancelTagEdit(${t.id})">✕</button>
      </div>
      <div style="margin-left:auto;display:flex;gap:6px">
        <button class="btn btn-ghost btn-sm" onclick="editTag(${t.id})">Edit</button>
        <button class="btn btn-danger btn-sm" onclick="deleteTag(${t.id},'${t.name.replace(/'/g,"\\'")}')">Delete</button>
      </div>
    </div>`).join('');
}

function editTag(id){
  document.getElementById(`tag-edit-${id}`).style.display='flex';
}
function cancelTagEdit(id){
  document.getElementById(`tag-edit-${id}`).style.display='none';
}
async function saveTag(id){
  const name=document.getElementById(`tag-name-${id}`).value.trim();
  const color=document.getElementById(`tag-color-${id}`).value;
  if(!name) return;
  const r=await fetch(`/api/tags/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,color})});
  const d=await r.json();
  if(d.ok) loadTags();
  else alert(d.error||'Failed to save tag');
}
async function deleteTag(id, name){
  if(!confirm(`Delete tag "${name}"? This removes it from all files.`)) return;
  const r=await fetch(`/api/tags/${id}`,{method:'DELETE'});
  const d=await r.json();
  if(d.ok) loadTags();
  else alert(d.error||'Failed');
}

// ── AI Queue ──
let _queuePollTimer = null;

async function loadAiRunFolders(){
  const sel = document.getElementById('runFolderSelect');
  if(!sel) return;
  const data = await fetch('/api/folders').then(r=>r.json()).catch(()=>[]);
  sel.innerHTML = '<option value="">— Select folder —</option>' +
    data.map(f=>{
      const name = f.display_name||(f.path.split(/[\\/]/).pop());
      return `<option value="${f.id}" data-path="${f.path}">${name}</option>`;
    }).join('');
}

async function enqueueFolder(){
  const sel = document.getElementById('runFolderSelect');
  const id = sel.value;
  if(!id){ alert('Select a folder first'); return; }
  const btn = event.target; btn.disabled = true; btn.textContent = 'Queuing…';
  const d = await fetch('/api/ai-queue/enqueue-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder_id:parseInt(id)})}).then(r=>r.json()).catch(()=>({ok:false}));
  btn.disabled = false; btn.textContent = 'Enqueue Folder';
  if(d.ok) { alert(`Queued ${d.queued} files for AI processing`); loadQueueJobs(); }
  else alert(d.error||'Failed to enqueue folder');
}

async function enqueueAllUnprocessed(){
  const btn = event.target; btn.disabled = true; btn.textContent = 'Queuing…';
  const d = await fetch('/api/ai-queue/enqueue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all_unprocessed:true})}).then(r=>r.json()).catch(()=>({ok:false}));
  btn.disabled = false; btn.textContent = 'Enqueue All Unprocessed';
  if(d.ok) { alert(`Queued ${d.queued||0} unprocessed files`); loadQueueJobs(); }
  else alert(d.error||'Failed');
}

async function loadQueueJobs(){
  const status=document.getElementById('queueStatusFilter').value;
  const [q, jobs]=await Promise.all([
    fetch('/api/ai-queue').then(r=>r.json()).catch(()=>{}),
    fetch(`/api/ai-queue/jobs${status?'?status='+status:''}`).then(r=>r.json()).catch(()=>[])
  ]);
  if(q){
    document.getElementById('q-pending').textContent=q.pending||0;
    document.getElementById('q-processing').textContent=q.processing||0;
    document.getElementById('q-done').textContent=q.done||0;
    document.getElementById('q-error').textContent=q.errors||0;
    // Live progress section
    const progSec = document.getElementById('queueProgressSection');
    if(q.current_file){
      progSec.style.display='';
      document.getElementById('qCurrentFile').textContent = q.current_file;
      const elapsedSec = Math.round((q.current_elapsed_ms||0)/1000);
      document.getElementById('qElapsed').textContent = `Processing for ${elapsedSec}s`;
      // Indeterminate progress animation
      const bar = document.getElementById('qProgressBar');
      bar.style.width = ((elapsedSec % 10) * 10) + '%';
    } else {
      progSec.style.display='none';
    }
    // Auto-poll when active
    const isActive = (q.pending||0) > 0 || (q.processing||0) > 0;
    if(isActive && !_queuePollTimer){
      _queuePollTimer = setInterval(loadQueueJobs, 3000);
    } else if(!isActive && _queuePollTimer){
      clearInterval(_queuePollTimer); _queuePollTimer = null;
    }
  }
  const el=document.getElementById('queueJobList');
  if(!jobs.length){ el.innerHTML='<div style="padding:20px;text-align:center;color:var(--text3)">No jobs</div>'; return; }
  const badge={pending:'badge-warn',processing:'badge-info',done:'badge-ok',error:'badge-err'};
  el.innerHTML=jobs.map(j=>{
    const name=(j.file_path||j.path||'').split(/[\\/]/).pop();
    const ms = j.processing_ms ? ` · ${(j.processing_ms/1000).toFixed(1)}s` : '';
    const err = j.error ? `<span style="color:var(--red);font-size:.72rem;display:block;margin-top:2px">${j.error.substring(0,80)}</span>` : '';
    return `<div class="job-item">
      <span class="badge ${badge[j.status]||'badge-gray'}">${j.status}</span>
      <span class="job-path" title="${j.file_path||j.path||''}" style="flex:1">${name}${err}</span>
      <span class="job-status" style="color:var(--text3);font-size:.75rem">${(j.queued_at||'').substring(0,16)}${ms}</span>
    </div>`;
  }).join('');
}

async function clearQueueErrors(){
  if(!confirm('Remove all errored jobs from the queue?')) return;
  await fetch('/api/ai-queue/clear-errors',{method:'POST'});
  loadQueueJobs();
}

async function cleanStaleFiles(){
  if(!confirm('This will remove DB entries and queue jobs for files that no longer exist on disk, then re-scan your folders for new/moved files. Continue?')) return;
  const btn = event.target;
  btn.disabled=true; btn.textContent='🧹 Cleaning…';
  try {
    const c = await fetch('/api/clean-stale',{method:'POST'}).then(r=>r.json());
    const s = await fetch('/api/scan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})}).then(r=>r.json());
    alert(`Done!\nRemoved ${c.removed} stale entries.\nFound ${s.scanned||0} new files.`);
    loadQueueJobs();
  } catch(e){ alert('Error: '+e); }
  finally{ btn.disabled=false; btn.textContent='🧹 Clean Stale'; }
}


// ── AI Schedule ──
async function loadAiSchedule(){
  const d = await fetch('/api/ai-schedule').then(r=>r.json()).catch(()=>null);
  if(!d) return;
  document.getElementById('schedEnabled').checked = d.enabled;
  document.getElementById('schedHour').value = d.hour;
  document.getElementById('schedMinute').value = d.minute;
  document.getElementById('schedStatus').textContent = d.enabled
    ? `Scheduled daily at ${String(d.hour).padStart(2,'0')}:${String(d.minute).padStart(2,'0')}`
    : 'Schedule disabled';
}

async function saveAiSchedule(){
  const enabled = document.getElementById('schedEnabled').checked;
  const hour = parseInt(document.getElementById('schedHour').value)||0;
  const minute = parseInt(document.getElementById('schedMinute').value)||0;
  const d = await fetch('/api/ai-schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,hour,minute,mode:'daily'})}).then(r=>r.json()).catch(()=>({ok:false}));
  if(d.ok) {
    document.getElementById('schedStatus').textContent = enabled
      ? `Saved. Will run daily at ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}`
      : 'Schedule disabled and saved';
  }
}

// ── AI Analytics ──
async function loadAiAnalytics(){
  const el = document.getElementById('analyticsBody');
  const d = await fetch('/api/ai-analytics').then(r=>r.json()).catch(()=>null);
  if(!d){ el.innerHTML='<div style="color:var(--text3)">No data yet</div>'; return; }

  const total = (d.done||0) + (d.errors||0) + (d.skipped||0);
  const successRate = total > 0 ? Math.round((d.done||0)/total*100) : 0;
  const avgSec = d.avg_ms ? (d.avg_ms/1000).toFixed(1) : '—';

  // Bar chart for last 14 days
  let chartHtml = '';
  if(d.per_day && d.per_day.length){
    const maxN = Math.max(...d.per_day.map(x=>x.n), 1);
    chartHtml = `<div style="margin:16px 0">
      <div style="font-weight:600;margin-bottom:10px;font-size:.875rem">Files processed per day</div>
      <div style="display:flex;align-items:flex-end;gap:4px;height:64px">
        ${d.per_day.map(x=>{
          const h = Math.max(4, Math.round(x.n/maxN*60));
          return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">
            <span style="font-size:.6rem;color:var(--text3)">${x.n}</span>
            <div style="width:100%;height:${h}px;background:var(--accent);border-radius:3px 3px 0 0;opacity:.85" title="${x.day}: ${x.n} files"></div>
          </div>`;
        }).join('')}
      </div>
      <div style="font-size:.68rem;color:var(--text3);margin-top:4px">${d.per_day[0]?.day||''} → ${d.per_day[d.per_day.length-1]?.day||''}</div>
    </div>`;
  }

  // Top categories
  let catsHtml = '';
  if(d.top_categories && d.top_categories.length){
    const maxC = Math.max(...d.top_categories.map(x=>x.n), 1);
    catsHtml = `<div style="margin-top:16px">
      <div style="font-weight:600;margin-bottom:10px;font-size:.875rem">Top categories (AI-tagged)</div>
      ${d.top_categories.map(x=>`
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <span style="width:90px;font-size:.8rem;color:var(--text2)">${x.cat}</span>
          <div style="flex:1;background:var(--bg3);border-radius:3px;height:8px">
            <div style="width:${Math.round(x.n/maxC*100)}%;background:var(--accent);height:8px;border-radius:3px"></div>
          </div>
          <span style="font-size:.75rem;color:var(--text3);width:30px;text-align:right">${x.n}</span>
        </div>`).join('')}
    </div>`;
  }

  // Recent errors
  let errHtml = '';
  if(d.recent_errors && d.recent_errors.length){
    errHtml = `<div style="margin-top:16px">
      <div style="font-weight:600;margin-bottom:8px;font-size:.875rem;color:var(--red)">Recent errors</div>
      ${d.recent_errors.map(e=>`
        <div style="font-size:.78rem;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="font-weight:600">${e.file}</span>
          <span style="color:var(--text3);margin-left:8px">${(e.error||'').substring(0,80)}</span>
        </div>`).join('')}
    </div>`;
  }

  el.innerHTML = `
    <div class="stat-grid" style="margin-bottom:16px">
      <div class="stat-card"><div class="val">${successRate}%</div><div class="lbl">Success rate</div></div>
      <div class="stat-card"><div class="val">${avgSec}s</div><div class="lbl">Avg time/file</div></div>
      <div class="stat-card"><div class="val">${d.done||0}</div><div class="lbl">Completed</div></div>
      <div class="stat-card"><div class="val">${d.errors||0}</div><div class="lbl">Errors</div></div>
    </div>
    ${chartHtml}${catsHtml}${errHtml}
  `;
}

// ── Settings ──
async function loadSettings(){
  const d=await fetch('/api/settings').then(r=>r.json()).catch(()=>null);
  if(d){
    if(d.ollama_host) document.getElementById('settingOllamaHost').value=d.ollama_host;
  }
  loadOllamaStatus();
}

async function saveSetting(key, value){
  const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,value})});
  const d=await r.json().catch(()=>({ok:false}));
  if(d.ok) { loadOllamaStatus(); alert('Saved'); }
  else alert(d.error||'Failed to save setting');
}

// ── Init ──
window.addEventListener('DOMContentLoaded',()=>{
  loadOllamaStatus();
  loadDashboard();
});
</script>
</body>
</html>"""

# ─── MAIN HTML ────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI File Classifier</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f13;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* ── TOP BAR ── */
.topbar{display:flex;align-items:center;gap:10px;padding:10px 16px;background:#1a1a24;border-bottom:1px solid #2d2d3d;flex-shrink:0;flex-wrap:wrap}
.logo{font-size:1.1rem;font-weight:700;color:#818cf8;white-space:nowrap}
.search-wrap{flex:1;min-width:200px;display:flex;gap:6px}
.search-wrap input{flex:1;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:7px 12px;border-radius:8px;font-size:.9rem}
.search-wrap input:focus{outline:none;border-color:#818cf8}
.btn{padding:7px 14px;border-radius:8px;border:none;cursor:pointer;font-size:.85rem;font-weight:500;transition:.15s}
.btn-primary{background:#818cf8;color:#fff}.btn-primary:hover{background:#6366f1}
.btn-sm{padding:4px 10px;font-size:.8rem}
.btn-ghost{background:transparent;color:#94a3b8;border:1px solid #3d3d55}.btn-ghost:hover{background:#1e1e2e}
.btn-danger{background:#ef4444;color:#fff}.btn-danger:hover{background:#dc2626}
.topbar-right{display:flex;align-items:center;gap:8px}
select.sort-sel{background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:6px 10px;border-radius:8px;font-size:.85rem;cursor:pointer}

/* ── LAYOUT ── */
.layout{display:flex;flex:1;overflow:hidden}
.sidebar{width:210px;flex-shrink:0;background:#13131e;border-right:1px solid #2d2d3d;overflow-y:auto;padding:10px 0}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}

/* ── SIDEBAR ── */
.sidebar-section{margin-bottom:4px}
.sidebar-title{padding:6px 14px;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:#64748b;display:flex;align-items:center;justify-content:space-between}
.sidebar-item{display:flex;align-items:center;justify-content:space-between;padding:6px 14px;cursor:pointer;font-size:.83rem;color:#94a3b8;border-radius:0;transition:.1s}
.sidebar-item:hover{background:#1e1e2e;color:#e2e8f0}
.sidebar-item.active{background:#2d2d4d;color:#818cf8}
.sidebar-count{font-size:.75rem;background:#2d2d3d;padding:1px 7px;border-radius:99px;color:#64748b}
.sidebar-item.active .sidebar-count{background:#4338ca;color:#c7d2fe}
.sidebar-del{cursor:pointer;color:#64748b;font-size:.75rem;padding:2px 5px;border-radius:4px;border:none;background:transparent}
.sidebar-del:hover{color:#ef4444;background:#2d1a1a}
.tag-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-right:6px}

/* ── FILTERS STRIP ── */
.filters-strip{display:flex;align-items:center;gap:8px;padding:8px 14px;background:#13131e;border-bottom:1px solid #2d2d3d;overflow-x:auto;flex-shrink:0;flex-wrap:wrap}
.filter-chip{padding:4px 12px;border-radius:99px;border:1px solid #3d3d55;font-size:.78rem;cursor:pointer;white-space:nowrap;color:#94a3b8;background:#0f0f1a;transition:.1s}
.filter-chip:hover,.filter-chip.active{background:#4338ca;border-color:#818cf8;color:#fff}
.filter-label{font-size:.75rem;color:#64748b;white-space:nowrap}

/* ── BULK BAR ── */
.bulk-bar{display:none;align-items:center;gap:8px;padding:8px 14px;background:#1e1e3a;border-bottom:1px solid #4338ca;flex-shrink:0;flex-wrap:wrap}
.bulk-bar.visible{display:flex}
.bulk-count{font-size:.85rem;color:#a5b4fc;font-weight:600}

/* ── GRID ── */
.grid-container{flex:1;overflow-y:auto;padding:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.card{position:relative;background:#1a1a24;border:2px solid transparent;border-radius:10px;overflow:hidden;cursor:pointer;transition:.15s;aspect-ratio:1}
.card:hover{border-color:#4338ca}
.card.selected{border-color:#818cf8;background:#1e1e3a}
.card img,.card video,.card .thumb-wrap{width:100%;height:100%;object-fit:cover;display:block}
.thumb-wrap{background:#0f0f1a;display:flex;align-items:center;justify-content:center;font-size:2.5rem;color:#4d4d6e}
.card-overlay{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,.85));padding:8px 8px 6px;opacity:0;transition:.15s}
.card:hover .card-overlay,.card.selected .card-overlay{opacity:1}
.card-name{font-size:.72rem;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-cat{font-size:.68rem;color:#818cf8;margin-top:1px}
.card-tags{display:flex;flex-wrap:wrap;gap:3px;margin-top:4px}
.card-tag{font-size:.62rem;padding:1px 6px;border-radius:99px;color:#fff}
.card-check{position:absolute;top:8px;left:8px;width:20px;height:20px;border-radius:5px;border:2px solid rgba(255,255,255,.5);background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;font-size:.75rem;cursor:pointer;z-index:2;transition:.1s}
.card-check.chk{background:#818cf8;border-color:#818cf8;color:#fff}
.card-vid-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.6);border-radius:4px;padding:2px 5px;font-size:.65rem;color:#fbbf24}

/* ── STATUS BAR ── */
.statusbar{padding:5px 14px;background:#13131e;border-top:1px solid #2d2d3d;font-size:.75rem;color:#64748b;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;gap:8px}
.load-more-wrap{text-align:center;padding:14px}
.spinner{display:none;width:24px;height:24px;border:3px solid #2d2d3d;border-top-color:#818cf8;border-radius:50%;animation:spin .7s linear infinite;margin:auto}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── LIGHTBOX ── */
.lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.95);z-index:900;flex-direction:column}
.lightbox.open{display:flex}
.lb-topbar{display:flex;align-items:center;gap:10px;padding:12px 16px;background:#1a1a24;flex-shrink:0;flex-wrap:wrap}
.lb-title{flex:1;font-size:.9rem;color:#e2e8f0;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lb-body{flex:1;display:flex;overflow:hidden}
.lb-media{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative}
.lb-media img,.lb-media video{max-width:100%;max-height:100%;object-fit:contain;border-radius:6px}
.lb-nav{position:absolute;top:50%;transform:translateY(-50%);background:rgba(255,255,255,.1);border:none;color:#fff;font-size:1.5rem;cursor:pointer;border-radius:8px;padding:12px 16px;transition:.15s}
.lb-nav:hover{background:rgba(255,255,255,.2)}
.lb-prev{left:12px}.lb-next{right:12px}
.lb-sidebar{width:280px;flex-shrink:0;background:#13131e;border-left:1px solid #2d2d3d;overflow-y:auto;padding:14px}
.lb-field{margin-bottom:14px}
.lb-label{font-size:.72rem;text-transform:uppercase;color:#64748b;margin-bottom:5px;letter-spacing:.07em}
.lb-value{font-size:.83rem;color:#e2e8f0;word-break:break-all}
.lb-reason{font-size:.78rem;color:#94a3b8;line-height:1.5}
.lb-tags-wrap{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.lb-tag-chip{display:flex;align-items:center;gap:4px;font-size:.75rem;padding:3px 8px;border-radius:99px;color:#fff}
.lb-tag-chip button{background:none;border:none;color:rgba(255,255,255,.7);cursor:pointer;font-size:.8rem;padding:0;line-height:1}
.lb-tag-chip button:hover{color:#fff}
.lb-tag-input-wrap{display:flex;gap:6px;margin-top:6px}
.lb-tag-input{flex:1;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:5px 8px;border-radius:6px;font-size:.82rem}
.lb-tag-input:focus{outline:none;border-color:#818cf8}
.cat-sel{width:100%;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:6px;border-radius:6px;font-size:.83rem}
.act-sel{width:100%;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:6px;border-radius:6px;font-size:.83rem}

/* ── MODALS ── */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:#1a1a24;border:1px solid #2d2d3d;border-radius:14px;padding:24px;min-width:340px;max-width:480px;width:90%}
.modal h2{font-size:1rem;font-weight:600;margin-bottom:14px}
.modal-row{margin-bottom:12px}
.modal-row label{font-size:.8rem;color:#94a3b8;display:block;margin-bottom:4px}
.modal-row input,.modal-row select{width:100%;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:7px 10px;border-radius:7px;font-size:.85rem}
.modal-row input:focus,.modal-row select:focus{outline:none;border-color:#818cf8}
.modal-btns{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}

/* ── NETWORK MODAL ── */
.qr-wrap{text-align:center;margin:12px 0}
.qr-wrap img{border-radius:8px;border:2px solid #3d3d55}
.url-box{background:#0f0f1a;border:1px solid #3d3d55;border-radius:6px;padding:8px 12px;font-size:.82rem;word-break:break-all;color:#a5b4fc;margin:6px 0}
.step-list{counter-reset:s}
.step-list li{counter-increment:s;display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;font-size:.82rem;color:#94a3b8;line-height:1.5}
.step-list li::before{content:counter(s);flex-shrink:0;width:20px;height:20px;background:#4338ca;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:.7rem;font-weight:700}

/* ── TAG PANEL INLINE (sidebar right side) ── */
.tags-add-row{padding:4px 10px 8px;display:flex;gap:5px}
.tags-add-row input{flex:1;background:#0f0f1a;border:1px solid #3d3d55;color:#e2e8f0;padding:5px 8px;border-radius:6px;font-size:.78rem}
.tags-add-row input:focus{outline:none;border-color:#818cf8}
.tags-add-row button{background:#4338ca;border:none;color:#fff;border-radius:6px;padding:5px 8px;cursor:pointer;font-size:.78rem}

/* ── VIEW / GROUP BAR ── */
.view-bar{display:flex;align-items:center;gap:8px;padding:7px 14px;background:#13131e;border-bottom:1px solid #2d2d3d;flex-shrink:0;flex-wrap:wrap}
.vg-pill{padding:4px 13px;border-radius:99px;border:1px solid #3d3d55;font-size:.78rem;cursor:pointer;color:#94a3b8;background:#0f0f1a;transition:.12s;white-space:nowrap}
.vg-pill.active{background:#4338ca;border-color:#818cf8;color:#fff}
.vg-pill:hover:not(.active){background:#1e1e2e;color:#e2e8f0}
.vg-icon{width:30px;height:30px;display:flex;align-items:center;justify-content:center;border-radius:7px;border:1px solid #3d3d55;background:#0f0f1a;color:#94a3b8;cursor:pointer;transition:.12s;flex-shrink:0}
.vg-icon.active,.vg-icon:hover{background:#4338ca;border-color:#818cf8;color:#fff}

/* ── SECTION HEADERS (grouped view) ── */
.dsec-wrap{margin-bottom:4px}
.dsec-hdr{display:flex;align-items:center;justify-content:space-between;padding:10px 6px;cursor:pointer;user-select:none;border-radius:8px;transition:.1s}
.dsec-hdr:hover{background:#1e1e2e}
.dsec-hdr-left{display:flex;align-items:baseline;gap:10px}
.dsec-hdr-label{font-size:.95rem;font-weight:700;color:#e2e8f0}
.dsec-hdr-count{font-size:.75rem;color:#64748b}
/* type tabs */
.type-tab-bar{display:flex;gap:6px;padding:10px 2px 14px;border-bottom:1px solid var(--surface3,#2d2d3d);margin-bottom:16px;flex-wrap:wrap}
.type-tab{display:flex;align-items:center;gap:8px;padding:8px 18px;border-radius:10px;border:1px solid var(--surface3,#2d2d3d);background:var(--surface2,#1e1e2a);color:#94a3b8;font-size:.85rem;font-weight:600;cursor:pointer;transition:all .15s}
.type-tab:hover{background:#23233a;color:#e2e8f0}
.type-tab.active{background:#2d2d4d;color:#818cf8;border-color:#818cf8}
.type-tab-count{font-size:.72rem;background:rgba(255,255,255,.07);padding:1px 7px;border-radius:99px;font-weight:700}
.type-tab.active .type-tab-count{background:rgba(129,140,248,.18)}
.dsec-body{overflow:hidden}

/* ── LIST VIEW ── */
.desk-list-view{display:flex;flex-direction:column;gap:1px}
.list-row{display:flex;align-items:center;gap:12px;padding:7px 6px;border-radius:8px;cursor:pointer;transition:.1s}
.list-row:hover{background:#1e1e2e}
.list-thumb{width:46px;height:46px;border-radius:7px;object-fit:cover;flex-shrink:0;background:#0f0f1a;display:flex;align-items:center;justify-content:center;font-size:1.3rem;color:#4d4d6e;overflow:hidden}
.list-thumb img{width:100%;height:100%;object-fit:cover;border-radius:7px}
.list-body{flex:1;min-width:0}
.list-name{font-size:.83rem;color:#e2e8f0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:500}
.list-sub{font-size:.72rem;color:#64748b;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list-right{text-align:right;flex-shrink:0;font-size:.72rem;color:#64748b}
.list-tags{display:flex;gap:3px;flex-wrap:wrap;margin-top:3px}
.list-tag{font-size:.6rem;padding:1px 6px;border-radius:99px;color:#fff}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:#0f0f13}::-webkit-scrollbar-thumb{background:#2d2d4d;border-radius:3px}

/* ── RESPONSIVE ── */
@media(max-width:700px){
  .sidebar{display:none}
  .lb-sidebar{display:none}
  .lb-nav{padding:8px 10px;font-size:1.2rem}
}
</style>
</head>
<body>

<!-- TOP BAR -->
<div class="topbar">
  <span class="logo">🗂 AI Classifier</span>
  <span style="font-size:.65rem;color:#64748b;letter-spacing:.04em;margin-left:-4px;flex-shrink:0">v__APP_VER__</span>
  <div class="search-wrap">
    <input id="searchInput" type="search" placeholder="Search… use #tag to filter by tag" autocomplete="off">
    <button class="btn btn-primary" onclick="doSearch()">Search</button>
  </div>
  <div class="topbar-right">
    <select class="sort-sel" id="sortSel" onchange="doSearch()">
      <option value="recent" selected>Newest First</option>
      <option value="oldest">Oldest First</option>
      <option value="name_az">Name A-Z</option>
      <option value="name_za">Name Z-A</option>
      <option value="size_big">Largest</option>
      <option value="size_sml">Smallest</option>
      <option value="conf">Confidence</option>
    </select>
    <button class="btn btn-ghost btn-sm" onclick="openNetModal()">📶 Connect</button>
    <button class="btn btn-ghost btn-sm" onclick="openSettingsModal()">⚙️ Settings</button>
    <a href="/admin" class="btn btn-ghost btn-sm">🛠 Admin</a>
    <a href="/mobile" target="_blank" class="btn btn-ghost btn-sm">📱 Mobile</a>
    <div style="width:1px;height:20px;background:#2d2d3d;margin:0 2px"></div>
    <button class="vg-icon active" id="dv-grid" onclick="setDeskView('grid')" title="Grid view">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
    </button>
    <button class="vg-icon" id="dv-list" onclick="setDeskView('list')" title="List view">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
    </button>
  </div>
</div>

<!-- LAYOUT -->
<div class="layout">

  <!-- SIDEBAR -->
  <div class="sidebar" id="sidebar">
    <!-- Folders -->
    <div class="sidebar-section">
      <div class="sidebar-title">Folders
        <button class="btn btn-sm btn-ghost" style="font-size:.7rem;padding:2px 7px" onclick="openFolderModal()">+ Add</button>
      </div>
      <div id="folderList"></div>
    </div>

    <!-- Categories -->
    <div class="sidebar-section">
      <div class="sidebar-title">Categories
        <button class="btn btn-sm btn-ghost" id="addCatBtn" style="font-size:.7rem;padding:2px 7px;display:none" onclick="openAddCatModal()">+</button>
      </div>
      <div class="sidebar-item" onclick="setFilter('category','');setFilter('action','')" id="catAll">All Files <span class="sidebar-count" id="totalCount">0</span></div>
      <div id="categoryList"></div>
    </div>

    <!-- Action -->
    <div class="sidebar-section">
      <div class="sidebar-title">Action</div>
      <div class="sidebar-item" onclick="setFilter('action','keep')">✅ Keep</div>
      <div class="sidebar-item" onclick="setFilter('action','review')">👁 Review</div>
      <div class="sidebar-item" onclick="setFilter('action','delete')">🗑 Delete</div>
    </div>

    <!-- Tags -->
    <div class="sidebar-section">
      <div class="sidebar-title">Tags</div>
      <div class="tags-add-row">
        <input id="newTagInput" placeholder="New tag…" onkeydown="if(event.key==='Enter')addGlobalTag()">
        <button onclick="addGlobalTag()">+</button>
      </div>
      <div id="tagList"></div>
    </div>
  </div>

  <!-- MAIN -->
  <div class="main">
    <!-- Group / view bar -->
    <div class="view-bar">
      <button class="vg-pill" id="dg-all" onclick="setDeskGroup('all')">All Files</button>
      <button class="vg-pill active" id="dg-date" onclick="setDeskGroup('date')">By Date</button>
      <button class="vg-pill" id="dg-type" onclick="setDeskGroup('type')">By Type</button>
    </div>
    <!-- Filter strip -->
    <div class="filters-strip" id="filtersStrip" style="display:none">
      <span class="filter-label">Filter:</span>
      <span id="activeFiltersDisplay"></span>
      <button class="btn btn-sm btn-ghost" style="margin-left:auto" onclick="clearAllFilters()">Clear</button>
    </div>

    <!-- Bulk bar -->
    <div class="bulk-bar" id="bulkBar">
      <span class="bulk-count" id="bulkCount">0 selected</span>
      <select id="bulkCatSel" class="sort-sel" style="background:#0f0f1a">
        <option value="">– Retag category –</option>
      </select>
      <select id="bulkActSel" class="sort-sel" style="background:#0f0f1a">
        <option value="">– Set action –</option>
        <option value="keep">✅ Keep</option>
        <option value="review">👁 Review</option>
        <option value="delete">🗑 Delete</option>
      </select>
      <button class="btn btn-sm btn-ghost" onclick="openBulkTagModal()">🏷 Tag</button>
      <button class="btn btn-sm btn-danger" onclick="bulkDelete()">🗑 Delete</button>
      <button class="btn btn-sm btn-ghost" onclick="clearSelection()">✕</button>
    </div>

    <!-- Grid -->
    <div class="grid-container" id="gridContainer" onscroll="onGridScroll()">
      <div class="grid" id="grid"></div>
      <div class="load-more-wrap"><div class="spinner" id="spinner"></div></div>
    </div>

    <!-- Status bar -->
    <div class="statusbar">
      <span id="statusText">Loading…</span>
      <span id="netInfo" style="color:#4338ca;cursor:pointer" onclick="openNetModal()"></span>
    </div>
  </div>
</div>

<!-- ── LIGHTBOX ── -->
<div class="lightbox" id="lightbox">
  <div class="lb-topbar">
    <span class="lb-title" id="lbTitle"></span>
    <button class="btn btn-ghost btn-sm" onclick="openFile()">Open</button>
    <button class="btn btn-ghost btn-sm" onclick="revealFile()">Show in Folder</button>
    <button class="btn btn-danger btn-sm" onclick="lbDelete()">Delete</button>
    <button class="btn btn-ghost btn-sm" onclick="closeLb()">✕</button>
  </div>
  <div class="lb-body">
    <div class="lb-media">
      <button class="lb-nav lb-prev" onclick="lbNav(-1)">‹</button>
      <div id="lbMediaWrap"></div>
      <button class="lb-nav lb-next" onclick="lbNav(1)">›</button>
    </div>
    <div class="lb-sidebar">
      <div class="lb-field">
        <div class="lb-label">Category</div>
        <select class="cat-sel" id="lbCatSel" onchange="lbSaveCat()"></select>
      </div>
      <div class="lb-field">
        <div class="lb-label">Action</div>
        <select class="act-sel" id="lbActSel" onchange="lbSaveAct()">
          <option value="keep">✅ Keep</option>
          <option value="review">👁 Review</option>
          <option value="delete">🗑 Delete</option>
        </select>
      </div>
      <div class="lb-field">
        <div class="lb-label">Tags</div>
        <div class="lb-tags-wrap" id="lbTagsWrap"></div>
        <div class="lb-tag-input-wrap">
          <input class="lb-tag-input" id="lbTagInput" placeholder="Add tag…" onkeydown="if(event.key==='Enter')lbAddTag()">
          <button class="btn btn-sm btn-primary" onclick="lbAddTag()">+</button>
        </div>
      </div>
      <div class="lb-field">
        <div class="lb-label">AI Caption</div>
        <div class="lb-reason" id="lbAiCaption" style="font-style:italic;color:#94a3b8"></div>
      </div>
      <div class="lb-field">
        <div class="lb-label">AI Description</div>
        <div class="lb-reason" id="lbAiDesc" style="font-size:.78rem;line-height:1.5;color:#94a3b8"></div>
      </div>
      <div class="lb-field" id="lbOcrField" style="display:none">
        <div class="lb-label">📝 Text in Image</div>
        <div class="lb-reason" id="lbOcrText" style="font-size:.75rem;line-height:1.5;max-height:120px;overflow-y:auto;white-space:pre-wrap;color:#94a3b8"></div>
      </div>
      <div class="lb-field">
        <div class="lb-label">AI Reason</div>
        <div class="lb-reason" id="lbReason"></div>
      </div>
      <div class="lb-field">
        <div class="lb-label">Issues</div>
        <div class="lb-reason" id="lbIssues"></div>
      </div>
      <div class="lb-field">
        <div class="lb-label">Confidence</div>
        <div class="lb-value" id="lbConf"></div>
      </div>
      <div class="lb-field">
        <div class="lb-label">File</div>
        <div class="lb-value" id="lbPath" style="font-size:.72rem;color:#64748b"></div>
      </div>
    </div>
  </div>
</div>

<!-- ── MODALS ── -->
<!-- Delete confirm -->
<div class="modal-bg" id="delModal">
  <div class="modal">
    <h2>🗑 Delete Files</h2>
    <p style="font-size:.85rem;color:#94a3b8;margin-bottom:14px">Delete <strong id="delCount"></strong> file(s)? They will be moved to the Recycle Bin.</p>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal('delModal')">Cancel</button>
      <button class="btn btn-danger" id="delConfirmBtn">Delete</button>
    </div>
  </div>
</div>

<!-- Bulk tag -->
<div class="modal-bg" id="bulkTagModal">
  <div class="modal">
    <h2>🏷 Add Tags to Selected</h2>
    <div class="modal-row">
      <label>Tag names (comma-separated)</label>
      <input id="bulkTagInput" placeholder="e.g. gym, 2024, london">
    </div>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal('bulkTagModal')">Cancel</button>
      <button class="btn btn-primary" onclick="bulkTag()">Apply Tags</button>
    </div>
  </div>
</div>

<!-- Add folder -->
<div class="modal-bg" id="folderModal">
  <div class="modal">
    <h2>📁 Add Folder</h2>
    <div class="modal-row">
      <label>Folder path</label>
      <input id="folderPathInput" placeholder="C:\Users\you\Pictures">
    </div>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal('folderModal')">Cancel</button>
      <button class="btn btn-primary" onclick="submitFolder()">Add</button>
    </div>
  </div>
</div>

<!-- Add category -->
<div class="modal-bg" id="addCatModal">
  <div class="modal">
    <h2>➕ Add Category</h2>
    <div class="modal-row"><label>Label</label><input id="newCatLabel" placeholder="e.g. Work Trips"></div>
    <div class="modal-row"><label>Slug (auto)</label><input id="newCatSlug" placeholder="work_trips"></div>
    <div class="modal-row"><label>Output Folder</label><input id="newCatFolder" placeholder="Work" value="Other"></div>
    <div class="modal-row"><label>Description</label><input id="newCatDesc" placeholder="Optional"></div>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal('addCatModal')">Cancel</button>
      <button class="btn btn-primary" onclick="submitNewCat()">Add</button>
    </div>
  </div>
</div>

<!-- Network / connect -->
<div class="modal-bg" id="netModal">
  <div class="modal" style="max-width:400px">
    <h2>📶 Connect Mobile Device</h2>
    <div id="netModalBody" style="font-size:.84rem;color:#94a3b8">Loading…</div>
    <div class="modal-btns"><button class="btn btn-ghost" onclick="closeModal('netModal')">Close</button></div>
  </div>
</div>

<!-- Settings -->
<div class="modal-bg" id="settingsModal">
  <div class="modal">
    <h2>⚙️ Settings</h2>
    <div class="modal-row"><label>Upload folder (for phone uploads)</label><input id="settingUploadFolder" placeholder="Default: Pictures\Phone Uploads"></div>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal('settingsModal')">Cancel</button>
      <button class="btn btn-primary" onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>

<script>
// ── STATE ──────────────────────────────────────────────────────────────────
let page=1, totalItems=0, loading=false, allLoaded=false;
let activeFilter={category:'',action:'',folder:'',tag:''};
let selectedPaths=new Set(), results=[], lbIdx=0;
let allCategories=[], allTags=[], allFolders=[];
let activeFolderId=null, activeFolderPath='';
let deskView='grid', deskGroup='date';
let _deskGroupedLabels=[], _deskGroupedData={}, _deskCollapsed=new Set();
let _deskActiveTypeTab='Images';

// ── INIT ───────────────────────────────────────────────────────────────────
window.onload = async()=>{
  await Promise.all([loadFolders(), loadCategories(), loadTags(), loadNetInfo()]);
  // Apply default grouped state
  setDeskGroup(deskGroup);
};

// ── NETWORK INFO ───────────────────────────────────────────────────────────
async function loadNetInfo(){
  try{
    const d=await fetch('/api/network').then(r=>r.json());
    document.getElementById('netInfo').textContent=`LAN: ${d.local_ip}:${d.port}`;
  }catch(e){}
}
async function openNetModal(){
  openModal('netModal');
  const d=await fetch('/api/network').then(r=>r.json());
  let html='';
  html+=`<p style="margin-bottom:10px">Your PC is accessible on the local network at:</p>`;
  html+=`<div class="url-box">${d.local_url}/mobile</div>`;
  if(d.has_qr){
    html+=`<div class="qr-wrap"><img src="/qr.png?url=${encodeURIComponent(d.mobile_url)}" width="180" height="180" alt="QR"></div>`;
  }
  html+=`<p style="margin:10px 0 6px;font-size:.8rem;color:#64748b">📱 On your phone, open the camera and scan the QR code — or type the URL above.</p>`;
  if(d.tailscale_url){
    html+=`<p style="margin:10px 0 4px;font-weight:600">🌍 Internet (Tailscale) URL:</p>`;
    html+=`<div class="url-box">${d.tailscale_url}/mobile</div>`;
  } else {
    html+=`<p style="margin:10px 0 4px;font-size:.8rem;color:#64748b"><strong>For internet access:</strong> Install <a href="https://tailscale.com/download" target="_blank" style="color:#818cf8">Tailscale</a> on both PC and phone, then sign in with the same account.</p>`;
  }
  document.getElementById('netModalBody').innerHTML=html;
}

// ── LOAD DATA ──────────────────────────────────────────────────────────────
async function loadFolders(){
  const rows=await fetch('/api/folders').then(r=>r.json());
  allFolders=rows;
  const el=document.getElementById('folderList');
  el.innerHTML='';
  rows.forEach(f=>{
    const d=document.createElement('div');
    d.className='sidebar-item'+(f.path===activeFolderPath?' active':'');
    d.dataset.path=f.path; d.dataset.id=f.id;
    d.innerHTML=`<span>${f.display_name||f.path.split(/[\\/]/).pop()}</span><span class="sidebar-count">${f.n||0}</span>`;
    d.onclick=()=>selectFolder(f);
    el.appendChild(d);
  });
}

async function loadCategories(){
  const url='/api/categories'+(activeFolderPath?`?folder=${encodeURIComponent(activeFolderPath)}`:'');
  const rows=await fetch(url).then(r=>r.json());
  allCategories=rows;
  const el=document.getElementById('categoryList');
  el.innerHTML='';
  document.getElementById('addCatBtn').style.display=activeFolderId?'':'none';
  rows.forEach(r=>{
    const cat=r.category||r.slug||''; const n=r.n||0; const label=r.label||cat; const isBuiltin=r.is_builtin;
    const d=document.createElement('div');
    d.className='sidebar-item'+(activeFilter.category===cat?' active':'');
    d.innerHTML=`<span>${label}</span><div style="display:flex;align-items:center;gap:4px"><span class="sidebar-count">${n}</span>${activeFolderId&&!isBuiltin?`<button class="sidebar-del" title="Delete category" onclick="event.stopPropagation();deleteCat(${r.id})">×</button>`:''}</div>`;
    d.onclick=()=>{ setFilter('category',cat==='other'?cat:(activeFilter.category===cat?'':cat)); };
    el.appendChild(d);
  });
  // Populate bulk category selector
  const bsel=document.getElementById('bulkCatSel');
  bsel.innerHTML='<option value="">– Retag category –</option>';
  rows.forEach(r=>{ const o=document.createElement('option'); o.value=r.category||r.slug||''; o.textContent=r.label||o.value; bsel.appendChild(o); });
  // Populate lightbox category selector
  const lsel=document.getElementById('lbCatSel');
  lsel.innerHTML='';
  rows.forEach(r=>{ const o=document.createElement('option'); o.value=r.category||r.slug||''; o.textContent=r.label||o.value; lsel.appendChild(o); });
}

async function loadTags(){
  const rows=await fetch('/api/tags').then(r=>r.json());
  allTags=rows;
  const el=document.getElementById('tagList');
  el.innerHTML='';
  rows.forEach(t=>{
    const d=document.createElement('div');
    d.className='sidebar-item'+(activeFilter.tag===t.name?' active':'');
    d.innerHTML=`<span><span class="tag-dot" style="background:${t.color}"></span>${t.name}</span><div style="display:flex;align-items:center;gap:4px"><span class="sidebar-count">${t.n}</span><button class="sidebar-del" onclick="event.stopPropagation();deleteTag(${t.id})" title="Delete tag">×</button></div>`;
    d.onclick=()=>setFilter('tag', activeFilter.tag===t.name?'':t.name);
    el.appendChild(d);
  });
}

function selectFolder(f){
  activeFolderId=f.id; activeFolderPath=f.path;
  setFilter('folder', activeFilter.folder===f.path?'':f.path);
  loadCategories();
}

// ── SEARCH ─────────────────────────────────────────────────────────────────
function doSearch(reset=true){
  if(reset){ page=1; allLoaded=false; document.getElementById('grid').innerHTML=''; results=[]; }
  if(deskGroup!=='all'){ loadDeskGrouped(); return; }
  loadPage();
}

async function loadPage(){
  if(loading||allLoaded) return;
  loading=true;
  document.getElementById('spinner').style.display='block';
  const q=document.getElementById('searchInput').value.trim();
  const sort=document.getElementById('sortSel').value;
  const params=new URLSearchParams({q,sort,page,per_page:60,...activeFilter});
  try{
    const d=await fetch('/api/search?'+params).then(r=>r.json());
    totalItems=d.total;
    if(page===1) results=d.results; else results=[...results,...d.results];
    renderGrid(d.results, page>1);
    document.getElementById('statusText').textContent=`${totalItems} files${activeFilter.category?' · '+activeFilter.category:''}`;
    document.getElementById('totalCount').textContent=totalItems;
    if(d.results.length<60) allLoaded=true; else page++;
  } catch(e){ console.error(e); }
  loading=false;
  document.getElementById('spinner').style.display='none';
}

function onGridScroll(){
  if(deskGroup!=='all') return;
  const gc=document.getElementById('gridContainer');
  if(gc.scrollHeight-gc.scrollTop-gc.clientHeight<200) loadPage();
}

// ── RENDER ─────────────────────────────────────────────────────────────────
const VIDEO_EXTS=new Set(['.mp4','.mov','.avi','.mkv','.webm','.m4v','.wmv','.flv','.3gp','.ts','.mts','.m2ts']);
function isVideo(p){ return VIDEO_EXTS.has((p||'').split('.').pop().toLowerCase().replace(/^/,'.').replace(/^\.\./,'.')); }
function ext(p){ const parts=(p||'').split('.'); return parts.length>1?'.'+parts.pop().toLowerCase():''; }
function isVid(p){ return VIDEO_EXTS.has(ext(p)); }

function renderGrid(items, append=false){
  const grid=document.getElementById('grid');
  grid.className=deskView==='grid'?'grid':'desk-list-view';
  if(!append) grid.innerHTML='';
  items.forEach((item,i)=>{
    const idx=append? results.length-items.length+i : i;
    if(deskView==='list'){ _appendDeskListRow(item,idx); return; }
    const isV=isVid(item.path);
    const card=document.createElement('div');
    card.className='card'+(selectedPaths.has(item.path)?' selected':'');
    card.dataset.idx=idx;

    // Checkbox
    const chk=document.createElement('div');
    chk.className='card-check'+(selectedPaths.has(item.path)?' chk':'');
    chk.innerHTML=selectedPaths.has(item.path)?'✓':'';
    chk.onclick=(e)=>{ e.stopPropagation(); toggleSelect(item.path,card,chk); };
    card.appendChild(chk);

    // Media
    const ep=encodeURIComponent(item.path);
    if(isV){
      const img=document.createElement('img');
      img.src=`/thumb?path=${ep}`;
      img.onerror=()=>{ img.src=''; img.style.display='none'; const ic=document.createElement('div'); ic.className='thumb-wrap'; ic.textContent='🎬'; img.parentNode.insertBefore(ic,img); };
      card.appendChild(img);
      const badge=document.createElement('div');
      badge.className='card-vid-badge'; badge.textContent='▶ VIDEO';
      card.appendChild(badge);
    } else {
      const img=document.createElement('img');
      img.loading='lazy';
      img.src=`/img?path=${ep}`;
      img.onerror=()=>{ img.style.display='none'; const ic=document.createElement('div'); ic.className='thumb-wrap'; ic.textContent='🖼'; img.parentNode.insertBefore(ic,img); };
      card.appendChild(img);
    }

    // Overlay
    const ov=document.createElement('div');
    ov.className='card-overlay';
    const nm=item.path.split(/[\\/]/).pop();
    let tagHtml='';
    if(item.tags&&item.tags.length){
      tagHtml='<div class="card-tags">';
      item.tags.slice(0,3).forEach(t=>{ tagHtml+=`<span class="card-tag" style="background:${t.color}">${t.name}</span>`; });
      tagHtml+='</div>';
    }
    ov.innerHTML=`<div class="card-name">${nm}</div><div class="card-cat">${item.category||''}</div>${tagHtml}`;
    card.appendChild(ov);

    card.onclick=(e)=>{ if(e.ctrlKey||e.metaKey){ toggleSelect(item.path,card,chk); } else if(e.shiftKey&&selectedPaths.size>0){ rangeSelect(idx); } else { openLb(idx); } };
    grid.appendChild(card);
  });
}


// ── DESKTOP VIEW / GROUP CONTROLS ─────────────────────────────────────────
function setDeskView(v){
  deskView=v;
  document.getElementById('dv-grid').classList.toggle('active',v==='grid');
  document.getElementById('dv-list').classList.toggle('active',v==='list');
  // Update card grid size for list vs grid
  if(deskGroup==='all'){
    document.getElementById('grid').className=v==='grid'?'grid':'desk-list-view';
    document.getElementById('grid').innerHTML='';
    results.forEach((item,i)=>{ v==='grid'?_appendDeskCard(item,i):_appendDeskListRow(item,i); });
  } else {
    renderDeskGrouped();
  }
}

function setDeskGroup(g){
  deskGroup=g;
  ['all','date','type'].forEach(x=>document.getElementById('dg-'+x).classList.toggle('active',x===g));
  const sortSel=document.getElementById('sortSel');
  sortSel.style.opacity=g==='all'?'1':'0.5';
  sortSel.style.pointerEvents=g==='all'?'':'none';
  doSearch();
}

// ── GROUPED LOAD ────────────────────────────────────────────────────────────
async function loadDeskGrouped(){
  document.getElementById('spinner').style.display='block';
  document.getElementById('grid').innerHTML='';
  const q=document.getElementById('searchInput').value.trim();
  const params=new URLSearchParams({q,sort:'recent',page:1,per_page:1500,...activeFilter});
  try{
    const d=await fetch('/api/search?'+params).then(r=>r.json());
    results=d.results||[];
    totalItems=d.total;
    document.getElementById('statusText').textContent=`${totalItems} files`;
    document.getElementById('totalCount').textContent=totalItems;
    // Group
    const labels=[], groups={};
    const IMAGE_EXTS=new Set(['.jpg','.jpeg','.png','.gif','.webp','.heic','.heif','.bmp','.tiff','.tif','.avif']);
    const isImg=p=>IMAGE_EXTS.has((p||'').split('.').pop().replace(/^/,'.').toLowerCase().replace(/^\.\./,'.'));
    if(deskGroup==='date'){
      results.forEach(f=>{
        const date=_deskFileDate(f);
        const label=_deskMonthLabel(date);
        const key=_deskMonthSortKey(date);
        if(!groups[label]){ groups[label]=[]; labels.push({label,key}); }
        groups[label].push(f);
      });
      labels.sort((a,b)=>b.key.localeCompare(a.key));
      _deskGroupedLabels=labels.map(x=>x.label);
    } else {
      // By Type: always show all 3 tabs even if empty
      const imgExts=new Set(['.jpg','.jpeg','.png','.gif','.webp','.heic','.heif','.bmp','.tiff','.tif','.avif']);
      const vidExts=new Set(['.mp4','.mov','.avi','.mkv','.wmv','.m4v','.3gp','.ts','.mts','.mxf','.flv','.webm']);
      const typeGroups={Images:[],Videos:[],'Other Files':[]};
      results.forEach(f=>{
        const ft=(f.file_type||'').toLowerCase();
        const ext='.'+((f.path||'').split('.').pop()||'').toLowerCase();
        if(ft==='image'||imgExts.has(ext)) typeGroups.Images.push(f);
        else if(ft==='video'||vidExts.has(ext)) typeGroups.Videos.push(f);
        else typeGroups['Other Files'].push(f);
      });
      _deskGroupedLabels=['Images','Videos','Other Files'];
      _deskGroupedData=typeGroups;
      renderDeskGrouped();
      return; // already rendered
    }
    _deskGroupedData=groups;
    renderDeskGrouped();
  }catch(e){ console.error(e); }
  document.getElementById('spinner').style.display='none';
}

function _deskFileDate(f){
  const name=(f.path||'').split(/[\\/]/).pop();
  const m=name.match(/(\d{4})(\d{2})(\d{2})/)||name.match(/(\d{4})-(\d{2})-(\d{2})/);
  if(m){ const d=new Date(`${m[1]}-${m[2]}-${m[3]}`); if(!isNaN(d)) return d; }
  if(f.analyzed_at){ const d=new Date(f.analyzed_at); if(!isNaN(d)) return d; }
  return new Date(0);
}
function _deskMonthLabel(d){ return d.getFullYear()===1970?'Unknown Date':d.toLocaleString('default',{month:'long',year:'numeric'}); }
function _deskMonthSortKey(d){ return d.getFullYear()===1970?'0000-00':`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; }

function renderDeskGrouped(){
  const grid=document.getElementById('grid');
  grid.className='';
  if(!_deskGroupedLabels.length){ grid.innerHTML='<div style="text-align:center;padding:60px 20px;color:#64748b">No files found</div>'; return; }

  if(deskGroup==='type'){
    // Validate active tab still exists
    if(!_deskGroupedLabels.includes(_deskActiveTypeTab)) _deskActiveTypeTab=_deskGroupedLabels[0];
    const icons={Images:'🖼',Videos:'🎬','Other Files':'📄'};
    const tabs=_deskGroupedLabels.map(label=>{
      const cnt=(_deskGroupedData[label]||[]).length;
      return `<button class="type-tab${label===_deskActiveTypeTab?' active':''}" onclick="switchDeskTypeTab('${label}')">${icons[label]||'📁'} ${label} <span class="type-tab-count">${cnt}</span></button>`;
    }).join('');
    const files=_deskGroupedData[_deskActiveTypeTab]||[];
    const inner=files.length===0
      ?`<div style="text-align:center;padding:60px 20px;color:#64748b">No ${_deskActiveTypeTab.toLowerCase()} in this library</div>`
      :deskView==='grid'
        ?`<div class="grid">${files.map((f,i)=>_deskCardHTML(f,i)).join('')}</div>`
        :`<div class="desk-list-view">${files.map((f,i)=>_deskListRowHTML(f,i)).join('')}</div>`;
    grid.innerHTML=`<div class="type-tab-bar">${tabs}</div><div>${inner}</div>`;
    grid.querySelectorAll('[data-ridx]').forEach(el=>{
      const idx=parseInt(el.dataset.ridx);
      el.onclick=(e)=>{ if(!e.target.classList.contains('card-check')) openLb(idx); };
      const chk=el.querySelector('.card-check');
      if(chk) chk.onclick=(e)=>{ e.stopPropagation(); toggleSelect(results[idx].path,el,chk); };
    });
    return;
  }

  grid.innerHTML=_deskGroupedLabels.map(label=>{
    const files=_deskGroupedData[label]||[];
    const secId='dsec-'+label.replace(/[\s\W]+/g,'-');
    const collapsed=_deskCollapsed.has(label);
    const inner=deskView==='grid'
      ?`<div class="grid" style="padding-bottom:4px">${files.map((f,i)=>_deskCardHTML(f,i)).join('')}</div>`
      :`<div class="desk-list-view">${files.map((f,i)=>_deskListRowHTML(f,i)).join('')}</div>`;
    const chevRot=collapsed?'rotate(-90deg)':'rotate(0deg)';
    return `<div class="dsec-wrap">
      <div class="dsec-hdr" onclick="toggleDeskSection('${label.replace(/'/g,"\\'")}','${secId}')">
        <div class="dsec-hdr-left">
          <span class="dsec-hdr-label">${label}</span>
          <span class="dsec-hdr-count">${files.length} item${files.length!==1?'s':''}</span>
        </div>
        <svg id="dchev-${secId}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="transition:transform .22s;transform:${chevRot};flex-shrink:0"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <div id="${secId}" style="display:${collapsed?'none':'block'}">${inner}</div>
    </div>`;
  }).join('');
  // After render, attach onclick to each card so lightbox works (finds item index in results[])
  grid.querySelectorAll('[data-ridx]').forEach(el=>{
    const idx=parseInt(el.dataset.ridx);
    el.onclick=(e)=>{ if(!e.target.classList.contains('card-check')) openLb(idx); };
    const chk=el.querySelector('.card-check');
    if(chk) chk.onclick=(e)=>{ e.stopPropagation(); toggleSelect(results[idx].path,el,chk); };
  });
}

function toggleDeskSection(label, secId){
  const el=document.getElementById(secId);
  const chev=document.getElementById('dchev-'+secId);
  if(!el) return;
  const collapsed=el.style.display==='none';
  el.style.display=collapsed?'block':'none';
  if(chev) chev.style.transform=collapsed?'rotate(0deg)':'rotate(-90deg)';
  if(collapsed) _deskCollapsed.delete(label); else _deskCollapsed.add(label);
}

function switchDeskTypeTab(label){
  _deskActiveTypeTab=label;
  renderDeskGrouped();
}

function _deskFileSize(f){ const b=f.file_size||f.size_bytes||0; if(!b) return ''; if(b<1024) return b+'B'; if(b<1048576) return (b/1024).toFixed(0)+'KB'; return (b/1048576).toFixed(1)+'MB'; }

function _deskCardHTML(f, localIdx){
  const ridx=results.indexOf(f); // index in global results for lightbox
  const ep=encodeURIComponent(f.path);
  const nm=f.path.split(/[\\/]/).pop();
  const isV=isVid(f.path);
  const sel=selectedPaths.has(f.path);
  const tagHtml=(f.tags&&f.tags.length)?'<div class="card-tags">'+f.tags.slice(0,3).map(t=>`<span class="card-tag" style="background:${t.color}">${t.name}</span>`).join('')+'</div>':'';
  const media=isV
    ?`<img src="/thumb?path=${ep}" onerror="this.style.display='none'"><div class="card-vid-badge">▶ VIDEO</div>`
    :`<img loading="lazy" src="/thumb?path=${ep}" onerror="this.style.display='none'">`;
  return `<div class="card${sel?' selected':''}" data-ridx="${ridx}">
    <div class="card-check${sel?' chk':''}">${sel?'✓':''}</div>
    ${media}
    <div class="card-overlay"><div class="card-name">${nm}</div><div class="card-cat">${f.category||''}</div>${tagHtml}</div>
  </div>`;
}

function _deskListRowHTML(f, localIdx){
  const ridx=results.indexOf(f);
  const ep=encodeURIComponent(f.path);
  const nm=f.path.split(/[\\/]/).pop();
  const isV=isVid(f.path);
  const sz=_deskFileSize(f);
  const date=_deskFileDate(f);
  const dateStr=date.getFullYear()===1970?'':date.toLocaleDateString('default',{day:'numeric',month:'short',year:'numeric'});
  const tagHtml=(f.tags&&f.tags.length)?'<div class="list-tags">'+f.tags.slice(0,3).map(t=>`<span class="list-tag" style="background:${t.color}">${t.name}</span>`).join('')+'</div>':'';
  const thumb=isV
    ?`<div class="list-thumb">🎬</div>`
    :`<div class="list-thumb"><img src="/thumb?path=${ep}" loading="lazy" onerror="this.src='';this.parentNode.textContent='🖼'"></div>`;
  return `<div class="list-row" data-ridx="${ridx}">
    <div class="card-check${selectedPaths.has(f.path)?' chk':''}">${selectedPaths.has(f.path)?'✓':''}</div>
    ${thumb}
    <div class="list-body">
      <div class="list-name">${nm}</div>
      <div class="list-sub">${f.category||''}${f.category&&f.ai_caption?' · ':''}${f.ai_caption?f.ai_caption.slice(0,60)+(f.ai_caption.length>60?'…':''):''}</div>
      ${f.ocr_text?`<div style="font-size:.72rem;color:var(--text3);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:480px">📝 ${f.ocr_text.slice(0,100)}${f.ocr_text.length>100?'…':''}</div>`:''}
      ${tagHtml}
    </div>
    <div class="list-right"><div>${sz}</div><div style="margin-top:3px">${dateStr}</div></div>
  </div>`;
}

// Keep legacy DOM-based appenders for "all files" flat mode
function _appendDeskCard(item, idx){ const tmp=document.createElement('div'); tmp.innerHTML=_deskCardHTML(item,idx); const card=tmp.firstElementChild; card.onclick=(e)=>{ if(!e.target.classList.contains('card-check')) openLb(idx); }; const chk=card.querySelector('.card-check'); if(chk) chk.onclick=(e)=>{ e.stopPropagation(); toggleSelect(item.path,card,chk); }; document.getElementById('grid').appendChild(card); }
function _appendDeskListRow(item, idx){ const tmp=document.createElement('div'); tmp.innerHTML=_deskListRowHTML(item,idx); const row=tmp.firstElementChild; row.onclick=(e)=>{ if(!e.target.classList.contains('card-check')) openLb(idx); }; const chk=row.querySelector('.card-check'); if(chk) chk.onclick=(e)=>{ e.stopPropagation(); toggleSelect(item.path,row,chk); }; document.getElementById('grid').appendChild(row); }

// ── SELECTION ──────────────────────────────────────────────────────────────
function toggleSelect(path,card,chk){
  if(selectedPaths.has(path)){ selectedPaths.delete(path); card.classList.remove('selected'); chk.classList.remove('chk'); chk.textContent=''; }
  else { selectedPaths.add(path); card.classList.add('selected'); chk.classList.add('chk'); chk.textContent='✓'; }
  updateBulkBar();
}
function rangeSelect(toIdx){
  const keys=[...selectedPaths];
  if(!keys.length) return;
  const firstIdx=parseInt(document.querySelector('.card.selected')?.dataset.idx??'0');
  const lo=Math.min(firstIdx,toIdx), hi=Math.max(firstIdx,toIdx);
  for(let i=lo;i<=hi&&i<results.length;i++) selectedPaths.add(results[i].path);
  document.querySelectorAll('.card').forEach(c=>{
    const i=parseInt(c.dataset.idx);
    if(i>=lo&&i<=hi){ c.classList.add('selected'); const chk=c.querySelector('.card-check'); chk.classList.add('chk'); chk.textContent='✓'; }
  });
  updateBulkBar();
}
function clearSelection(){ selectedPaths.clear(); document.querySelectorAll('.card.selected').forEach(c=>{ c.classList.remove('selected'); const chk=c.querySelector('.card-check'); chk.classList.remove('chk'); chk.textContent=''; }); updateBulkBar(); }
function updateBulkBar(){
  const n=selectedPaths.size;
  document.getElementById('bulkBar').classList.toggle('visible',n>0);
  document.getElementById('bulkCount').textContent=`${n} selected`;
}

// ── FILTERS ───────────────────────────────────────────────────────────────
function setFilter(key,val){
  activeFilter[key]=val;
  const strip=document.getElementById('filtersStrip');
  const active=Object.entries(activeFilter).filter(([,v])=>v);
  strip.style.display=active.length?'flex':'none';
  document.getElementById('activeFiltersDisplay').innerHTML=active.map(([k,v])=>`<span class="filter-chip active">${k}: ${v} <span onclick="setFilter('${k}','');event.stopPropagation()" style="cursor:pointer;margin-left:4px">×</span></span>`).join('');
  document.querySelectorAll('#categoryList .sidebar-item').forEach(el=>{ el.classList.toggle('active', el.querySelector('span').textContent.trim()===val||(activeFilter.category&&el.querySelector('span').textContent.trim()===allCategories.find(c=>(c.category||c.slug)===activeFilter.category)?.label)); });
  document.querySelectorAll('#tagList .sidebar-item').forEach(el=>{ el.classList.toggle('active', activeFilter.tag&&el.querySelector('span').lastChild.textContent.trim()===activeFilter.tag); });
  document.getElementById('catAll').classList.toggle('active', !activeFilter.category&&!activeFilter.action);
  doSearch();
}
function clearAllFilters(){ activeFilter={category:'',action:'',folder:'',tag:''}; activeFolderId=null; activeFolderPath=''; loadCategories(); loadFolders(); doSearch(); }

// ── LIGHTBOX ──────────────────────────────────────────────────────────────
function openLb(idx){
  lbIdx=idx;
  const item=results[idx];
  document.getElementById('lightbox').classList.add('open');
  document.getElementById('lbTitle').textContent=item.path.split(/[\\/]/).pop();
  const wrap=document.getElementById('lbMediaWrap');
  const ep=encodeURIComponent(item.path);
  if(isVid(item.path)){
    wrap.innerHTML=`<video controls style="max-width:100%;max-height:75vh" src="/img?path=${ep}"></video>`;
  } else {
    wrap.innerHTML=`<img src="/img?path=${ep}" style="max-width:100%;max-height:75vh;object-fit:contain">`;
  }
  document.getElementById('lbReason').textContent=item.reason||'—';
  document.getElementById('lbIssues').textContent=item.issues||'—';
  document.getElementById('lbConf').textContent=item.confidence||'—';
  document.getElementById('lbPath').textContent=item.moved_to||item.path;
  document.getElementById('lbCatSel').value=item.category||'';
  document.getElementById('lbActSel').value=item.action||'review';
  document.getElementById('lbAiCaption').textContent=item.ai_caption||'—';
  document.getElementById('lbAiDesc').textContent=item.ai_description||'—';
  const ocrField=document.getElementById('lbOcrField');
  const ocrEl=document.getElementById('lbOcrText');
  if(item.ocr_text){ ocrEl.textContent=item.ocr_text; ocrField.style.display=''; }
  else { ocrField.style.display='none'; }
  renderLbTags(item.tags||[]);
}
function closeLb(){ document.getElementById('lightbox').classList.remove('open'); document.getElementById('lbMediaWrap').innerHTML=''; }
function lbNav(dir){ const ni=lbIdx+dir; if(ni>=0&&ni<results.length) openLb(ni); }
document.addEventListener('keydown',e=>{ if(!document.getElementById('lightbox').classList.contains('open')) return; if(e.key==='ArrowLeft') lbNav(-1); if(e.key==='ArrowRight') lbNav(1); if(e.key==='Escape') closeLb(); if(e.key==='Delete') lbDelete(); });

function renderLbTags(tags){
  const w=document.getElementById('lbTagsWrap');
  w.innerHTML=tags.map(t=>`<span class="lb-tag-chip" style="background:${t.color}">${t.name}<button onclick="lbRemoveTag(${t.id})">×</button></span>`).join('');
}
async function lbAddTag(){
  const inp=document.getElementById('lbTagInput');
  const names=inp.value.split(',').map(s=>s.trim()).filter(Boolean);
  if(!names.length) return;
  const path=results[lbIdx].path;
  await fetch('/api/file-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,tag_names:names})});
  inp.value='';
  await refreshItem(lbIdx);
}
async function lbRemoveTag(tid){
  const path=results[lbIdx].path;
  await fetch('/api/file-tags',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,tag_id:tid})});
  await refreshItem(lbIdx);
}
async function lbSaveCat(){
  const path=results[lbIdx].path;
  const cat=document.getElementById('lbCatSel').value;
  await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,category:cat})});
  results[lbIdx].category=cat;
  updateCard(lbIdx);
}
async function lbSaveAct(){
  const path=results[lbIdx].path;
  const action=document.getElementById('lbActSel').value;
  await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,action})});
  results[lbIdx].action=action;
}
async function lbDelete(){
  if(!confirm('Delete this file?')) return;
  const path=results[lbIdx].path;
  await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:[path]})});
  results.splice(lbIdx,1); totalItems--;
  const grid=document.getElementById('grid');
  const card=grid.querySelector(`.card[data-idx="${lbIdx}"]`);
  if(card) card.remove();
  if(results.length===0){ closeLb(); } else { if(lbIdx>=results.length) lbIdx=results.length-1; openLb(lbIdx); }
  document.getElementById('statusText').textContent=`${totalItems} files`;
}
function openFile(){ fetch('/api/open?path='+encodeURIComponent(results[lbIdx].path)+'&type=file'); }
function revealFile(){ fetch('/api/open?path='+encodeURIComponent(results[lbIdx].path)+'&type=reveal'); }

async function refreshItem(idx){
  const path=results[idx].path;
  const d=await fetch('/api/search?q=&category=&action=&folder=&tag=&sort=recent&page=1&per_page=1').then(r=>r.json());
  // Re-fetch just this item
  const ep=encodeURIComponent(path);
  const row=await fetch('/api/search?q='+ep+'&per_page=1').then(r=>r.json());
  if(row.results&&row.results.length){ results[idx]={...results[idx],...row.results[0]}; }
  renderLbTags(results[idx].tags||[]);
  updateCard(idx);
}

function updateCard(idx){
  const item=results[idx];
  const card=document.getElementById('grid').querySelector(`.card[data-idx="${idx}"]`);
  if(!card) return;
  let ov=card.querySelector('.card-overlay');
  if(!ov) return;
  const nm=item.path.split(/[\\/]/).pop();
  let tagHtml='';
  if(item.tags&&item.tags.length){ tagHtml='<div class="card-tags">'; item.tags.slice(0,3).forEach(t=>{ tagHtml+=`<span class="card-tag" style="background:${t.color}">${t.name}</span>`; }); tagHtml+='</div>'; }
  ov.innerHTML=`<div class="card-name">${nm}</div><div class="card-cat">${item.category||''}</div>${tagHtml}`;
}

// ── BULK ACTIONS ───────────────────────────────────────────────────────────
async function bulkUpdate(){
  const paths=[...selectedPaths];
  if(!paths.length) return;
  const cat=document.getElementById('bulkCatSel').value;
  const act=document.getElementById('bulkActSel').value;
  if(!cat&&!act) return;
  const body={paths};
  if(cat) body.category=cat;
  if(act) body.action=act;
  await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  clearSelection(); doSearch();
}
document.getElementById('bulkCatSel').addEventListener('change',()=>{ if(document.getElementById('bulkCatSel').value) bulkUpdate(); });
document.getElementById('bulkActSel').addEventListener('change',()=>{ if(document.getElementById('bulkActSel').value) bulkUpdate(); });

function openBulkTagModal(){ if(!selectedPaths.size){ alert('Select files first'); return; } openModal('bulkTagModal'); }
async function bulkTag(){
  const names=document.getElementById('bulkTagInput').value.split(',').map(s=>s.trim()).filter(Boolean);
  if(!names.length) return;
  await fetch('/api/file-tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:[...selectedPaths],tag_names:names})});
  closeModal('bulkTagModal');
  clearSelection(); doSearch(); loadTags();
}
function bulkDelete(){ if(!selectedPaths.size) return; document.getElementById('delCount').textContent=selectedPaths.size; document.getElementById('delConfirmBtn').onclick=confirmBulkDelete; openModal('delModal'); }
async function confirmBulkDelete(){
  const paths=[...selectedPaths];
  await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths})});
  closeModal('delModal'); clearSelection(); doSearch();
}

// ── TAGS SIDEBAR ───────────────────────────────────────────────────────────
async function addGlobalTag(){
  const inp=document.getElementById('newTagInput');
  const name=inp.value.trim();
  if(!name) return;
  await fetch('/api/tags',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  inp.value=''; loadTags();
}
async function deleteTag(id){
  if(!confirm('Delete this tag? It will be removed from all files.')) return;
  await fetch(`/api/tags/${id}`,{method:'DELETE'}); loadTags();
}

// ── CATEGORIES ─────────────────────────────────────────────────────────────
function openAddCatModal(){ if(!activeFolderId){ alert('Select a folder first'); return; } openModal('addCatModal'); }
async function submitNewCat(){
  const label=document.getElementById('newCatLabel').value.trim();
  const slug =document.getElementById('newCatSlug').value.trim()||label.toLowerCase().replace(/\s+/g,'_');
  const ofol =document.getElementById('newCatFolder').value.trim()||'Other';
  const desc =document.getElementById('newCatDesc').value.trim();
  if(!label){ alert('Label required'); return; }
  await fetch(`/api/folders/${activeFolderId}/categories`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,slug,output_folder:ofol,description:desc})});
  closeModal('addCatModal'); loadCategories();
}
document.getElementById('newCatLabel').addEventListener('input',()=>{
  const slug=document.getElementById('newCatSlug');
  if(!slug._edited) slug.value=document.getElementById('newCatLabel').value.toLowerCase().replace(/\s+/g,'_');
});
document.getElementById('newCatSlug').addEventListener('input',()=>{ document.getElementById('newCatSlug')._edited=true; });
async function deleteCat(id){ if(!confirm('Delete this category?')) return; await fetch(`/api/categories/${id}`,{method:'DELETE'}); loadCategories(); }

// ── FOLDERS ────────────────────────────────────────────────────────────────
function openFolderModal(){ openModal('folderModal'); }
async function submitFolder(){
  const path=document.getElementById('folderPathInput').value.trim();
  if(!path){ alert('Path required'); return; }
  await fetch('/api/folders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  closeModal('folderModal'); loadFolders();
}

// ── SETTINGS ───────────────────────────────────────────────────────────────
async function openSettingsModal(){
  const d=await fetch('/api/settings').then(r=>r.json());
  document.getElementById('settingUploadFolder').value=d.upload_folder||'';
  openModal('settingsModal');
}
async function saveSettings(){
  const v=document.getElementById('settingUploadFolder').value.trim();
  const body={}; if(v) body.upload_folder=v;
  await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  closeModal('settingsModal');
}

// ── MODAL HELPERS ──────────────────────────────────────────────────────────
function openModal(id){ document.getElementById(id).classList.add('open'); }
function closeModal(id){ document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('.modal-bg').forEach(el=>{ el.addEventListener('click',e=>{ if(e.target===el) el.classList.remove('open'); }); });

// ── SEARCH INPUT ───────────────────────────────────────────────────────────
document.getElementById('searchInput').addEventListener('keydown',e=>{ if(e.key==='Enter') doSearch(); });
</script>
</body>
</html>
"""



# ─── MOBILE HTML ──────────────────────────────────────────────────────────────

_MOBILE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#0f0f13">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="AI Files">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icons/192.png">
<link rel="apple-touch-icon" sizes="152x152" href="/icons/152.png">
<title>AI Files</title>
<style>
/* ── RESET & BASE ── */
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0f0f13; --surface:#1a1a24; --surface2:#222232; --border:#2d2d3d;
  --accent:#818cf8; --accent-dark:#6366f1; --text:#e2e8f0; --text2:#94a3b8; --text3:#64748b;
  --red:#ef4444; --green:#22c55e; --purple:#a78bfa; --yellow:#fbbf24;
  --safe-top:env(safe-area-inset-top,0px);
  --safe-bot:env(safe-area-inset-bottom,0px);
  --tab-h:0px;
  --radius:14px;
}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font-family:-apple-system,'SF Pro Display',BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif}

/* ── LAYOUT ── */
.app{display:flex;flex-direction:column;height:100%;padding-top:var(--safe-top)}
.screen{flex:1;overflow:hidden;display:none;flex-direction:column}
.screen.active{display:flex}
.scroll{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.scroll::-webkit-scrollbar{display:none}

/* ── HEADER ── */
.hdr{padding:16px 20px 12px;background:var(--bg);border-bottom:1px solid var(--border);flex-shrink:0}
.hdr-title{font-size:1.3rem;font-weight:700;letter-spacing:-.02em}
.hdr-sub{font-size:.78rem;color:var(--text3);margin-top:2px}
.hdr-row{display:flex;align-items:center;justify-content:space-between}

/* ── BOTTOM TAB BAR ── */
.tabbar{display:flex;background:rgba(15,15,19,.92);border-top:1px solid var(--border);
  backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
  padding-bottom:var(--safe-bot);height:calc(var(--tab-h) + var(--safe-bot));flex-shrink:0}
.tab-btn{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:3px;cursor:pointer;color:var(--text3);transition:.15s;padding:6px 0;border:none;background:none;
  font-family:inherit;position:relative}
.tab-btn.active{color:var(--accent)}
.tab-btn .tab-icon{width:26px;height:26px;display:flex;align-items:center;justify-content:center}
.tab-btn .tab-icon svg{width:24px;height:24px;stroke:currentColor;fill:none;transition:.15s}
.tab-btn .tab-label{font-size:.64rem;font-weight:500;letter-spacing:.01em}
.tab-btn .tab-badge{position:absolute;top:5px;right:calc(50% - 14px);
  background:var(--red);color:#fff;font-size:.55rem;font-weight:700;
  min-width:16px;height:16px;border-radius:99px;display:none;align-items:center;justify-content:center;padding:0 4px}
.tab-btn .tab-badge.show{display:flex}

/* ── CARDS ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.card+.card{margin-top:12px}

/* ── CELLS (iOS list style) ── */
.cell{display:flex;align-items:center;gap:12px;padding:14px 18px;
  background:var(--surface);border-bottom:1px solid var(--border);cursor:pointer;transition:.1s}
.cell:last-child{border-bottom:none}
.cell:active{background:var(--surface2)}
.cell-icon{font-size:1.4rem;width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.cell-body{flex:1;min-width:0}
.cell-title{font-size:.93rem;font-weight:500;color:var(--text)}
.cell-sub{font-size:.75rem;color:var(--text3);margin-top:1px}
.cell-right{color:var(--text3);font-size:.8rem;flex-shrink:0}
.cell-chevron::after{content:'›';font-size:1.1rem;color:var(--text3)}

/* ── BUTTONS ── */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;
  border:none;border-radius:12px;cursor:pointer;font-family:inherit;font-weight:600;
  transition:.12s;-webkit-appearance:none}
.btn:active{transform:scale(.97)}
.btn-fill{background:var(--accent);color:#fff;padding:14px 20px;font-size:.95rem;width:100%}
.btn-fill:active{background:var(--accent-dark)}
.btn-fill.red{background:var(--red)}
.btn-fill.green{background:#166534;color:#4ade80}
.btn-secondary{background:var(--surface2);color:var(--text);padding:12px 20px;font-size:.88rem;width:100%;border:1px solid var(--border)}
.btn-sm{padding:8px 14px;font-size:.8rem;border-radius:9px}
.btn-icon{width:40px;height:40px;border-radius:10px;background:var(--surface2);color:var(--text);padding:0;font-size:1.1rem}

/* ── INPUT ── */
.field{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:13px 16px;
  color:var(--text);font-size:.95rem;width:100%;font-family:inherit;-webkit-appearance:none}
.field:focus{outline:none;border-color:var(--accent);background:var(--surface2)}
.field::placeholder{color:var(--text3)}
.search-wrap{position:relative}
.search-wrap .field{padding-left:40px}
.search-icon{position:absolute;left:14px;top:50%;transform:translateY(-50%);font-size:1rem;color:var(--text3);pointer-events:none}

/* ── PILLS / CHIPS ── */
.pills{display:flex;gap:7px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none}
.pills::-webkit-scrollbar{display:none}
.pill{padding:7px 14px;border-radius:99px;font-size:.78rem;font-weight:500;white-space:nowrap;
  cursor:pointer;border:1px solid var(--border);color:var(--text2);background:var(--surface);transition:.1s;flex-shrink:0}
.pill.active{background:var(--accent);border-color:var(--accent);color:#fff}
.pill-sep{width:1px;height:22px;background:var(--border);align-self:center;flex-shrink:0;margin:0 4px}
.pill-tags{border-style:dashed;color:var(--purple,#a78bfa)}

/* ── SYNC BADGES ── */
.sync-badge{display:inline-flex;align-items:center;gap:4px;font-size:.68rem;font-weight:600;
  padding:3px 8px;border-radius:99px}
.s-none    {background:#1e1e2e;color:var(--text3)}
.s-loaded  {background:#052e16;color:#4ade80}
.s-offloaded{background:#2e1065;color:var(--purple)}
.s-pc_only {background:#1c1917;color:#78716c}

/* ── GRID ── */
/* Photo grid */
.photo-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px}
.photo-cell{position:relative;overflow:hidden;cursor:pointer;-webkit-tap-highlight-color:transparent;background:var(--surface2)}
.photo-cell img{width:100%;aspect-ratio:1;object-fit:cover;display:block;pointer-events:none}
.photo-cell .ph-vid{width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;background:#111;position:relative}
.photo-cell .ph-dot{position:absolute;top:4px;right:4px;width:8px;height:8px;border-radius:50%;box-shadow:0 0 0 1.5px rgba(0,0,0,.7)}
.photo-cell .ph-vid-ic{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.25)}
.photo-cell .ph-dur{position:absolute;bottom:3px;right:5px;color:#fff;font-size:.6rem;font-weight:700;text-shadow:0 1px 3px rgba(0,0,0,.9)}
.photo-cell .ph-file-thumb{width:100%;aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:center;background:var(--surface2);padding:8px}
.photo-cell:active{opacity:.85}
/* List-mode grid btn kept for list rows */
.grid-btn{flex:1;padding:7px 4px;border-radius:8px;font-size:.65rem;font-weight:600;
  text-align:center;border:none;cursor:pointer;font-family:inherit;text-decoration:none;
  display:flex;align-items:center;justify-content:center;gap:3px;transition:.1s}
.grid-btn:active{transform:scale(.95)}
.gb-dl{background:var(--accent);color:#fff}
.gb-off{background:#2e1065;color:var(--purple)}
.gb-ok{background:#052e16;color:#4ade80}
.gb-pc{background:var(--surface2);color:var(--text3);border:1px solid var(--border)}
/* Media Viewer */
#mediaViewer{position:fixed;inset:0;z-index:9999;background:#000;flex-direction:column;overflow:hidden}
#viewerSlide{flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none;will-change:transform}
#viewerSlide img{max-width:100%;max-height:100%;object-fit:contain;user-select:none;-webkit-user-drag:none;transform-origin:center center;will-change:transform;display:block}
#viewerSlide video{max-width:100%;max-height:100%;object-fit:contain;display:block}
.vbar{position:absolute;left:0;right:0;z-index:10;display:flex;align-items:center;pointer-events:none}
.vbar>*{pointer-events:auto}
#vTop{top:0;padding:calc(env(safe-area-inset-top,0px) + 8px) 12px 24px;background:linear-gradient(to bottom,rgba(0,0,0,.85),transparent);justify-content:space-between;gap:8px}
#vBottom{bottom:0;padding:16px 20px calc(env(safe-area-inset-bottom,0px) + 8px);background:linear-gradient(to top,rgba(0,0,0,.85),transparent);justify-content:center;gap:8px}
.vbtn{background:rgba(0,0,0,.45);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.18);border-radius:50%;width:40px;height:40px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fff;flex-shrink:0;font-family:inherit;-webkit-tap-highlight-color:transparent}
.vbtn:active{transform:scale(.93)}
.vbtn-wide{border-radius:14px;width:auto;padding:0 18px;font-size:.85rem;font-weight:600;gap:7px;height:42px}
#viewerInfo{display:none;position:absolute;bottom:0;left:0;right:0;z-index:20;background:var(--surface);border-radius:20px 20px 0 0;max-height:72vh;overflow-y:auto;box-shadow:0 -4px 40px rgba(0,0,0,.6)}

/* ── UPLOAD DROP ZONE ── */
.drop-zone{border:2px dashed var(--border);border-radius:var(--radius);padding:36px 20px;
  text-align:center;cursor:pointer;transition:.2s;background:var(--surface)}
.drop-zone.drag{border-color:var(--accent);background:#1e1e3a}
.drop-zone-icon{font-size:3rem;margin-bottom:12px}
.drop-zone-title{font-size:1rem;font-weight:600;color:var(--text);margin-bottom:4px}
.drop-zone-sub{font-size:.8rem;color:var(--text3)}

/* ── PROGRESS ── */
.progress-track{height:5px;background:var(--border);border-radius:99px;overflow:hidden;margin:12px 0 6px}
.progress-fill{height:100%;background:var(--accent);border-radius:99px;transition:width .15s;width:0%}
.progress-label{font-size:.75rem;color:var(--text3);text-align:center}

/* ── TOAST ── */
.toast{position:fixed;bottom:calc(var(--tab-h) + var(--safe-bot) + 12px);left:50%;transform:translateX(-50%) translateY(20px);
  background:#1e1e3a;border:1px solid var(--accent);border-radius:12px;padding:12px 20px;
  font-size:.85rem;font-weight:500;white-space:nowrap;opacity:0;transition:.25s;z-index:99;pointer-events:none}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

/* ── INSTALL BANNER ── */
.install-banner{margin:0 16px 0;background:linear-gradient(135deg,#2d1e6b,#1e1e3a);
  border:1px solid var(--accent);border-radius:var(--radius);padding:14px 16px;
  display:flex;align-items:center;gap:12px;cursor:pointer}
.install-banner-text{flex:1}
.install-banner-title{font-size:.88rem;font-weight:600}
.install-banner-sub{font-size:.72rem;color:var(--text3);margin-top:2px}

/* ── SPACING HELPERS ── */
.p16{padding:16px}.px16{padding-left:16px;padding-right:16px}.pt12{padding-top:12px}
.mb12{margin-bottom:12px}.mt12{margin-top:12px}.mt16{margin-top:16px}
.section-title{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.09em;color:var(--text3);padding:16px 20px 8px}
.empty{text-align:center;padding:48px 24px;color:var(--text3);font-size:.88rem;line-height:1.6}
/* Folder tree view */
.ftree-node{display:flex;flex-direction:column}
.ftree-row{display:flex;align-items:center;gap:8px;padding:11px 0;border-bottom:1px solid var(--border);cursor:pointer;-webkit-tap-highlight-color:transparent}
.ftree-row:active{background:var(--surface2)}
.ftree-chev{width:22px;height:22px;display:flex;align-items:center;justify-content:center;flex-shrink:0;color:var(--text3)}
.ftree-chev-svg{transition:transform .2s}
.ftree-chev-ghost{width:22px;height:22px;flex-shrink:0}
.ftree-ico{flex-shrink:0}
.ftree-name{flex:1;font-size:.88rem;color:var(--text1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ftree-cnt{font-size:.7rem;color:var(--text3);background:var(--surface2);padding:2px 7px;border-radius:99px;flex-shrink:0}
.ftree-kids{border-left:1.5px solid var(--border);margin-left:22px}

/* ── ANIMATIONS ── */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.uploading{animation:pulse 1.2s ease-in-out infinite}

@keyframes ptr-spin{to{transform:rotate(360deg)}}
@keyframes tab-tap{0%{transform:scale(1)}35%{transform:scale(.78)}100%{transform:scale(1)}}
@keyframes icon-bounce{0%,100%{transform:translateY(0)}35%{transform:translateY(-5px)}65%{transform:translateY(2px)}}
.tab-btn:active .tab-icon{animation:tab-tap .22s cubic-bezier(.34,1.56,.64,1)}
.tab-btn.active .tab-icon{animation:icon-bounce .32s cubic-bezier(.34,1.56,.64,1)}

@keyframes card-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.grid .card{animation:card-in .18s ease both}

@keyframes slide-in{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:none}}
.screen.active{animation:slide-in .22s cubic-bezier(.22,.68,0,1.2) both}

@keyframes ripple{to{transform:scale(4);opacity:0}}
.ripple-el{position:absolute;border-radius:50%;background:rgba(255,255,255,.22);pointer-events:none;animation:ripple .5s ease-out forwards}

@keyframes check-pop{0%{transform:scale(0) rotate(-25deg)}70%{transform:scale(1.18) rotate(5deg)}100%{transform:scale(1) rotate(0)}}
.check-anim{animation:check-pop .38s cubic-bezier(.34,1.56,.64,1) both}

@keyframes badge-pop{0%{transform:scale(0)}70%{transform:scale(1.35)}100%{transform:scale(1)}}
.tab-badge.show{animation:badge-pop .28s cubic-bezier(.34,1.56,.64,1)}

@keyframes shimmer{from{background-position:-200% 0}to{background-position:200% 0}}
.progress-shimmer{background:linear-gradient(90deg,var(--accent),#a78bfa,var(--accent));background-size:200%;animation:shimmer 1.4s infinite linear}

@keyframes spin-in{from{opacity:0;transform:rotate(-90deg) scale(.6)}to{opacity:1;transform:rotate(0) scale(1)}}
.drop-icon-anim{animation:spin-in .4s cubic-bezier(.34,1.56,.64,1) both}

@keyframes row-in{from{opacity:0;transform:translateX(-12px)}to{opacity:1;transform:none}}
.cell{animation:row-in .16s ease both}
</style>
</head>
<body>
<div class="app">

  <!-- ══ UPLOAD SCREEN ══ -->
  <div class="screen" id="s-upload" style="display:none!important">
    <!-- Upload functionality moved to FAB sheet -->
  </div>

  <!-- ══ LIBRARY SCREEN ══ -->
  <div class="screen active" id="s-library">
    <div class="hdr">
      <!-- Folder switcher row -->
      <div style="display:flex;align-items:center;gap:8px;padding:0 0 4px">
        <button id="libFolderBtn" onclick="openFolderPicker()" style="flex:1;min-width:0;background:var(--surface2);border:1px solid var(--border);border-radius:12px;padding:8px 12px;display:flex;align-items:center;gap:8px;cursor:pointer;text-align:left;transition:background .15s">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
          <span id="libFolderName" style="flex:1;font-weight:700;font-size:.92rem;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">All Folders</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--text3)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <button id="libBackBtn" onclick="libBack()" style="display:none;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:8px 10px;cursor:pointer;color:var(--accent);align-items:center;gap:4px;font-size:.82rem;font-weight:600;flex-shrink:0">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          Back
        </button>
        <button class="btn btn-icon" id="libViewToggle" onclick="toggleLibView()" title="Switch view" style="flex-shrink:0">
          <svg id="libViewIcon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
        </button>
        <button class="btn btn-icon" onclick="openProfile()" title="Your profile" id="profileBtn" style="font-size:.8rem;font-weight:700;background:var(--accent);color:#fff;flex-shrink:0">?</button>
      </div>
      <div class="search-wrap" style="margin-top:8px">
        <span class="search-icon"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
        <input class="field" id="libQ" type="search" placeholder='Try "dog at beach" or "birthday 2024"' oninput="debounce(()=>loadLib(true),400)()">
      </div>
      <!-- Popular tags strip -->
      <div id="libTagStrip" style="display:flex;gap:6px;overflow-x:auto;padding:8px 0 2px;scrollbar-width:none;-webkit-overflow-scrolling:touch"></div>
    </div>
    <div style="padding:8px 16px 0;flex-shrink:0">
      <div class="pills" id="libGroupPills">
        <span class="pill active" onclick="setLibGroup('date',this)">By Date</span>
        <span class="pill" onclick="setLibGroup('type',this)">By Type</span>
        <span class="pill-sep"></span>
        <span class="pill pill-tags" onclick="setLibGroup('tags',this)">🏷 Tags</span>
      </div>
    </div>
    <!-- Breadcrumb shown when viewing a sub-folder's files -->
    <div id="libBreadcrumb" style="display:none;padding:6px 16px 0;overflow-x:auto;white-space:nowrap;scrollbar-width:none;-webkit-overflow-scrolling:touch;flex-shrink:0"></div>
    <div id="ptrIndicator" style="display:flex;align-items:center;justify-content:center;height:0;overflow:hidden;transition:height .2s;flex-shrink:0">
      <div id="ptrSpinner" style="width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;transition:transform .3s"></div>
    </div>
    <div class="scroll p16 pt12" id="libScroll">
      <div id="libFlatGrid" class="photo-grid"></div>
      <div id="libFlatList" class="card" style="display:none"></div>
      <div id="libTagsView" style="display:none"></div>
      <div id="libDateView" style="display:none"></div>
      <div id="libTypeView" style="display:none"></div>
      <div id="libFolderView" style="display:none"></div>
      <div id="libMore" style="display:none;margin-top:12px">
        <button class="btn btn-secondary" onclick="loadMoreLib()">Load more</button>
      </div>
    </div>
  </div>

  <!-- ══ SYNC SCREEN ══ -->
  <div class="screen" id="s-sync">
    <div class="hdr">
      <div class="hdr-title">Sync Status</div>
      <div class="hdr-sub">Track what's on your phone</div>
    </div>
    <div class="scroll p16">

      <!-- Status summary cards -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px" id="syncStats">
        <div class="card p16" style="text-align:center">
          <div style="font-size:1.6rem;font-weight:700;color:#4ade80" id="statLoaded">–</div>
          <div style="font-size:.72rem;color:var(--text3);margin-top:2px">On Device</div>
        </div>
        <div class="card p16" style="text-align:center">
          <div style="font-size:1.6rem;font-weight:700;color:var(--purple)" id="statOffloaded">–</div>
          <div style="font-size:.72rem;color:var(--text3);margin-top:2px">Offloaded</div>
        </div>
        <div class="card p16" style="text-align:center">
          <div style="font-size:1.6rem;font-weight:700;color:#78716c" id="statPcOnly">–</div>
          <div style="font-size:.72rem;color:var(--text3);margin-top:2px">PC Only</div>
        </div>
        <div class="card p16" style="text-align:center">
          <div style="font-size:1.6rem;font-weight:700;color:var(--text3)" id="statNone">–</div>
          <div style="font-size:.72rem;color:var(--text3);margin-top:2px">Not Synced</div>
        </div>
      </div>

      <!-- Legend -->
      <div class="card mb12">
        <div class="cell" style="cursor:default">
          <span style="width:10px;height:10px;border-radius:50%;background:#4ade80;display:inline-block;flex-shrink:0"></span>
          <div class="cell-body"><div class="cell-title" style="font-size:.85rem">On Device</div><div class="cell-sub">Downloaded to phone — tap Offloaded after you delete it</div></div>
        </div>
        <div class="cell" style="cursor:default">
          <span style="width:10px;height:10px;border-radius:50%;background:var(--purple);display:inline-block;flex-shrink:0"></span>
          <div class="cell-body"><div class="cell-title" style="font-size:.85rem">Offloaded</div><div class="cell-sub">Deleted from phone — still safely on your PC</div></div>
        </div>
        <div class="cell" style="cursor:default">
          <span style="width:10px;height:10px;border-radius:50%;background:#78716c;display:inline-block;flex-shrink:0"></span>
          <div class="cell-body"><div class="cell-title" style="font-size:.85rem">PC Only</div><div class="cell-sub">Intentionally kept off your phone</div></div>
        </div>
      </div>

      <p style="font-size:.72rem;color:var(--text3);margin-bottom:14px;line-height:1.5">
        ⚠️ Status updates when you tap Save or mark buttons. The browser cannot detect phone deletions automatically.
      </p>

      <!-- Tracked files list -->
      <div class="section-title" style="padding-left:0">Files with sync status</div>
      <div class="card" id="syncList"><div class="empty">No tracked files yet.<br>Save files to your phone to start.</div></div>
    </div>
  </div>

  <!-- ══ MEDIA VIEWER ══ -->
  <div id="mediaViewer" style="display:none">
    <!-- Top bar -->
    <div class="vbar" id="vTop">
      <button class="vbtn" onclick="closeViewer()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
      <div id="viewerCounter" style="flex:1;text-align:center;color:rgba(255,255,255,.9);font-size:.82rem;font-weight:600">1 / 1</div>
      <button class="vbtn" onclick="toggleViewerInfo()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      </button>
    </div>
    <!-- Main slide -->
    <div id="viewerSlide" ontouchstart="_vtStart(event)" ontouchmove="_vtMove(event)" ontouchend="_vtEnd(event)"></div>
    <!-- AI caption overlay -->
    <div id="viewerCaption" style="position:absolute;bottom:60px;left:0;right:0;padding:10px 16px 10px;background:linear-gradient(transparent,rgba(0,0,0,.72));pointer-events:none;display:none">
      <div id="viewerCaptionText" style="color:rgba(255,255,255,.88);font-size:.8rem;font-style:italic;line-height:1.45;text-shadow:0 1px 3px rgba(0,0,0,.6)"></div>
    </div>
    <!-- Bottom actions -->
    <div class="vbar" id="vBottom">
      <button class="vbtn vbtn-wide" onclick="viewerSave()">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Save to Phone
      </button>
      <button class="vbtn" onclick="toggleViewerInfo()" title="Info">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
      </button>
    </div>
    <!-- Info panel (slide up) -->
    <div id="viewerInfo" onclick="if(event.target===this)hideViewerInfo()">
      <div style="padding:12px 20px calc(env(safe-area-inset-bottom,0px) + 12px)">
        <div style="width:36px;height:4px;background:var(--border);border-radius:99px;margin:0 auto 16px" onclick="hideViewerInfo()"></div>
        <div id="viewerInfoBody"></div>
      </div>
    </div>
  </div>

    <!-- Upload FAB -->
    <button id="uploadFab" onclick="openUploadSheet()" style="position:fixed;bottom:calc(20px + var(--safe-bot));right:20px;z-index:280;width:56px;height:56px;border-radius:50%;background:var(--accent);border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 20px rgba(99,102,241,.5);transition:transform .15s" ontouchstart="this.style.transform='scale(.92)'" ontouchend="this.style.transform=''">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4m0 0-4 4m4-4 4 4M4 20h16"/></svg>
    </button>
</div>

<!-- UPLOAD SHEET -->
<div id="uploadSheet" style="display:none;position:fixed;inset:0;z-index:350;background:rgba(0,0,0,.6);backdrop-filter:blur(6px)" onclick="if(event.target===this)closeUploadSheet()">
  <div style="position:absolute;bottom:0;left:0;right:0;background:var(--surface);border-radius:20px 20px 0 0;padding:16px 0 calc(20px + var(--safe-bot));max-height:90vh;display:flex;flex-direction:column">
    <div style="width:36px;height:4px;background:var(--border);border-radius:99px;margin:0 auto 12px"></div>
    <div style="display:flex;align-items:center;justify-content:space-between;padding:0 20px 14px;flex-shrink:0">
      <div>
        <div style="font-size:1rem;font-weight:700">Send to PC</div>
        <div style="font-size:.75rem;color:var(--text3)" id="uploadSheetSub">Saved directly to your hard drive</div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn btn-icon" onclick="showNetInfo()" title="Network info"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1" fill="currentColor"/></svg></button>
        <button class="btn btn-icon" onclick="closeUploadSheet()"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
    </div>
    <div class="scroll p16" style="padding-top:0">
      <!-- Install banner -->
      <div class="install-banner mb12" id="installBanner" style="display:none" onclick="triggerInstall()">
        <span style="font-size:1.6rem">📲</span>
        <div class="install-banner-text">
          <div class="install-banner-title">Add to Home Screen</div>
          <div class="install-banner-sub">Use like a native app — works offline</div>
        </div>
        <span style="color:var(--accent);font-size:1.1rem">›</span>
      </div>
      <!-- Drop zone -->
      <div class="drop-zone" id="dropZone" onclick="document.getElementById('fileInput').click()"
           ondragover="onDragOver(event)" ondragleave="onDragLeave()" ondrop="onDrop(event)">
        <div class="drop-zone-icon"><svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg></div>
        <div class="drop-zone-title">Tap to choose files</div>
        <div class="drop-zone-sub">Photos, videos, any file • Saved directly to your PC</div>
      </div>
      <input type="file" id="fileInput" accept="image/*,video/*" multiple onchange="checkAndUpload(this.files)">
      <!-- Camera / Record shortcuts -->
      <div style="display:flex;gap:10px;margin-top:12px">
        <button class="btn btn-secondary btn-sm" style="gap:6px" onclick="document.getElementById('camIn').click()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg> Camera
        </button>
        <input type="file" id="camIn" accept="image/*" capture="environment" style="display:none" onchange="checkAndUpload(this.files)">
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('vidIn').click()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg> Video
        </button>
        <input type="file" id="vidIn" accept="video/*" capture="environment" style="display:none" onchange="checkAndUpload(this.files)">
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('anyIn').click()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg> File
        </button>
        <input type="file" id="anyIn" multiple style="display:none" onchange="checkAndUpload(this.files)">
      </div>
      <!-- Progress -->
      <div id="progressWrap" style="display:none;margin-top:16px" class="card p16">
        <div style="font-size:.88rem;font-weight:600;margin-bottom:8px" id="progressTitle">Uploading…</div>
        <div class="progress-track"><div class="progress-fill" id="progressFill"></div></div>
        <div class="progress-label" id="progressLabel">0%</div>
      </div>
      <!-- Recent uploads -->
      <div id="recentSection" style="display:none">
        <div class="section-title" style="padding-left:0">Recently sent to PC</div>
        <div class="grid" id="recentGrid"></div>
      </div>
    </div>
  </div>
</div>

<!-- AI QUEUE TOAST -->
<div id="aiToast" style="display:none;position:fixed;bottom:calc(70px + var(--safe-bot,0px) + 8px);left:12px;right:12px;z-index:290;background:linear-gradient(135deg,#1e1e3a,#16213e);border:1px solid var(--accent);border-radius:14px;padding:10px 14px;display:none;align-items:center;gap:10px;box-shadow:0 4px 24px rgba(0,0,0,.5)">
  <div style="width:24px;height:24px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;flex-shrink:0"></div>
  <div style="flex:1;min-width:0">
    <div style="font-size:.78rem;font-weight:600;color:var(--accent)">AI Tagging</div>
    <div id="aiToastMsg" style="font-size:.72rem;color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Processing uploads…</div>
  </div>
  <div id="aiToastCount" style="font-size:.7rem;font-weight:700;color:var(--text2);background:var(--surface2);padding:2px 8px;border-radius:99px;flex-shrink:0"></div>
</div>

<!-- FOLDER PICKER SHEET -->
<div id="folderPickerSheet" style="display:none;position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.6);backdrop-filter:blur(6px)" onclick="if(event.target===this)closeFolderPicker()">
  <div style="position:absolute;bottom:0;left:0;right:0;background:var(--surface);border-radius:20px 20px 0 0;padding:16px 0 calc(16px + var(--safe-bot));max-height:80vh;display:flex;flex-direction:column">
    <div style="width:36px;height:4px;background:var(--border);border-radius:99px;margin:0 auto 14px"></div>
    <div style="font-size:1rem;font-weight:700;padding:0 20px 12px;flex-shrink:0">Switch Folder</div>
    <div id="folderPickerList" style="overflow-y:auto;padding:0 16px;flex:1">
      <div class="empty">Loading…</div>
    </div>
  </div>
</div>

<!-- DUPLICATE SHEET -->
<div id="dupOverlay" style="display:none;position:fixed;inset:0;z-index:300;background:rgba(0,0,0,.6);backdrop-filter:blur(6px)" onclick="if(event.target===this)closeDupSheet()">
  <div style="position:absolute;bottom:0;left:0;right:0;background:var(--surface);border-radius:20px 20px 0 0;padding:20px 20px calc(20px + var(--safe-bot));max-height:75vh;overflow-y:auto">
    <div style="width:36px;height:4px;background:var(--border);border-radius:99px;margin:0 auto 16px"></div>
    <div style="font-size:1.05rem;font-weight:700;margin-bottom:4px">⚠️ Duplicates found</div>
    <div style="font-size:.8rem;color:var(--text3);margin-bottom:14px">These files already exist on your PC.</div>
    <div id="dupList" style="margin-bottom:16px"></div>
    <button class="btn btn-fill" id="dupSkipBtn" onclick="dupAction('skip')" style="margin-bottom:10px">Skip duplicates — upload new only</button>
    <button class="btn btn-secondary" onclick="dupAction('all')" style="margin-bottom:10px">Upload all anyway</button>
    <button class="btn btn-secondary" style="color:var(--red)" onclick="closeDupSheet()">Cancel</button>
  </div>
</div>

<!-- TOAST -->
<div class="toast" id="toast"></div>

<!-- PROFILE SHEET -->
<div id="profileOverlay" style="display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.55);backdrop-filter:blur(4px)" onclick="closeProfile(event)">
  <div style="position:absolute;bottom:0;left:0;right:0;background:var(--surface);border-radius:20px 20px 0 0;padding:20px 20px calc(20px + var(--safe-bot))">
    <div style="width:36px;height:4px;background:var(--border);border-radius:99px;margin:0 auto 18px"></div>
    <div style="font-size:1.05rem;font-weight:700;margin-bottom:4px">Your Profile</div>
    <div style="font-size:.78rem;color:var(--text3);margin-bottom:18px">Files you upload will be saved to a folder with your name, organised by date.</div>

    <label style="font-size:.75rem;color:var(--text3);font-weight:600;display:block;margin-bottom:6px;text-transform:uppercase;letter-spacing:.08em">Your name / device label</label>
    <input class="field" id="profileName" type="text" placeholder="e.g. My iPhone" maxlength="64" style="margin-bottom:8px">
    <div style="font-size:.72rem;color:var(--text3);margin-bottom:20px">This label appears as a sub-folder: <em>Phone Uploads / Your Name / 2026 / May 2026</em></div>

    <button class="btn btn-fill" onclick="saveProfile()">Save</button>
    <button class="btn btn-secondary mt12" onclick="document.getElementById('profileOverlay').style.display='none'">Cancel</button>
    <div style="text-align:center;margin-top:20px;font-size:.62rem;color:var(--text3);letter-spacing:.04em;user-select:none;opacity:.6">AI File Classifier v__APP_VER__</div>
  </div>
</div>

<script>
// ── CONSTANTS ──────────────────────────────────────────────────────────────
const VID=new Set(['mp4','mov','avi','mkv','webm','m4v','wmv','flv','3gp','ts','mts','m2ts']);
const ext=p=>{ const s=p.split('.'); return s.length>1?s.pop().toLowerCase():''; };
const isVid=p=>VID.has(ext(p));

// ── DEVICE / PROFILE ───────────────────────────────────────────────────────
function getDeviceName(){ return localStorage.getItem('device_name')||''; }
function setDeviceName(n){ localStorage.setItem('device_name',n); updateProfileBtn(n); }
function updateProfileBtn(n){
  const btn=document.getElementById('profileBtn');
  if(!btn) return;
  btn.textContent=n?n.charAt(0).toUpperCase():'?';
  btn.title=n?('Profile: '+n):'Set your name';
}
function openProfile(){
  document.getElementById('profileName').value=getDeviceName();
  document.getElementById('profileOverlay').style.display='block';
}
function closeProfile(e){
  if(e.target===document.getElementById('profileOverlay'))
    document.getElementById('profileOverlay').style.display='none';
}
function saveProfile(){
  const n=document.getElementById('profileName').value.trim();
  if(!n){ showToast('Please enter a name'); return; }
  setDeviceName(n);
  document.getElementById('profileOverlay').style.display='none';
  showToast('✅ Saved as "'+n+'"');
}
// Init on load
(function(){ const n=getDeviceName(); updateProfileBtn(n); if(!n) setTimeout(openProfile,1200); })();

// ── SERVICE WORKER ─────────────────────────────────────────────────────────
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'})
    .then(reg=>{ reg.update(); })
    .catch(()=>{});
}

// ── PULL TO REFRESH ─────────────────────────────────────────────────────────
(function initPullToRefresh(){
  const scroller = document.getElementById('libScroll');
  const indicator = document.getElementById('ptrIndicator');
  const spinner = document.getElementById('ptrSpinner');
  if(!scroller||!indicator) return;

  let startY=0, pulling=false, triggered=false;
  const THRESHOLD=64; // px of pull needed to trigger refresh

  scroller.addEventListener('touchstart', e=>{
    if(scroller.scrollTop > 0) return;
    startY = e.touches[0].clientY;
    pulling = true;
    triggered = false;
  }, {passive:true});

  scroller.addEventListener('touchmove', e=>{
    if(!pulling) return;
    if(scroller.scrollTop > 0){ pulling=false; return; }
    const dy = Math.min(e.touches[0].clientY - startY, THRESHOLD*1.5);
    if(dy <= 0){ indicator.style.height='0'; return; }
    const h = Math.min(dy*0.5, 44);
    indicator.style.height = h+'px';
    const progress = Math.min(dy/THRESHOLD, 1);
    spinner.style.transform = `rotate(${progress*270}deg)`;
    spinner.style.borderTopColor = progress>=1 ? 'var(--accent)' : 'var(--text3)';
    triggered = progress >= 1;
  }, {passive:true});

  scroller.addEventListener('touchend', ()=>{
    if(!pulling) return;
    pulling = false;
    if(triggered){
      // Show spinning animation briefly then refresh
      spinner.style.animation='ptr-spin .6s linear infinite';
      indicator.style.height='44px';
      setTimeout(()=>{
        loadLib(true);
        setTimeout(()=>{
          indicator.style.height='0';
          spinner.style.animation='';
          spinner.style.transform='rotate(0deg)';
        }, 600);
      }, 200);
    } else {
      indicator.style.height='0';
      spinner.style.transform='rotate(0deg)';
    }
  }, {passive:true});
})();

// ── WIFI PREFERENCE ────────────────────────────────────────────────────────
// If accessing via Tailscale but local WiFi is also available, prefer local IP
(async function preferLocalWifi(){
  try{
    const d=await fetch('/api/network').then(r=>r.json());
    const localUrl=d.local_url; // e.g. http://192.168.x.x:5050
    const currentHost=location.host;
    if(!localUrl) return;
    const localHost=new URL(localUrl).host;
    if(currentHost===localHost) return; // already on local
    // Try reaching local IP (1s timeout)
    const ok=await Promise.race([
      fetch(localUrl+'/api/health',{mode:'no-cors'}).then(()=>true),
      new Promise(r=>setTimeout(()=>r(false),1000))
    ]);
    if(ok){
      showToast('📶 Faster local WiFi detected — switching…',3000);
      setTimeout(()=>{ location.href=localUrl+'/mobile'; },1500);
    }
  }catch(e){}
})();

// ── PWA INSTALL ────────────────────────────────────────────────────────────
let _installPrompt=null;
window.addEventListener('beforeinstallprompt',e=>{
  e.preventDefault(); _installPrompt=e;
  document.getElementById('installBanner').style.display='flex';
});
function triggerInstall(){
  if(_installPrompt){ _installPrompt.prompt(); _installPrompt=null; document.getElementById('installBanner').style.display='none'; }
  else { showToast('Open browser menu → "Add to Home Screen"'); }
}
// Hide banner if already standalone
if(window.navigator.standalone||window.matchMedia('(display-mode:standalone)').matches){
  document.getElementById('installBanner').style.display='none';
}

// ── RIPPLE ─────────────────────────────────────────────────────────────────
function addRipple(el, e){
  const r=document.createElement('span');
  r.className='ripple-el';
  const rect=el.getBoundingClientRect();
  const size=Math.max(rect.width,rect.height);
  const x=(e.clientX-rect.left)-size/2;
  const y=(e.clientY-rect.top)-size/2;
  Object.assign(r.style,{width:size+'px',height:size+'px',left:x+'px',top:y+'px'});
  el.style.overflow='hidden'; el.style.position='relative';
  el.appendChild(r);
  r.addEventListener('animationend',()=>r.remove());
}
document.querySelectorAll('.btn-fill,.btn-secondary,.tab-btn').forEach(b=>{
  b.addEventListener('click',e=>addRipple(b,e));
});

// ── TABS (simplified — library is the only screen) ─────────────────────────
let curTab='library';
function goTab(name){
  document.querySelectorAll('.screen').forEach(s=>s.classList.remove('active'));
  const scr=document.getElementById('s-'+name);
  if(scr) scr.classList.add('active');
  curTab=name;
  if(name==='library'&&!libLoaded){ _initLibraryFolder().then(()=>loadLib(true)); }
  if(name==='library'){ startAIQueuePolling(); loadPopularTags(); }
}

// ── UPLOAD SHEET ────────────────────────────────────────────────────────────
function openUploadSheet(){
  document.getElementById('uploadSheet').style.display='flex';
  document.getElementById('uploadSheet').style.alignItems='flex-end';
}
function closeUploadSheet(){
  document.getElementById('uploadSheet').style.display='none';
}

// ── TOAST ──────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg,ms=2500){
  const t=document.getElementById('toast');
  t.textContent=msg; t.classList.add('show');
  clearTimeout(_toastTimer); _toastTimer=setTimeout(()=>t.classList.remove('show'),ms);
}

// ── NETWORK INFO ───────────────────────────────────────────────────────────
let netData={};
fetch('/api/network').then(r=>r.json()).then(d=>{
  netData=d;
  const el=document.getElementById('uploadSheetSub');
  if(el) el.textContent='Connected to '+d.local_ip;
}).catch(()=>{
  const el=document.getElementById('uploadSheetSub');
  if(el) el.textContent='Check your WiFi connection';
});



function showNetInfo(){
  const d=netData;
  if(!d.local_ip){ showToast('Not connected'); return; }
  let msg=`PC: ${d.local_ip}:${d.port}`;
  if(d.tailscale_ip) msg+=` | Tailscale: ${d.tailscale_ip}`;
  showToast(msg, 4000);
}

// ── UPLOAD ─────────────────────────────────────────────────────────────────
function onDragOver(e){ e.preventDefault(); document.getElementById('dropZone').classList.add('drag'); }
function onDragLeave(){ document.getElementById('dropZone').classList.remove('drag'); }
function onDrop(e){ e.preventDefault(); onDragLeave(); checkAndUpload(e.dataTransfer.files); }

async function uploadFiles(files){
  if(!files||!files.length) return;
  const deviceName=getDeviceName();
  if(!deviceName){ openProfile(); showToast('Please set your name first'); return; }

  const wrap=document.getElementById('progressWrap');
  const fill=document.getElementById('progressFill');
  const lbl=document.getElementById('progressLabel');
  const title=document.getElementById('progressTitle');
  wrap.style.display='block'; fill.style.width='0%'; fill.className='progress-shimmer'; lbl.textContent='0%';
  title.textContent='Uploading '+files.length+' file'+(files.length>1?'s':'')+'…';
  title.className='uploading';

  const fd=new FormData();
  for(const f of files) fd.append('file',f);
  fd.append('device_name', deviceName);

  const xhr=new XMLHttpRequest();
  xhr.open('POST','/upload');
  xhr.upload.onprogress=e=>{
    if(e.lengthComputable){ const p=Math.round(e.loaded/e.total*100); fill.style.width=p+'%'; lbl.textContent=p+'%'; }
  };
  xhr.onload=()=>{
    wrap.style.display='none'; title.className=''; fill.className='';
    try{
      const d=JSON.parse(xhr.responseText);
      if(d.ok){
        const months=[...new Set((d.uploaded||[]).map(u=>u.month).filter(Boolean))];
        const monthStr=months.length?(' → '+months.join(', ')):'';
        // Flash drop zone with success
        const dz=document.getElementById('dropZone');
        dz.style.borderColor='#4ade80'; dz.style.background='#0d2e1a';
        const dzIcon=dz.querySelector('.drop-zone-icon');
        if(dzIcon){ dzIcon.style.animation='none'; dzIcon.offsetWidth;
          dzIcon.innerHTML=`<svg class="check-anim" width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="#4ade80" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`; }
        setTimeout(()=>{
          dz.style.borderColor=''; dz.style.background='';
          if(dzIcon) dzIcon.innerHTML=`<svg width="52" height="52" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`;
        }, 2000);
        showToast('✅ '+d.count+' file'+(d.count>1?'s':'')+' saved'+monthStr, 3500);
        loadRecentUploads();
        setTimeout(()=>{ closeUploadSheet(); loadLib(true); }, 2200);
      } else showToast('❌ '+(d.error||'Upload failed'));
    }catch{ showToast('❌ Upload failed'); }
  };
  xhr.onerror=()=>{ wrap.style.display='none'; showToast('❌ Network error'); };
  xhr.send(fd);
}

async function loadRecentUploads(){
  const d=await fetch('/api/search?q=&sort=recent&per_page=6&uploaded=1').then(r=>r.json());
  if(!d.results||!d.results.length){ document.getElementById('recentSection').style.display='none'; return; }
  document.getElementById('recentSection').style.display='block';
  document.getElementById('recentGrid').innerHTML=d.results.map((f,i)=>gridCardHTML(f,i)).join('');
}

// ── LIBRARY ────────────────────────────────────────────────────────────────
let libGroup='date', libView='grid', libPg=1, libLoaded=false;
let libActiveFolderPath='', libActiveFolderName='', _libItems=[];
let libActiveFolder=null; // {id, path, name} or null = all folders
let _libGroupedLabels=[], _libGroupedData={}, _collapsedSections=new Set();

async function _initLibraryFolder(){
  libLoaded=true;
  try{
    const folders=await fetch('/api/folders').then(r=>r.json());
    if(folders.length===1){
      const f=folders[0];
      libActiveFolder={id:f.id,path:f.path,name:f.display_name||(f.path.split(/[\\/]/).pop())};
      document.getElementById('libFolderName').textContent=libActiveFolder.name;
    }
  }catch(e){}
}

function _libFolderParam(){
  return libActiveFolder ? `&folder=${encodeURIComponent(libActiveFolder.path)}` : '';
}

async function openFolderPicker(){
  const sheet=document.getElementById('folderPickerSheet');
  sheet.style.display='block';
  const list=document.getElementById('folderPickerList');
  list.innerHTML='<div class="empty">Loading…</div>';
  const folders=await fetch('/api/folders').then(r=>r.json());
  const sel=libActiveFolder;
  const allRow=`<div onclick="selectLibFolder(null)" style="display:flex;align-items:center;gap:12px;padding:12px 4px;cursor:pointer;border-bottom:1px solid var(--border)">
    <div style="width:36px;height:36px;border-radius:10px;background:var(--surface2);display:flex;align-items:center;justify-content:center;flex-shrink:0">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--text3)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
    </div>
    <div style="flex:1;min-width:0"><div style="font-weight:600;font-size:.9rem">All Folders</div><div style="font-size:.72rem;color:var(--text3)">Show all files</div></div>
    ${!sel?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>':''}
  </div>`;
  const rows=folders.map(f=>{
    const name=f.display_name||(f.path.split(/[\\/]/).pop())||f.path;
    const isSel=sel&&sel.id===f.id;
    const dot=f.available?'#4ade80':'var(--red)';
    const tip=f.available?'Available':(f.is_external?'Drive not connected':'Not found');
    return `<div onclick="selectLibFolder(${JSON.stringify({id:f.id,path:f.path,name})})" style="display:flex;align-items:center;gap:12px;padding:12px 4px;cursor:pointer;border-bottom:1px solid var(--border)">
      <div style="width:36px;height:36px;border-radius:10px;background:var(--surface2);display:flex;align-items:center;justify-content:center;flex-shrink:0">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${f.available?'var(--accent)':'var(--text3)'}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</div>
        <div style="font-size:.7rem;color:var(--text3);display:flex;align-items:center;gap:4px;margin-top:2px">
          <span style="width:6px;height:6px;border-radius:50%;background:${dot};display:inline-block"></span>${tip}
        </div>
      </div>
      ${isSel?'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>':''}
    </div>`;
  });
  list.innerHTML=allRow+rows.join('');
}

function closeFolderPicker(){
  document.getElementById('folderPickerSheet').style.display='none';
}

function selectLibFolder(f){
  libActiveFolder=f;
  document.getElementById('libFolderName').textContent=f?(f.name||(f.path.split(/[\\/]/).pop())):'All Folders';
  closeFolderPicker();
  libActiveFolderPath=''; libActiveFolderName='';
  document.getElementById('libBackBtn').style.display='none';
  loadLib(true);
}

function setLibGroup(g, el){
  document.querySelectorAll('#libGroupPills .pill').forEach(p=>p.classList.remove('active'));
  el.classList.add('active');
  libGroup=g;
  libActiveFolderPath=''; libActiveFolderName='';
  document.getElementById('libBackBtn').style.display='none';
  const bc=document.getElementById('libBreadcrumb'); if(bc) bc.style.display='none';
  loadLib(true);
}

function _updateViewToggleIcon(){
  const gridSVG=`<svg id="libViewIcon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>`;
  const listSVG=`<svg id="libViewIcon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`;
  const treeSVG=`<svg id="libViewIcon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/><line x1="12" y1="13" x2="12" y2="19"/><line x1="8" y1="19" x2="16" y2="19"/></svg>`;
  document.getElementById('libViewToggle').innerHTML=libView==='grid'?gridSVG:libView==='list'?listSVG:treeSVG;
}

function toggleLibView(){
  libView=libView==='grid'?'list':libView==='list'?'folders':'grid';
  _updateViewToggleIcon();
  if(libView==='folders'){ loadLibFolders(); return; }
  if(libActiveFolderPath||libGroup==='all'){ renderLibFlat(); return; }
  // Re-render grouped views from cache (no re-fetch needed)
  if((libGroup==='date'||libGroup==='type') && _libGroupedLabels.length){
    const vid=libGroup==='date'?'libDateView':'libTypeView';
    _hideAllLibViews();
    const container=document.getElementById(vid);
    container.style.display='block';
    _renderSectionedView(container, _libGroupedLabels, _libGroupedData);
  }
}

async function loadLib(reset=true){
  libLoaded=true;
  if(reset) libPg=1;
  if(libActiveFolderPath){ await loadLibFolder(reset); return; }
  if(libGroup==='tags')   { await loadLibTags(); return; }
  if(libGroup==='date')   { await loadLibDate(); return; }
  if(libGroup==='type')   { await loadLibType(); return; }
  await loadLibAll(reset);
}

function _hideAllLibViews(){
  ['libFlatGrid','libFlatList','libTagsView','libDateView','libTypeView','libFolderView'].forEach(id=>{
    const el=document.getElementById(id); if(el) el.style.display='none';
  });
  document.getElementById('libMore').style.display='none';
}

// ── Folder Tree View ──
async function loadLibFolders(){
  _hideAllLibViews();
  const tv=document.getElementById('libFolderView');
  tv.style.display='block';
  tv.innerHTML='<div class="empty" style="padding:32px 24px">Loading folders…</div>';
  const roots=await fetch('/api/folder-tree').then(r=>r.json()).catch(()=>[]);
  if(!roots.length){tv.innerHTML='<div class="empty">No folders configured.<br>Add folders in Admin ⚙</div>';return;}
  tv.innerHTML=roots.map(f=>_ftreeNodeHTML(f,0)).join('');
  _ftreeAttachEvents(tv);
}

function _ftreeNodeHTML(f,depth){
  const pad=depth*20;
  const qp=f.path.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
  const qn=(f.name||f.path.split(/[\\/]/).pop()||f.path).replace(/&/g,'&amp;').replace(/"/g,'&quot;');
  const chevron=f.has_children
    ?`<span class="ftree-chev" data-ftpath="${qp}"><svg class="ftree-chev-svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></span>`
    :`<span class="ftree-chev-ghost"></span>`;
  const cnt=f.file_count?`<span class="ftree-cnt">${f.file_count}</span>`:'';
  const nm=f.name||f.path.split(/[\\/]/).pop()||f.path;
  const bld=depth===0?'font-weight:600;':'';
  return `<div class="ftree-node" data-ftdepth="${depth}" data-ftpath="${qp}"><div class="ftree-row" style="padding-left:${pad+4}px">${chevron}<svg class="ftree-ico" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg><span class="ftree-name" style="${bld}" data-ftopen="${qp}" data-ftname="${qn}">${nm}</span>${cnt}</div><div class="ftree-kids" style="display:none"></div></div>`;
}

function _ftreeAttachEvents(container){
  container.querySelectorAll('.ftree-chev').forEach(chev=>{
    chev.addEventListener('click',async e=>{
      e.stopPropagation();
      const node=chev.closest('.ftree-node');
      const kids=node.querySelector('.ftree-kids');
      const svg=chev.querySelector('.ftree-chev-svg');
      if(kids.style.display==='none'){
        kids.style.display='block'; svg.style.transform='rotate(90deg)';
        if(!kids.dataset.loaded){
          kids.innerHTML='<div style="padding:6px 0 6px 16px;color:var(--text3);font-size:.78rem">Loading…</div>';
          const depth=(+node.dataset.ftdepth||0)+1;
          const path=node.dataset.ftpath;
          const subs=await fetch(`/api/folder-tree?path=${encodeURIComponent(path)}`).then(r=>r.json()).catch(()=>[]);
          if(!subs.length){svg.style.visibility='hidden';kids.innerHTML='';}
          else{kids.innerHTML=subs.map(f=>_ftreeNodeHTML(f,depth)).join('');_ftreeAttachEvents(kids);}
          kids.dataset.loaded='1';
        }
      } else {
        kids.style.display='none'; svg.style.transform='';
      }
    });
  });
  container.querySelectorAll('[data-ftopen]').forEach(el=>{
    el.addEventListener('click',e=>{
      const path=el.dataset.ftopen; const name=el.dataset.ftname;
      libView='grid'; _updateViewToggleIcon(); openLibFolder(path,name);
    });
  });
}

// ── All (flat) ──
async function loadLibAll(reset=true){
  if(reset){ _libItems=[]; }
  const q=document.getElementById('libQ').value.trim();
  let url=`/api/search?q=${encodeURIComponent(q)}&sort=recent&page=${libPg}&per_page=24${_libFolderParam()}`;
  const d=await fetch(url).then(r=>r.json());
  let items=d.results||[];
  _libItems=reset?items:[..._libItems,...items];
  renderLibFlat();
  document.getElementById('libMore').style.display=d.results.length<24?'none':'block';
}

function renderLibFlat(){
  _hideAllLibViews();
  const isGrid=libView==='grid';
  document.getElementById('libFlatGrid').style.display=isGrid?'':'none';
  document.getElementById('libFlatList').style.display=isGrid?'none':'';
  if(!_libItems.length){
    const empty='<div class="empty">No files found</div>';
    if(isGrid) document.getElementById('libFlatGrid').innerHTML=empty;
    else document.getElementById('libFlatList').innerHTML=empty;
    return;
  }
  if(isGrid){
    const poolKey=_newPool(_libItems);
    document.getElementById('libFlatGrid').innerHTML=_libItems.map((f,i)=>gridCardHTML(f,poolKey,i)).join('');
  } else {
    const poolKey=_newPool(_libItems);
    document.getElementById('libFlatList').innerHTML=_libItems.map((f,i)=>listRowHTML(f,poolKey,i)).join('');
  }
}

function loadMoreLib(){ libPg++; loadLib(false); }

// ── Folder cards ──
async function loadLibFolderCards(){
  _hideAllLibViews();
  const fv=document.getElementById('libFoldersView');
  fv.style.display='block';
  fv.innerHTML='<div class="empty" style="padding:24px">Loading…</div>';
  const data=await fetch('/api/folders').then(r=>r.json());
  if(!data.length){ fv.innerHTML='<div class="empty">No folders registered yet.</div>'; return; }
  fv.innerHTML=data.map(f=>folderCardHTML(f)).join('');
  const offline=data.filter(f=>f.is_external&&!f.available).length;
  const badge=document.getElementById('badge-library');
  if(badge){ badge.textContent=offline||''; badge.className='tab-badge'+(offline?' show':''); }
}

function openLibFolder(path, name){
  libActiveFolderPath=path; libActiveFolderName=name; libPg=1;
  document.getElementById('libBackBtn').style.display='flex';
  _updateBreadcrumb();
  loadLibFolder(true);
}

function _updateBreadcrumb(){
  const bc=document.getElementById('libBreadcrumb');
  if(!bc) return;
  if(!libActiveFolderPath){ bc.style.display='none'; return; }
  const rootName=libActiveFolder?(libActiveFolder.name||(libActiveFolder.path.split(/[\\/]/).pop())||'Library'):'All Folders';
  bc.innerHTML=`<div style="display:inline-flex;align-items:center;gap:0;background:var(--surface2);border:1px solid var(--border);border-radius:20px;padding:4px 10px">
    <span style="font-size:.75rem;margin-right:4px">📁</span>
    <span onclick="libBack()" style="color:var(--accent);cursor:pointer;font-size:.78rem;font-weight:600;text-decoration:none">${rootName}</span>
    <span style="color:var(--text3);font-size:.78rem;margin:0 5px">›</span>
    <span style="color:var(--text);font-size:.78rem;font-weight:700;max-width:160px;overflow:hidden;text-overflow:ellipsis;display:inline-block;vertical-align:middle">${libActiveFolderName}</span>
  </div>`;
  bc.style.display='block';
}

async function loadLibFolder(reset=true){
  if(reset) libPg=1;
  const url=`/api/search?q=&sort=recent&folder=${encodeURIComponent(libActiveFolderPath)}&page=${libPg}&per_page=24`;
  const d=await fetch(url).then(r=>r.json());
  const items=d.results||[];
  if(reset) _libItems=items; else _libItems=[..._libItems,...items];
  renderLibFlat();
  document.getElementById('libMore').style.display=items.length<24?'none':'block';
}

function libBack(){
  libActiveFolderPath=''; libActiveFolderName='';
  document.getElementById('libBackBtn').style.display='none';
  const bc=document.getElementById('libBreadcrumb'); if(bc) bc.style.display='none';
  _libItems=[];
  loadLib(true);
}

// ── Tags view ──
async function loadLibTags(){
  _hideAllLibViews();
  const tv=document.getElementById('libTagsView');
  tv.style.display='block';
  tv.innerHTML='<div class="empty" style="padding:24px">Loading tags…</div>';
  const tags=await fetch('/api/tags').then(r=>r.json());
  const withFiles=tags.filter(t=>t.n>0);
  if(!withFiles.length){ tv.innerHTML='<div class="empty">No tags yet — AI will add them automatically after uploads are processed.</div>'; return; }
  const maxN=withFiles[0].n||1;
  tv.innerHTML=`
    <div style="display:flex;flex-wrap:wrap;gap:10px;padding-bottom:16px">
      ${withFiles.map(t=>{
        const sz=Math.max(0.75,Math.min(1.2, 0.75 + (t.n/maxN)*0.45));
        const op=Math.max(0.6,Math.min(1, 0.6 + (t.n/maxN)*0.4));
        return `<button onclick="filterByTag('${t.name.replace(/'/g,"\\'")}','${t.color}')"
          style="background:${t.color}22;color:${t.color};border:1.5px solid ${t.color}66;border-radius:99px;padding:6px 14px;font-size:${sz}rem;opacity:${op};cursor:pointer;font-weight:600;transition:all .2s;display:flex;align-items:center;gap:5px">
          ${t.name}<span style="font-size:.65rem;opacity:.7">${t.n}</span>
        </button>`;
      }).join('')}
    </div>`;
}

function filterByTag(name, color){
  // Switch to All Files, apply tag filter via search box
  const pills=document.querySelectorAll('#libGroupPills .pill');
  pills.forEach(p=>p.classList.remove('active'));
  pills[0].classList.add('active');
  libGroup='all';
  document.getElementById('libQ').value=`#${name}`;
  loadLib(true);
}

// ── Popular tags strip (under search box) ──
async function loadPopularTags(){
  const strip=document.getElementById('libTagStrip');
  if(!strip) return;
  const tags=await fetch('/api/tags/popular?limit=20').then(r=>r.json()).catch(()=>[]);
  if(!tags.length){ strip.style.display='none'; return; }
  strip.style.display='flex';
  strip.innerHTML=tags.map(t=>`
    <span onclick="filterByTag('${t.name.replace(/'/g,"\\'")}','${t.color}')"
      style="flex-shrink:0;background:${t.color}22;color:${t.color};border:1px solid ${t.color}55;border-radius:99px;padding:4px 11px;font-size:.72rem;font-weight:600;cursor:pointer;white-space:nowrap">
      ${t.name}
    </span>`).join('');
}

// ── AI queue polling ──
let _aiPollTimer=null;
async function pollAIQueue(){
  try{
    const q=await fetch('/api/ai-queue').then(r=>r.json());
    const toast=document.getElementById('aiToast');
    if(!toast) return;
    if((q.pending||0)>0||(q.processing||0)>0){
      toast.style.display='flex';
      document.getElementById('aiToastMsg').textContent=q.next?`Tagging: ${q.next}`:'Waiting…';
      document.getElementById('aiToastCount').textContent=`${q.pending||0} left`;
      if(!_aiPollTimer) _aiPollTimer=setInterval(pollAIQueue,6000);
    } else {
      toast.style.display='none';
      clearInterval(_aiPollTimer); _aiPollTimer=null;
    }
  } catch(e){ /* Ollama may be offline */ }
}
// Start polling when library is opened — called from goTab('library')
function startAIQueuePolling(){
  pollAIQueue();
}

// ── Date helper: extract date from filename or fall back to analyzed_at ──
function _fileDate(f){
  const name=(f.path||'').split(/[\\/]/).pop();
  // IMG_20240115_123456, VID_20240115_, 20240115_
  let m=name.match(/(\d{4})(\d{2})(\d{2})[_\-T]/);
  if(m){ const d=new Date(+m[1],+m[2]-1,+m[3]); if(!isNaN(d)&&d.getFullYear()>=1990&&d.getFullYear()<=2099) return d; }
  // YYYY-MM-DD anywhere
  m=name.match(/(\d{4})-(\d{2})-(\d{2})/);
  if(m){ const d=new Date(+m[1],+m[2]-1,+m[3]); if(!isNaN(d)) return d; }
  // analyzed_at fallback
  if(f.analyzed_at){ const d=new Date(f.analyzed_at); if(!isNaN(d)) return d; }
  return null;
}
function _monthSortKey(date){ return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`; }
function _monthLabel(date){ return date.toLocaleString('default',{month:'long',year:'numeric'}); }

// ── Sectioned view renderer (shared by date + type) ──
function _renderSectionedView(container, labels, groups){
  if(!labels.length){ container.innerHTML='<div class="empty">No files found</div>'; return; }
  container.innerHTML=labels.map(label=>{
    const files=groups[label]||[];
    const secId='sec-'+label.replace(/[\s\W]+/g,'-');
    const collapsed=_collapsedSections.has(label);
    const poolKey=_newPool(files);
    const body=libView==='grid'
      ?`<div class="photo-grid">${files.map((f,i)=>gridCardHTML(f,poolKey,i)).join('')}</div>`
      :`<div>${files.map((f,i)=>listRowHTML(f,poolKey,i)).join('')}</div>`;
    return `<div style="margin-bottom:8px;border-radius:12px;overflow:hidden;background:var(--surface2);border:1px solid var(--border)">
      <div onclick="toggleSection('${label.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}','${secId}')" style="display:flex;align-items:center;justify-content:space-between;padding:12px 14px;cursor:pointer;-webkit-tap-highlight-color:transparent;user-select:none">
        <div>
          <div style="font-size:.88rem;font-weight:700;color:var(--text)">${label}</div>
          <div style="font-size:.72rem;color:var(--text3);margin-top:1px">${files.length} item${files.length!==1?'s':''}</div>
        </div>
        <svg id="chev-${secId}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--text3)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="transition:transform .22s;transform:rotate(${collapsed?'-90deg':'0deg'})"><polyline points="6 9 12 15 18 9"/></svg>
      </div>
      <div id="${secId}" style="display:${collapsed?'none':'block'};padding:0 8px 8px">${body}</div>
    </div>`;
  }).join('');
}

function toggleSection(label, secId){
  const el=document.getElementById(secId);
  const chev=document.getElementById('chev-'+secId);
  if(!el) return;
  const nowCollapsed=el.style.display==='none';
  el.style.display=nowCollapsed?'block':'none';
  if(chev) chev.style.transform=nowCollapsed?'rotate(0deg)':'rotate(-90deg)';
  if(nowCollapsed) _collapsedSections.delete(label);
  else _collapsedSections.add(label);
}

// ── By Date ──
async function loadLibDate(){
  _hideAllLibViews();
  const dv=document.getElementById('libDateView');
  dv.style.display='block';
  dv.innerHTML='<div class="empty" style="padding:24px">Loading…</div>';
  const q=document.getElementById('libQ').value.trim();
  const d=await fetch(`/api/search?q=${encodeURIComponent(q)}&sort=recent&per_page=500${_libFolderParam()}`).then(r=>r.json());
  const order=[], groups={};
  const seen=new Map(); // sortKey → label
  for(const f of (d.results||[])){
    const date=_fileDate(f);
    const label=date?_monthLabel(date):'Unknown Date';
    const key=date?_monthSortKey(date):'0000-00';
    if(!seen.has(key)){ seen.set(key,label); order.push({key,label}); groups[label]=[]; }
    groups[seen.get(key)].push(f);
  }
  order.sort((a,b)=>b.key.localeCompare(a.key));
  _libGroupedLabels=order.map(o=>o.label);
  _libGroupedData=groups;
  _renderSectionedView(dv, _libGroupedLabels, _libGroupedData);
}

// ── By Type ──
let _libActiveTypeTab='Photos';
async function loadLibType(){
  _hideAllLibViews();
  const tv=document.getElementById('libTypeView');
  tv.style.display='block';
  tv.innerHTML='<div class="empty" style="padding:24px">Loading…</div>';
  const q=document.getElementById('libQ').value.trim();
  const d=await fetch(`/api/search?q=${encodeURIComponent(q)}&sort=recent&per_page=500${_libFolderParam()}`).then(r=>r.json());
  const IMG=new Set(['jpg','jpeg','png','gif','bmp','webp','tiff','tif','heic','heif','avif']);
  const VID=new Set(['mp4','mov','avi','mkv','wmv','m4v','3gp','ts','mts','mxf','flv','webm']);
  const groups={Photos:[],Videos:[],Files:[]};
  for(const f of (d.results||[])){
    // Use file_type field if reliable, otherwise derive from path extension
    const ft=(f.file_type||'').toLowerCase();
    const ext=(f.path||'').split('.').pop().toLowerCase();
    if(ft==='image'||IMG.has(ext)) groups.Photos.push(f);
    else if(ft==='video'||VID.has(ext)) groups.Videos.push(f);
    else groups.Files.push(f);
  }
  // Always show all 3 tabs even if empty
  _libGroupedLabels=['Photos','Videos','Files'];
  _libGroupedData=groups;
  _renderTypeTabView(tv);
}

function _renderTypeTabView(tv){
  const labels=_libGroupedLabels;
  const groups=_libGroupedData;
  if(!labels.length){ tv.innerHTML='<div class="empty">No files found</div>'; return; }
  if(!labels.includes(_libActiveTypeTab)) _libActiveTypeTab=labels[0];
  const icons={Photos:'🖼',Videos:'🎬',Files:'📄'};
  const tabBar=`<div style="display:flex;gap:6px;padding:10px 12px 0;overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none">
    ${labels.map(l=>`<button onclick="switchLibTypeTab('${l}')" style="flex-shrink:0;display:flex;align-items:center;gap:6px;padding:8px 16px;border-radius:10px;border:1px solid ${l===_libActiveTypeTab?'var(--accent)':'var(--border)'};background:${l===_libActiveTypeTab?'rgba(129,140,248,.15)':'var(--surface2)'};color:${l===_libActiveTypeTab?'var(--accent)':'var(--text3)'};font-size:.83rem;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap">
      ${icons[l]||'📁'} ${l}
      <span style="background:rgba(255,255,255,.08);padding:1px 7px;border-radius:99px;font-size:.7rem">${groups[l].length}</span>
    </button>`).join('')}
  </div>`;
  const files=groups[_libActiveTypeTab]||[];
  const poolKey=files.length ? _newPool(files) : null;
  const body=files.length===0
    ?`<div class="empty" style="padding:40px 16px">No ${_libActiveTypeTab.toLowerCase()} in this library</div>`
    :libView==='grid'
      ?`<div class="photo-grid" style="padding:12px 8px">${files.map((f,i)=>gridCardHTML(f,poolKey,i)).join('')}</div>`
      :`<div style="padding:8px">${files.map((f,i)=>listRowHTML(f,poolKey,i)).join('')}</div>`;
  tv.innerHTML=tabBar+`<div id="typeTabContent" style="margin-top:12px">${body}</div>`;
}

function switchLibTypeTab(label){
  _libActiveTypeTab=label;
  const tv=document.getElementById('libTypeView');
  _renderTypeTabView(tv);
}

// ── List row ──
function listRowHTML(f, poolKey, idx){
  const name=f.path.split(/[\\/]/).pop();
  const ep=encodeURIComponent(f.path);
  const sp=f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  const s=f.device_status||'';
  const sz=f.file_size?formatSize(f.file_size):'';
  const badge=s?syncBadgeHTML(s):'';
  const thumb=isVid(f.path)
    ?`<div style="position:relative;width:44px;height:44px;border-radius:8px;overflow:hidden;flex-shrink:0;background:#111">
        <img src="/thumb?path=${ep}" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover" loading="lazy" onerror="this.remove()">
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.25)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="rgba(255,255,255,.9)"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </div>
      </div>`
    :`<img src="/thumb?path=${ep}" style="width:44px;height:44px;object-fit:cover;border-radius:8px;flex-shrink:0" loading="lazy" onerror="this.outerHTML='<div style=\\'width:44px;height:44px;border-radius:8px;background:var(--surface2);display:flex;align-items:center;justify-content:center\\'><svg width=20 height=20 viewBox=\\'0 0 24 24\\' fill=none stroke=\\'var(--text3)\\' stroke-width=1.5><rect x=3 y=3 width=18 height=18 rx=2/></svg></div>'">`;
  const clickable=poolKey!=null;
  return `<div class="cell" style="${clickable?'cursor:pointer;-webkit-tap-highlight-color:transparent':''}" ${clickable?`onclick="openViewer('${poolKey}',${idx})"`:''}>
    ${thumb}
    <div class="cell-body">
      <div class="cell-title" style="font-size:.85rem">${name}</div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:3px">
        ${sz?`<span class="cell-sub">${sz}</span>`:''}${badge}
      </div>
      ${f.ai_caption?`<div class="cell-sub" style="margin-top:3px;font-style:italic;opacity:.75;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:220px">${f.ai_caption.slice(0,70)}</div>`:''}
    </div>
    <a href="/download?path=${ep}" class="btn btn-sm gb-dl" download onclick="event.stopPropagation();onDownload('${sp}',this)" style="text-decoration:none;flex-shrink:0">⬇</a>
  </div>`;
}

// ── Folder card ──
function folderCardHTML(f){
  const name=f.display_name||f.path.split(/[\\/]/).pop()||f.path;
  const avail=f.available, ext=f.is_external;
  const statusDot=`<span style="width:8px;height:8px;border-radius:50%;background:${avail?'#4ade80':'var(--red)'};display:inline-block"></span>`;
  const statusLabel=avail?'Available':(ext?'Drive not connected':'Not found');
  const driveTag=ext?`<span style="background:#1c1e3a;color:var(--accent);font-size:.65rem;padding:2px 7px;border-radius:99px;font-weight:600">${f.drive} External</span>`:'';
  const subs=(f.subfolders||[]).slice(0,6);
  const subChips=subs.map(s=>`<span style="background:var(--surface2);color:var(--text3);font-size:.64rem;padding:2px 8px;border-radius:99px;border:1px solid var(--border)">${s}</span>`).join('');
  const moreCount=(f.subfolders||[]).length-subs.length;
  const fp=f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  const qName=name.replace(/'/g,"\\'");
  return `<div class="card mb12" style="overflow:visible">
    <div class="cell" onclick="${avail?`openLibFolder('${fp}','${qName}')`:''}" style="${avail?'cursor:pointer':'cursor:default;opacity:.7'}">
      <div class="cell-icon" style="background:${avail?'#1e1e3a':'#2a1010'};color:${avail?'var(--accent)':'var(--red)'}">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      </div>
      <div class="cell-body">
        <div style="display:flex;align-items:center;gap:7px;flex-wrap:wrap">
          <span class="cell-title">${name}</span>${driveTag}
        </div>
        <div style="display:flex;align-items:center;gap:6px;margin-top:3px">
          ${statusDot}
          <span style="font-size:.72rem;color:${avail?'#4ade80':'var(--red)'};font-weight:500">${statusLabel}</span>
          <span style="color:var(--text3);font-size:.72rem">·</span>
          <span style="font-size:.72rem;color:var(--text3)">${f.n} files</span>
        </div>
        ${avail?`<div style="font-size:.65rem;color:var(--text3);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f.path}</div>`:''}
      </div>
      ${avail?`<span style="color:var(--text3)"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg></span>`:''}
    </div>
    ${!avail&&ext?`<div style="padding:10px 16px 12px;border-top:1px solid var(--border)"><div style="background:#2a1010;border-radius:8px;padding:10px 12px;font-size:.75rem;color:#f87171;line-height:1.5"><strong>Drive not connected.</strong> Plug in ${f.drive} to access ${f.n} files.</div></div>`:''}
    ${avail&&subs.length?`<div style="padding:0 14px 12px;display:flex;flex-wrap:wrap;gap:5px">${subChips}${moreCount>0?`<span style="font-size:.64rem;color:var(--text3);padding:2px 8px">+${moreCount} more</span>`:''}</div>`:''}
  </div>`;
}

// ── SYNC SCREEN ────────────────────────────────────────────────────────────
async function loadSync(){
  const d=await fetch('/api/search?q=&sort=recent&per_page=200').then(r=>r.json());
  const loaded=d.results.filter(f=>f.device_status==='loaded').length;
  const offloaded=d.results.filter(f=>f.device_status==='offloaded').length;
  const pconly=d.results.filter(f=>f.device_status==='pc_only').length;
  const none=d.results.filter(f=>!f.device_status).length;
  document.getElementById('statLoaded').textContent=loaded;
  document.getElementById('statOffloaded').textContent=offloaded;
  document.getElementById('statPcOnly').textContent=pconly;
  document.getElementById('statNone').textContent=none;

  const tracked=d.results.filter(f=>f.device_status&&f.device_status!=='');
  const list=document.getElementById('syncList');
  if(!tracked.length){ list.innerHTML='<div class="empty">No tracked files yet.<br>Download a file to start.</div>'; return; }
  list.innerHTML=tracked.map(f=>syncCellHTML(f)).join('');
}

function syncCellHTML(f){
  const name=f.path.split(/[\\/]/).pop();
  const ep=encodeURIComponent(f.path);
  const sp=f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  const s=f.device_status||'';
  const badge=syncBadgeHTML(s);
  const thumb=isVid(f.path)
    ?`<svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="var(--text3)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>`
    :`<img src="/thumb?path=${ep}" style="width:42px;height:42px;object-fit:cover;border-radius:8px" onerror="this.outerHTML='<svg width=42 height=42 viewBox=\\'0 0 24 24\\' fill=none stroke=\\'var(--text3)\\' stroke-width=1.5><rect x=3 y=3 width=18 height=18 rx=2/><circle cx=8.5 cy=8.5 r=1.5/><polyline points=\\'21 15 16 10 5 21\\'/></svg>'">`;
  let action='';
  if(s==='loaded')     action=`<button class="btn btn-sm" style="background:#2e1065;color:var(--purple);font-size:.72rem" onclick="setSync('${sp}','offloaded',this)">Mark Offloaded</button>`;
  else if(s==='offloaded') action=`<button class="btn btn-sm gb-dl" onclick="setSync('${sp}','loaded',this)">Re-download</button>`;
  else if(s==='pc_only') action=`<button class="btn btn-sm gb-pc" onclick="setSync('${sp}','',this)">Unmark</button>`;
  return `<div class="cell" style="align-items:flex-start;gap:10px">
    <div style="flex-shrink:0;font-size:1.8rem;line-height:1">${thumb}</div>
    <div style="flex:1;min-width:0">
      <div style="font-size:.82rem;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</div>
      <div style="margin-top:4px">${badge}</div>
      <div style="margin-top:8px">${action}</div>
    </div>
    <a href="/download?path=${ep}" class="btn btn-sm gb-dl" style="text-decoration:none;flex-shrink:0" download onclick="onDownload('${sp}',this)">⬇</a>
  </div>`;
}

// ── GRID CARD ──────────────────────────────────────────────────────────────
// ── MEDIA VIEWER ───────────────────────────────────────────────────────────
const _viewerPools={};
let _viewerPoolCtr=0;
function _newPool(items){ const k='vp'+(++_viewerPoolCtr); _viewerPools[k]=items; return k; }

let viewerItems=[], viewerIdx=0;
let _vx0=0,_vy0=0,_vdx=0,_vdy=0;
let _vPinching=false,_vPinchD0=0,_vScale=1;

function openViewer(poolKey,idx){
  const items=_viewerPools[poolKey];
  if(!items||!items.length) return;
  viewerItems=items;
  viewerIdx=Math.max(0,Math.min(idx,items.length-1));
  _vScale=1;
  const v=document.getElementById('mediaViewer');
  v.style.display='flex';
  v.style.opacity='0';
  v.style.transition='opacity .18s';
  _renderViewerSlide();
  requestAnimationFrame(()=>{ v.style.opacity='1'; setTimeout(()=>v.style.transition='',200); });
}

function closeViewer(){
  const v=document.getElementById('mediaViewer');
  v.style.transition='opacity .18s';
  v.style.opacity='0';
  const slide=document.getElementById('viewerSlide');
  const vid=slide.querySelector('video');
  if(vid){ vid.pause(); vid.src=''; }
  setTimeout(()=>{ v.style.display='none'; v.style.opacity='1'; v.style.transition=''; },190);
}

function _renderViewerSlide(){
  const f=viewerItems[viewerIdx];
  const n=viewerItems.length;
  document.getElementById('viewerCounter').textContent=`${viewerIdx+1} \u2022 ${n}`;
  const slide=document.getElementById('viewerSlide');
  const oldVid=slide.querySelector('video');
  if(oldVid){ oldVid.pause(); oldVid.src=''; }
  _vScale=1;
  slide.style.transform='';
  slide.style.transition='';
  const ep=encodeURIComponent(f.path);
  if(isVid(f.path)){
    slide.innerHTML='';
    const vid=document.createElement('video');
    vid.controls=true; vid.playsInline=true; vid.autoplay=true;
    vid.setAttribute('webkit-playsinline','');
    vid.style.cssText='max-width:100%;max-height:100%;object-fit:contain;background:#000';
    vid.onerror=()=>{
      slide.innerHTML=`<div style="color:var(--text3);text-align:center;padding:40px 24px">
        <div style="font-size:2rem;margin-bottom:12px">⚠️</div>
        <div style="font-size:.9rem;font-weight:600;color:var(--text);margin-bottom:6px">Cannot play this video</div>
        <div style="font-size:.75rem">This format may not be supported on your device.<br>Try on PC or convert to MP4.</div>
      </div>`;
    };
    vid.src=`/stream?path=${ep}`;
    slide.appendChild(vid);
  } else {
    slide.innerHTML=`<img id="viewerImg" src="/img?path=${ep}" style="max-width:100%;max-height:100%;object-fit:contain;transform-origin:center center;will-change:transform" ondblclick="_viewerZoom(event)" onerror="this.style.display='none'">`;
  }
  // Update caption overlay
  const cap=document.getElementById('viewerCaption');
  const capTxt=document.getElementById('viewerCaptionText');
  const text=f.ai_caption||(f.ai_description?f.ai_description.slice(0,120):'');
  if(text){ capTxt.textContent=text; cap.style.display='block'; }
  else { cap.style.display='none'; }
}

function _viewerZoom(e){
  const img=document.getElementById('viewerImg');
  if(!img) return;
  if(_vScale>1){ _vScale=1; img.style.transform='scale(1)'; img.style.transition='transform .2s'; return; }
  // Zoom toward tap point
  const r=img.getBoundingClientRect();
  const ox=((e.clientX-r.left)/r.width-0.5)*100;
  const oy=((e.clientY-r.top)/r.height-0.5)*100;
  img.style.transformOrigin=`${50+ox}% ${50+oy}%`;
  _vScale=3;
  img.style.transform='scale(3)';
  img.style.transition='transform .25s ease';
  setTimeout(()=>img&&(img.style.transition=''),260);
}

function _vtStart(e){
  if(e.touches.length===2){
    _vPinching=true; _vPinchD0=_pinchDist(e);
    const img=document.getElementById('viewerImg');
    if(img) img.style.transition='';
    return;
  }
  _vPinching=false;
  _vx0=e.touches[0].clientX; _vy0=e.touches[0].clientY;
  _vdx=0; _vdy=0;
}

function _vtMove(e){
  if(_vPinching&&e.touches.length===2){
    const d=_pinchDist(e);
    const img=document.getElementById('viewerImg');
    if(img){ _vScale=Math.max(1,Math.min(5,_vScale*(d/_vPinchD0))); img.style.transform=`scale(${_vScale})`; }
    _vPinchD0=d; return;
  }
  if(e.touches.length!==1||_vScale>1.05) return;
  _vdx=e.touches[0].clientX-_vx0;
  _vdy=e.touches[0].clientY-_vy0;
  const slide=document.getElementById('viewerSlide');
  const viewer=document.getElementById('mediaViewer');
  if(Math.abs(_vdx)>Math.abs(_vdy)&&Math.abs(_vdx)>8){
    e.preventDefault();
    slide.style.transform=`translateX(${_vdx}px)`;
    slide.style.transition='none';
  } else if(_vdy>0&&Math.abs(_vdy)>Math.abs(_vdx)&&Math.abs(_vdy)>10){
    e.preventDefault();
    const prog=Math.min(1,_vdy/250);
    viewer.style.opacity=1-prog*0.7;
    slide.style.transform=`translateY(${_vdy*0.6}px) scale(${1-prog*0.08})`;
    slide.style.transition='none';
  }
}

function _vtEnd(e){
  if(_vPinching){ _vPinching=false; return; }
  const slide=document.getElementById('viewerSlide');
  const viewer=document.getElementById('mediaViewer');
  // Swipe down to close
  if(_vdy>90&&Math.abs(_vdy)>Math.abs(_vdx)){ closeViewer(); return; }
  // Reset vertical drag
  if(Math.abs(_vdy)>Math.abs(_vdx)&&_vdy<=90){
    slide.style.transform=''; slide.style.transition='transform .2s ease';
    viewer.style.opacity='1'; return;
  }
  // Horizontal nav
  const W=window.innerWidth;
  if(Math.abs(_vdx)>W*0.22){
    const dir=_vdx<0?1:-1;
    const ni=viewerIdx+dir;
    if(ni>=0&&ni<viewerItems.length){
      slide.style.transform=`translateX(${dir>0?'-':''}${W}px)`;
      slide.style.transition='transform .22s ease';
      hideViewerInfo();
      setTimeout(()=>{ viewerIdx=ni; _renderViewerSlide(); },220);
    } else {
      slide.style.transform=''; slide.style.transition='transform .2s ease';
    }
  } else {
    slide.style.transform=''; slide.style.transition='transform .2s ease';
    setTimeout(()=>slide.style.transition='',210);
  }
  viewer.style.opacity='1'; _vdx=0; _vdy=0;
}

function _pinchDist(e){
  const dx=e.touches[0].clientX-e.touches[1].clientX;
  const dy=e.touches[0].clientY-e.touches[1].clientY;
  return Math.sqrt(dx*dx+dy*dy);
}

function toggleViewerInfo(){
  const p=document.getElementById('viewerInfo');
  if(p.style.display==='none'||!p.style.display){ showViewerInfo(); } else { hideViewerInfo(); }
}
function showViewerInfo(){
  const p=document.getElementById('viewerInfo');
  _buildViewerInfo();
  p.style.display='block';
  p.style.transform='translateY(100%)';
  p.style.transition='transform .25s ease';
  requestAnimationFrame(()=>{ p.style.transform='translateY(0)'; setTimeout(()=>p.style.transition='',260); });
}
function hideViewerInfo(){
  const p=document.getElementById('viewerInfo');
  p.style.transition='transform .2s ease';
  p.style.transform='translateY(100%)';
  setTimeout(()=>{ p.style.display='none'; p.style.transition=''; p.style.transform=''; },210);
}

function _buildViewerInfo(){
  const f=viewerItems[viewerIdx];
  const name=f.path.split(/[\\/]/).pop();
  const ext=(name.split('.').pop()||'').toUpperCase();
  const size=f.file_size?formatSize(f.file_size):'';
  const ep=encodeURIComponent(f.path);
  const sp=f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  const syncColors={loaded:'#4ade80',offloaded:'#a78bfa',pc_only:'#78716c'};
  const syncLabels={loaded:'On Device',offloaded:'Offloaded',pc_only:'PC Only'};
  const s=f.device_status||'';
  // Date
  const dateObj=_fileDate(f);
  const dateStr=dateObj?dateObj.toLocaleDateString('default',{day:'numeric',month:'long',year:'numeric',hour:'2-digit',minute:'2-digit'}):'';
  // Metadata grid
  const metaItems=[
    size?['Size',size]:null,
    ext?['Type',ext]:null,
    dateStr?['Date',dateStr]:null,
    f.category?['Category',f.category]:null,
    f.confidence?['Confidence',f.confidence+'%']:null,
  ].filter(Boolean);
  const metaGrid=metaItems.map(([k,v])=>`
    <div style="background:var(--surface2);border-radius:10px;padding:10px 12px;min-width:0">
      <div style="font-size:.65rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text3);margin-bottom:3px">${k}</div>
      <div style="font-size:.82rem;font-weight:600;color:var(--text);word-break:break-word">${v}</div>
    </div>`).join('');
  const tags=(f.tags||[]).map(t=>`<span style="background:var(--surface2);border:1px solid var(--border);padding:3px 10px;border-radius:99px;font-size:.75rem;color:var(--text2)">${t}</span>`).join('');
  document.getElementById('viewerInfoBody').innerHTML=`
    <div style="font-weight:700;font-size:.95rem;word-break:break-all;margin-bottom:10px">${name}</div>
    ${metaGrid?`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px">${metaGrid}</div>`:''}
    ${s?`<div style="display:flex;align-items:center;gap:6px;margin-bottom:14px;font-size:.78rem;color:${syncColors[s]||'var(--text3)'}"><span style="width:8px;height:8px;border-radius:50%;background:${syncColors[s]||'var(--text3)'}"></span>${syncLabels[s]||s}</div>`:''}
    ${f.ai_caption?`<div style="font-size:.87rem;color:var(--text2);font-style:italic;padding:10px 12px;background:var(--surface2);border-radius:10px;margin-bottom:10px">"${f.ai_caption}"</div>`:''}
    ${f.ai_description?`<div style="font-size:.8rem;color:var(--text3);margin-bottom:12px;line-height:1.5">${f.ai_description}</div>`:''}
    ${f.ocr_text?`<div style="margin-bottom:12px"><div style="font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--text3);margin-bottom:5px">📝 Text in image</div><div style="font-size:.8rem;color:var(--text2);padding:10px 12px;background:var(--surface2);border-radius:10px;white-space:pre-wrap;line-height:1.55;max-height:140px;overflow-y:auto">${f.ocr_text.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div></div>`:''}
    ${tags?`<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">${tags}</div>`:''}
    <a href="/download?path=${ep}" download onclick="onDownload('${sp}',this)" style="display:flex;align-items:center;justify-content:center;gap:8px;background:var(--accent);color:#fff;padding:13px;border-radius:12px;font-weight:600;font-size:.9rem;text-decoration:none;margin-bottom:8px">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
      Save to Phone
    </a>
    <div style="display:flex;gap:8px">
      ${s!=='loaded'?`<button onclick="setSync('${sp}','loaded',this);viewerItems[viewerIdx].device_status='loaded';_buildViewerInfo()" style="flex:1;padding:11px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:#4ade80;font-size:.8rem;font-weight:600;cursor:pointer">🟢 Mark On Device</button>`:''}
      ${s!=='offloaded'?`<button onclick="setSync('${sp}','offloaded',this);viewerItems[viewerIdx].device_status='offloaded';_buildViewerInfo()" style="flex:1;padding:11px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;color:var(--purple);font-size:.8rem;font-weight:600;cursor:pointer">🟣 Offload</button>`:''}
    </div>`;
}

function viewerSave(){
  const f=viewerItems[viewerIdx];
  const sp=f.path.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  window.location.href='/download?path='+encodeURIComponent(f.path);
  setTimeout(()=>{ setSync(sp,'loaded',null); f.device_status='loaded'; showToast('✅ Saving — marked On Device'); },800);
}

// ── GRID CARD (photo cell) ──────────────────────────────────────────────────
function _fileIcon(path){
  const ext=(path||'').split('.').pop().toLowerCase();
  const m={pdf:'📄',doc:'📝',docx:'📝',xls:'📊',xlsx:'📊',ppt:'📑',pptx:'📑',
           zip:'🗜',rar:'🗜','7z':'🗜',txt:'📃',csv:'📊',mp3:'🎵',wav:'🎵',
           aac:'🎵',flac:'🎵',exe:'⚙️',apk:'📦'};
  return m[ext]||'📄';
}
const _IMG_EXTS=new Set(['jpg','jpeg','png','gif','bmp','webp','tiff','tif','heic','heif','avif']);
function gridCardHTML(f, poolKey, idx){
  const ep=encodeURIComponent(f.path);
  const s=f.device_status||'';
  const vid=isVid(f.path);
  const ext=(f.path||'').split('.').pop().toLowerCase();
  const isImg=_IMG_EXTS.has(ext);
  const dotColor={loaded:'#4ade80',offloaded:'#a78bfa',pc_only:'#78716c'}[s]||'';
  const dot=dotColor?`<div class="ph-dot" style="background:${dotColor}"></div>`:'';
  let thumb;
  if(vid){
    thumb=`<div class="ph-vid">
      <img src="/thumb?path=${ep}" loading="lazy" style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover" onerror="this.remove()">
      <div class="ph-vid-ic"><svg width="28" height="28" viewBox="0 0 24 24" fill="rgba(255,255,255,.9)"><polygon points="5 3 19 12 5 21 5 3"/></svg></div>
      <div class="ph-dur">VIDEO</div>
    </div>`;
  } else if(isImg){
    thumb=`<img src="/thumb?path=${ep}" loading="lazy" onerror="this.style.display='none'">`;
  } else {
    const icon=_fileIcon(f.path);
    const name=(f.path||'').split(/[\\/]/).pop();
    const extLabel=ext.toUpperCase();
    thumb=`<div class="ph-file-thumb">
      <div style="font-size:2rem;line-height:1">${icon}</div>
      <div style="font-size:.55rem;font-weight:700;letter-spacing:.04em;color:var(--accent);margin-top:4px;opacity:.8">${extLabel}</div>
      <div style="font-size:.5rem;color:var(--text3);margin-top:2px;max-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center">${name}</div>
    </div>`;
  }
  return `<div class="photo-cell" onclick="openViewer('${poolKey}',${idx})">${thumb}${dot}</div>`;
}

function syncBadgeHTML(s){
  if(s==='loaded')     return '<span class="sync-badge s-loaded"><span style="width:6px;height:6px;border-radius:50%;background:#4ade80;display:inline-block"></span> On Device</span>';
  if(s==='offloaded')  return '<span class="sync-badge s-offloaded"><span style="width:6px;height:6px;border-radius:50%;background:var(--purple);display:inline-block"></span> Offloaded</span>';
  if(s==='pc_only')    return '<span class="sync-badge s-pc_only"><span style="width:6px;height:6px;border-radius:50%;background:#78716c;display:inline-block"></span> PC Only</span>';
  return '';
}

// ── SYNC ACTIONS ───────────────────────────────────────────────────────────
async function setSync(path,status,el){
  await fetch('/api/device-status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paths:[path],status})});
  if(curTab==='sync') loadSync();
}

function refreshCard(btn,path,newStatus){
  // Find nearest grid-card and update badge
  const card=btn.closest('.grid-card');
  if(!card) return;
  const existing=card.querySelector('.grid-badge');
  if(existing) existing.remove();
  if(newStatus){
    const thumb=card.querySelector('.grid-thumb,.grid-thumb img');
    if(thumb){ const bd=document.createElement('div'); bd.className='grid-badge'; bd.innerHTML=syncBadgeHTML(newStatus); card.insertBefore(bd,card.querySelector('.grid-info')||card.firstChild.nextSibling); }
  }
}

function onDownload(path,el){
  setTimeout(async()=>{
    await setSync(path,'loaded',el);
    refreshCard(el,path,'loaded');
    showToast('✅ Saved — marked On Device');
  }, 600);
}

function triggerDl(path,btn){
  const ep=encodeURIComponent(path);
  window.location.href='/download?path='+ep;
  onDownload(path,btn);
}

// ── FORMAT ─────────────────────────────────────────────────────────────────
function formatSize(b){ if(b>1e9) return (b/1e9).toFixed(1)+' GB'; if(b>1e6) return (b/1e6).toFixed(1)+' MB'; if(b>1e3) return Math.round(b/1e3)+' KB'; return b+' B'; }

let _dt;
function debounce(fn,ms){ return(...a)=>{ clearTimeout(_dt); _dt=setTimeout(()=>fn(...a),ms); }; }

// ── DUPLICATE CHECK ────────────────────────────────────────────────────────
let _pendingFiles=[], _dupData=[];

async function checkAndUpload(files){
  const arr=Array.from(files);
  const payload=arr.map(f=>({name:f.name,size:f.size}));
  const r=await fetch('/api/check-duplicates',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json()).catch(()=>({duplicates:[]}));
  const dups=r.duplicates||[];
  if(dups.length){
    _pendingFiles=arr; _dupData=dups;
    const newCount=arr.length-dups.length;
    document.getElementById('dupSkipBtn').textContent=`Skip duplicates — upload ${newCount} new ${newCount===1?'file':'files'}`;
    document.getElementById('dupList').innerHTML=dups.map(d=>`
      <div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
        <div style="min-width:0;flex:1">
          <div style="font-size:.82rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${d.name}</div>
          <div style="font-size:.72rem;color:var(--red);margin-top:1px">${d.match_path?d.match_path.split(/[\\/]/).slice(-3).join('/'):'already exists on PC'}</div>
        </div>
      </div>`).join('');
    document.getElementById('dupOverlay').style.display='block';
  } else {
    uploadFiles(arr);
  }
}

function closeDupSheet(){ document.getElementById('dupOverlay').style.display='none'; }

function dupAction(mode){
  closeDupSheet();
  const dupNames=new Set(_dupData.map(d=>d.name));
  let toUpload=_pendingFiles;
  if(mode==='skip') toUpload=_pendingFiles.filter(f=>!dupNames.has(f.name));
  if(toUpload.length) uploadFiles(toUpload);
  _pendingFiles=[]; _dupData=[];
}

// ── INIT ───────────────────────────────────────────────────────────────────
loadRecentUploads();
// Load library immediately; then apply folder filter if exactly one folder is configured
loadLib(true);
_initLibraryFolder().then(()=>{ if(libActiveFolder) loadLib(true); });
startAIQueuePolling();
loadPopularTags();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    print(f"Starting AI File Classifier on http://0.0.0.0:{PORT}")
    print(f"Local network: http://{LOCAL_IP}:{PORT}")
    print(f"Mobile page:   http://{LOCAL_IP}:{PORT}/mobile")
    app.run(host='0.0.0.0', port=PORT, debug=False)
