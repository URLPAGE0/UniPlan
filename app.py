"""Blackboard UniPlan: Sprint 2 + account isolation. Run: python app.py."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from email.parser import BytesParser
from email.policy import default
from datetime import date, timedelta
from collections import Counter
from http.cookies import SimpleCookie
import time
import argparse
import io
import json
import os
import re
import sqlite3
import uuid
from pypdf import PdfReader
from study_features import answer_question, make_practice_questions, study_plan, to_csv
from auth import (SESSION_COOKIE, SESSION_SECONDS, DUMMY_HASH, normal_email, valid_name,
                  valid_password, hash_password, verify_password, create_session,
                  get_user, revoke_session, login_limiter, register_limiter)

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('UNIPLAN_DATA_DIR', ROOT / 'data'))
MAX_UPLOAD = 10 * 1024 * 1024


def connect():
    db = sqlite3.connect(DATA / 'uniplan_accounts.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'uploads').mkdir(exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE TABLE IF NOT EXISTS courses (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          code TEXT NOT NULL COLLATE NOCASE, name TEXT NOT NULL, color TEXT NOT NULL,
          UNIQUE(user_id, code));
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          title TEXT NOT NULL, course_id INTEGER NOT NULL REFERENCES courses(id),
          kind TEXT NOT NULL, due_date TEXT NOT NULL, priority TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS lectures (
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          filename TEXT NOT NULL, stored_name TEXT NOT NULL,
          course_id INTEGER NOT NULL REFERENCES courses(id), pages INTEGER NOT NULL,
          extracted TEXT NOT NULL, summary TEXT);
        ''')


def clean_string(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{name} is required and must be at most {limit} characters.')
    return value.strip()


def course_exists(db, value, uid):
    if isinstance(value, bool) or not isinstance(value, int) or not db.execute('SELECT id FROM courses WHERE id=? AND user_id=?', (value, uid)).fetchone():
        raise ValueError('Choose an existing course.')
    return value


def task_values(db, body, uid):
    title = clean_string(body.get('title'), 'Title', 150)
    course = course_exists(db, body.get('course_id'), uid)
    kind, priority = body.get('kind'), body.get('priority')
    if kind not in ('Assignment', 'Exam') or priority not in ('High', 'Medium', 'Low'):
        raise ValueError('Select a valid task type and priority.')
    due = body.get('due_date')
    try:
        if date.fromisoformat(due).isoformat() != due:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Enter a valid due date (YYYY-MM-DD).')
    notes = body.get('notes', '')
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError('Notes must be at most 2000 characters.')
    completed = body.get('completed', False)
    if not isinstance(completed, bool):
        raise ValueError('Completion must be true or false.')
    return title, course, kind, due, priority, notes.strip(), int(completed)


def summarize(pages):
    """Extractive sentence ranking; each bullet retains its real source page."""
    stop = set('the a an and or to of in is are was were for on with as by this that it be from at we you can will have has not into'.split())
    candidates = []
    for page in pages:
        text = re.sub(r'\s+', ' ', page['text']).strip()
        sentences = re.split(r'(?<=[.!?])\s+', text)
        for sentence in sentences:
            if len(sentence) >= 35:
                words = [w for w in re.findall(r'[^\W\d_]+', sentence.lower()) if w not in stop and len(w) > 2]
                candidates.append((sentence[:1000], page['page'], words))
    if not candidates:
        raise ValueError('There is not enough readable text to summarize. Try a text-based lecture PDF.')
    counts = Counter(w for _, _, words in candidates for w in words)
    ranked = sorted(range(len(candidates)), key=lambda i: sum(counts[w] for w in set(candidates[i][2])) / max(1, len(candidates[i][2])) ** .5, reverse=True)
    chosen, seen = [], set()
    for i in ranked:
        sentence = candidates[i][0]
        if sentence not in seen:
            chosen.append(i)
            seen.add(sentence)
        if len(chosen) == 5:
            break
    return [{'text': candidates[i][0], 'page': candidates[i][1]} for i in sorted(chosen)]


class Handler(BaseHTTPRequestHandler):
    def send(self, code, payload, content_type='application/json; charset=utf-8', cookie=None):
        data = json.dumps(payload).encode() if content_type.startswith('application/json') else payload
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        if os.environ.get('UNIPLAN_COOKIE_SECURE') == '1':
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        if content_type.startswith('text/csv'):
            filename = 'uniplan-study-plan.csv' if 'study-plan' in self.path else 'uniplan-tasks.csv'
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def session_cookie(self, token, clear=False):
        # Public HTTPS deployments MUST set UNIPLAN_COOKIE_SECURE=1.
        secure = '; Secure' if os.environ.get('UNIPLAN_COOKIE_SECURE') == '1' else ''
        if clear:
            return f'{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}'
        return f'{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}{secure}'

    def cookie_token(self):
        raw = self.headers.get('Cookie', '')
        if len(raw) > 8192:
            return None
        try:
            cookie = SimpleCookie()
            cookie.load(raw)
            return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else None
        except Exception:
            return None

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def do_PUT(self):
        self.handle_request('PUT')

    def do_DELETE(self):
        self.handle_request('DELETE')

    def body(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise ValueError('Invalid request size.')
        if size < 0 or size > MAX_UPLOAD + 65536:
            raise ValueError('The maximum upload size is 10 MB.')
        return self.rfile.read(size)

    def json_body(self):
        if not self.headers.get('Content-Type', '').split(';')[0].strip().lower() == 'application/json':
            raise ValueError('Send an application/json request.')
        if int(self.headers.get('Content-Length','0')) > 16384:
            raise ValueError('JSON request is too large.')
        try:
            data = json.loads(self.body())
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Send a valid JSON object.")
        if not isinstance(data, dict):
            raise ValueError("Send a valid JSON object.")
        return data

    def handle_request(self, method):
        try:
            self.dispatch(method)
        except (ValueError, json.JSONDecodeError) as error:
            self.send(400, {'error': str(error)})
        except sqlite3.IntegrityError:
            self.send(409, {'error': 'That course code already exists or the record is still in use.'})
        except Exception as error:
            print(f'Request failed: {type(error).__name__}: {error}')
            self.send(500, {'error': 'The request could not be completed. Check the server console.'})

    def dispatch(self, method):
        path = urlparse(self.path).path
        # Protect cookie-based writes from cross-site requests.
        if method != 'GET':
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': 'Cross-origin writes are not allowed.'})
            if self.headers.get('Sec-Fetch-Site', '').lower() == 'cross-site':
                return self.send(403, {'error': 'Cross-site requests are not allowed.'})
        if method == 'GET' and not path.startswith('/api/'):
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.send(404, {'error': 'Page not found.'})
            name, mime = files[path]
            return self.send(200, (ROOT / 'static' / name).read_bytes(), mime)
        with connect() as db:
            token = self.cookie_token()
            user = get_user(db, token)
            if path == '/api/auth/me' and method == 'GET':
                return self.send(200, {'authenticated': bool(user), 'user': user})
            if path == '/api/auth/register' and method == 'POST':
                if user:
                    return self.send(409, {'error': 'Log out before creating another account.'})
                key = 'register:' + self.client_address[0]
                if not register_limiter.allow(key, max_attempts=10, window=3600):
                    return self.send(429, {'error': 'Too many registration attempts. Please try again later.'})
                register_limiter.record(key)
                b = self.json_body()
                name, email, password = valid_name(b.get('name')), normal_email(b.get('email')), valid_password(b.get('password'))
                if db.execute('SELECT 1 FROM users WHERE email=?', (email,)).fetchone():
                    return self.send(409, {'error': 'An account already exists with this email.'})
                try:
                    result = db.execute('INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)',
                                        (name, email, hash_password(password), int(time.time())))
                except sqlite3.IntegrityError:
                    return self.send(409, {'error': 'An account already exists with this email.'})
                login_token = create_session(db, result.lastrowid)
                db.commit()
                return self.send(201, {'authenticated': True, 'user': {'id': result.lastrowid, 'name': name, 'email': email}},
                                 cookie=self.session_cookie(login_token))
            if path == '/api/auth/login' and method == 'POST':
                b = self.json_body()
                email = normal_email(b.get('email'))
                password = b.get('password')
                if not isinstance(password, str):
                    raise ValueError('Enter your password.')
                key = 'login:' + self.client_address[0] + ':' + email
                if not login_limiter.allow(key):
                    return self.send(429, {'error': 'Too many failed login attempts. Please try again later.'})
                row = db.execute('SELECT id,name,email,password_hash FROM users WHERE email=?', (email,)).fetchone()
                if not verify_password(password, row['password_hash'] if row else DUMMY_HASH) or not row:
                    login_limiter.record(key)
                    return self.send(401, {'error': 'Incorrect email or password.'})
                login_limiter.clear(key)
                revoke_session(db, token)
                login_token = create_session(db, row['id'])
                db.commit()
                return self.send(200, {'authenticated': True, 'user': {'id': row['id'], 'name': row['name'], 'email': row['email']}},
                                 cookie=self.session_cookie(login_token))
            if path == '/api/auth/logout' and method == 'POST':
                revoke_session(db, token)
                db.commit()
                return self.send(200, {'ok': True}, cookie=self.session_cookie('', clear=True))
            if not user:
                return self.send(401, {'error': 'Please sign in to access your workspace.'})
            uid = user['id']
            if path == '/api/state' and method == 'GET':
                return self.send(200, {
                    'courses': [dict(row) for row in db.execute('SELECT id,code,name,color FROM courses WHERE user_id=? ORDER BY code', (uid,))],
                    'tasks': [dict(row) for row in db.execute('SELECT id,title,course_id,kind,due_date,priority,notes,completed FROM tasks WHERE user_id=? ORDER BY due_date,id', (uid,))],
                    'lectures': [dict(row) for row in db.execute('SELECT id,filename,course_id,pages,summary FROM lectures WHERE user_id=? ORDER BY id DESC', (uid,))]
                })
            if path == '/api/study-plan' and method == 'GET':
                from urllib.parse import parse_qs
                query = parse_qs(urlparse(self.path).query)
                try:
                    days = int(query.get('days', ['14'])[0])
                    minutes = int(query.get('minutes', ['90'])[0])
                except ValueError:
                    raise ValueError('Study plan parameters must be numbers.')
                tasks = [dict(t) for t in db.execute('SELECT * FROM tasks WHERE user_id=? ORDER BY due_date,id', (uid,))]
                courses = [dict(c) for c in db.execute('SELECT * FROM courses WHERE user_id=?', (uid,))]
                return self.send(200, study_plan(tasks, courses, days, minutes))
            if path == '/api/export/tasks.csv' and method == 'GET':
                rows = db.execute('''SELECT t.title, c.code, t.kind, t.due_date, t.priority, t.notes, t.completed
                     FROM tasks t JOIN courses c ON t.course_id=c.id
                     WHERE t.user_id=? AND c.user_id=? ORDER BY t.due_date,t.id''', (uid, uid))
                csv_rows = [(r['title'], r['code'], r['kind'], r['due_date'], r['priority'],
                             'Completed' if r['completed'] else 'Pending', r['notes']) for r in rows]
                return self.send(200, to_csv(['Task', 'Course', 'Type', 'Due date', 'Priority', 'Status', 'Notes'], csv_rows), 'text/csv; charset=utf-8')
            if path == '/api/export/study-plan.csv' and method == 'GET':
                from urllib.parse import parse_qs
                query = parse_qs(urlparse(self.path).query)
                try:
                    days = int(query.get('days', ['14'])[0])
                    minutes = int(query.get('minutes', ['90'])[0])
                except ValueError:
                    raise ValueError('Study plan parameters must be numbers.')
                plan = study_plan([dict(t) for t in db.execute('SELECT * FROM tasks WHERE user_id=?', (uid,))],
                                  [dict(c) for c in db.execute('SELECT * FROM courses WHERE user_id=?', (uid,))], days, minutes)
                csv_rows = [(r['date'], r['course'], r['title'], r['kind'], r['priority'],
                             r['minutes'], r['due_date'], 'Yes' if r['overdue'] else 'No') for r in plan['sessions']]
                return self.send(200, to_csv(['Study date', 'Course', 'Task', 'Type', 'Priority', 'Minutes', 'Due date', 'Overdue'], csv_rows), 'text/csv; charset=utf-8')
            if path == '/api/courses' and method == 'POST':
                b = self.json_body()
                code, name = clean_string(b.get('code'), 'Course code', 20), clean_string(b.get('name'), 'Course name', 100)
                color = b.get('color', '#6663d5')
                if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                    raise ValueError('Select a valid course color.')
                result = db.execute('INSERT INTO courses(user_id,code,name,color) VALUES(?,?,?,?)', (uid, code.upper(), name, color))
                return self.send(201, {'id': result.lastrowid})
            if path == '/api/tasks' and method == 'POST':
                values = task_values(db, self.json_body(), uid)
                result = db.execute('INSERT INTO tasks(user_id,title,course_id,kind,due_date,priority,notes,completed) VALUES(?,?,?,?,?,?,?,?)', (uid, *values))
                return self.send(201, {'id': result.lastrowid})
            match = re.fullmatch(r'/api/tasks/(\d+)', path)
            if match and method in ('PUT', 'DELETE'):
                tid = int(match[1])
                if not db.execute('SELECT id FROM tasks WHERE id=? AND user_id=?', (tid, uid)).fetchone():
                    return self.send(404, {'error': 'Task not found.'})
                if method == 'DELETE':
                    db.execute('DELETE FROM tasks WHERE id=? AND user_id=?', (tid, uid))
                else:
                    values = task_values(db, self.json_body(), uid)
                    db.execute('UPDATE tasks SET title=?,course_id=?,kind=?,due_date=?,priority=?,notes=?,completed=? WHERE id=? AND user_id=?', (*values, tid, uid))
                return self.send(200, {'ok': True})
            if path == '/api/lectures' and method == 'POST':
                content_type = self.headers.get('Content-Type', '')
                if not content_type.startswith('multipart/form-data'):
                    raise ValueError('Upload a PDF using the file picker.')
                msg = BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+self.body())
                parts = {part.get_param('name', header='content-disposition'): part for part in msg.iter_parts()}
                if 'file' not in parts or 'course_id' not in parts:
                    raise ValueError('Select a course and PDF file.')
                try:
                    cid = int(parts['course_id'].get_payload(decode=True))
                except (ValueError, TypeError):
                    raise ValueError('Select a course.')
                course_exists(db, cid, uid)
                name = (parts['file'].get_filename() or '').replace('\\', '/').split('/')[-1]
                raw = parts['file'].get_payload(decode=True)
                if not name.lower().endswith('.pdf') or not raw or not raw.startswith(b'%PDF-'):
                    raise ValueError('Please upload a valid PDF file.')
                if len(raw) > MAX_UPLOAD:
                    raise ValueError('The maximum PDF size is 10 MB.')
                try:
                    reader = PdfReader(io.BytesIO(raw))
                    if reader.is_encrypted:
                        raise ValueError('Password-protected PDFs are not supported.')
                    if len(reader.pages) > 150:
                        raise ValueError('Please upload a PDF with at most 150 pages.')
                    pages = [{'page': i+1, 'text': page.extract_text() or ''} for i, page in enumerate(reader.pages)]
                except ValueError:
                    raise
                except Exception:
                    raise ValueError('This PDF could not be read. Try another text-based PDF.')
                if not any(p['text'].strip() for p in pages):
                    raise ValueError('No readable text was found. Scanned PDFs need OCR before uploading.')
                stored = uuid.uuid4().hex+'.pdf'
                (DATA / 'uploads' / stored).write_bytes(raw)
                try:
                    result = db.execute('INSERT INTO lectures(user_id,filename,stored_name,course_id,pages,extracted) VALUES(?,?,?,?,?,?)', (uid, name[:200], stored, cid, len(pages), json.dumps(pages)))
                except Exception:
                    (DATA / 'uploads' / stored).unlink(missing_ok=True)
                    raise
                return self.send(201, {'id': result.lastrowid})
            match = re.fullmatch(r'/api/lectures/(\d+)(/summary|/file|/ask|/questions)?', path)
            if match:
                lid, action = int(match[1]), match[2]
                lecture = db.execute('SELECT * FROM lectures WHERE id=? AND user_id=?', (lid, uid)).fetchone()
                if not lecture:
                    return self.send(404, {'error': 'Lecture not found.'})
                if method == 'POST' and action == '/summary':
                    summary = summarize(json.loads(lecture['extracted']))
                    db.execute('UPDATE lectures SET summary=? WHERE id=? AND user_id=?', (json.dumps(summary), lid, uid))
                    return self.send(200, {'summary': summary})
                if method == 'POST' and action == '/ask':
                    body = self.json_body()
                    question = clean_string(body.get('question'), 'Question', 500)
                    return self.send(200, answer_question(json.loads(lecture['extracted']), question))
                if method == 'GET' and action == '/questions':
                    return self.send(200, {'mode': 'extractive-practice',
                                           'questions': make_practice_questions(json.loads(lecture['extracted']))})
                if method == 'GET' and action == '/file':
                    return self.send(200, (DATA / 'uploads' / lecture['stored_name']).read_bytes(), 'application/pdf')
                if method == 'DELETE' and not action:
                    db.execute('DELETE FROM lectures WHERE id=? AND user_id=?', (lid, uid))
                    db.commit()
                    (DATA / 'uploads' / lecture['stored_name']).unlink(missing_ok=True)
                    return self.send(200, {'ok': True})
        self.send(404, {'error': 'Endpoint not found.'})


def main():
    parser = argparse.ArgumentParser(description='Run UniPlan locally')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', '8000')))
    parser.add_argument('--host', default=os.environ.get('UNIPLAN_HOST', '127.0.0.1'), help='Bind address; set 0.0.0.0 only behind a trusted HTTPS host')
    parser.add_argument('--open-browser', action='store_true', help='Open the application in your default browser')
    args = parser.parse_args()
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'UniPlan is listening on {args.host}:{args.port} — Ctrl+C to stop.', flush=True)
    if args.open_browser:
        import threading
        import webbrowser
        opener = threading.Timer(0.7, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}'))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nUniPlan stopped. Your data is saved.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
