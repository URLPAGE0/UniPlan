"""UniPlan Sprint 2. Run: python app.py. Local, single-user application."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from email.parser import BytesParser
from email.policy import default
from datetime import date, timedelta
from collections import Counter
import argparse
import io
import json
import os
import re
import sqlite3
import uuid
from pypdf import PdfReader
from study_features import answer_question, make_practice_questions, study_plan, to_csv

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('UNIPLAN_DATA_DIR', ROOT / 'data'))
MAX_UPLOAD = 10 * 1024 * 1024


def connect():
    db = sqlite3.connect(DATA / 'uniplan.sqlite3')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'uploads').mkdir(exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS courses (
          id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name TEXT NOT NULL, color TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, course_id INTEGER NOT NULL REFERENCES courses(id),
          kind TEXT NOT NULL, due_date TEXT NOT NULL, priority TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '', completed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS lectures (
          id INTEGER PRIMARY KEY, filename TEXT NOT NULL, stored_name TEXT NOT NULL,
          course_id INTEGER NOT NULL REFERENCES courses(id), pages INTEGER NOT NULL,
          extracted TEXT NOT NULL, summary TEXT);
        ''')


def clean_string(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f'{name} is required and must be at most {limit} characters.')
    return value.strip()


def course_exists(db, value):
    if isinstance(value, bool) or not isinstance(value, int) or not db.execute('SELECT id FROM courses WHERE id=?', (value,)).fetchone():
        raise ValueError('Choose an existing course.')
    return value


def task_values(db, body):
    title = clean_string(body.get('title'), 'Title', 150)
    course = course_exists(db, body.get('course_id'))
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
    def send(self, code, payload, content_type='application/json; charset=utf-8'):
        data = json.dumps(payload).encode() if content_type.startswith('application/json') else payload
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        if content_type.startswith('text/csv'):
            filename = 'uniplan-study-plan.csv' if 'study-plan' in self.path else 'uniplan-tasks.csv'
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

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
        # Browser writes must come from this local application.
        if method != 'GET':
            origin = self.headers.get('Origin')
            if origin and urlparse(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': 'Cross-origin writes are not allowed.'})
        if method == 'GET' and not path.startswith('/api/'):
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.send(404, {'error': 'Page not found.'})
            name, mime = files[path]
            return self.send(200, (ROOT / 'static' / name).read_bytes(), mime)
        with connect() as db:
            if path == '/api/state' and method == 'GET':
                return self.send(200, {table: [dict(row) for row in db.execute(sql)] for table, sql in {
                    'courses': 'SELECT * FROM courses ORDER BY code',
                    'tasks': 'SELECT * FROM tasks ORDER BY due_date, id',
                    'lectures': 'SELECT id,filename,course_id,pages,summary FROM lectures ORDER BY id DESC'}.items()})
            if path == '/api/study-plan' and method == 'GET':
                from urllib.parse import parse_qs
                query = parse_qs(urlparse(self.path).query)
                try:
                    days = int(query.get('days', ['14'])[0])
                    minutes = int(query.get('minutes', ['90'])[0])
                except ValueError:
                    raise ValueError('Study plan parameters must be numbers.')
                tasks = [dict(t) for t in db.execute('SELECT * FROM tasks ORDER BY due_date,id')]
                courses = [dict(c) for c in db.execute('SELECT * FROM courses')]
                return self.send(200, study_plan(tasks, courses, days, minutes))
            if path == '/api/export/tasks.csv' and method == 'GET':
                rows = db.execute('''SELECT t.title, c.code, t.kind, t.due_date, t.priority, t.notes, t.completed
                     FROM tasks t JOIN courses c ON t.course_id=c.id ORDER BY t.due_date,t.id''')
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
                plan = study_plan([dict(t) for t in db.execute('SELECT * FROM tasks')],
                                  [dict(c) for c in db.execute('SELECT * FROM courses')], days, minutes)
                csv_rows = [(r['date'], r['course'], r['title'], r['kind'], r['priority'],
                             r['minutes'], r['due_date'], 'Yes' if r['overdue'] else 'No') for r in plan['sessions']]
                return self.send(200, to_csv(['Study date', 'Course', 'Task', 'Type', 'Priority', 'Minutes', 'Due date', 'Overdue'], csv_rows), 'text/csv; charset=utf-8')
            if path == '/api/demo' and method == 'POST':
                if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in ('courses', 'tasks', 'lectures')):
                    return self.send(409, {'error': 'Demo data can only be loaded into an empty workspace.'})
                for code, name, color in [('SW332', 'Agile Software Construction', '#6663d5'), ('CS240', 'Database Systems', '#2a927d'), ('CS310', 'Software Engineering', '#ce8944')]:
                    db.execute('INSERT INTO courses(code,name,color) VALUES(?,?,?)', (code, name, color))
                for title, course, kind, days, priority, done in [('Sprint review preparation', 1, 'Assignment', 2, 'High', 0), ('Database design exercise', 2, 'Assignment', 4, 'Medium', 0), ('Midterm examination', 3, 'Exam', 7, 'High', 0), ('Product vision statement', 1, 'Assignment', -2, 'Low', 1)]:
                    db.execute('INSERT INTO tasks(title,course_id,kind,due_date,priority,completed) VALUES(?,?,?,?,?,?)', (title, course, kind, (date.today()+timedelta(days=days)).isoformat(), priority, done))
                return self.send(201, {'ok': True})
            if path == '/api/courses' and method == 'POST':
                b = self.json_body()
                code, name = clean_string(b.get('code'), 'Course code', 20), clean_string(b.get('name'), 'Course name', 100)
                color = b.get('color', '#6663d5')
                if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                    raise ValueError('Select a valid course color.')
                result = db.execute('INSERT INTO courses(code,name,color) VALUES(?,?,?)', (code.upper(), name, color))
                return self.send(201, {'id': result.lastrowid})
            if path == '/api/tasks' and method == 'POST':
                values = task_values(db, self.json_body())
                result = db.execute('INSERT INTO tasks(title,course_id,kind,due_date,priority,notes,completed) VALUES(?,?,?,?,?,?,?)', values)
                return self.send(201, {'id': result.lastrowid})
            match = re.fullmatch(r'/api/tasks/(\d+)', path)
            if match and method in ('PUT', 'DELETE'):
                tid = int(match[1])
                if not db.execute('SELECT id FROM tasks WHERE id=?', (tid,)).fetchone():
                    return self.send(404, {'error': 'Task not found.'})
                if method == 'DELETE':
                    db.execute('DELETE FROM tasks WHERE id=?', (tid,))
                else:
                    values = task_values(db, self.json_body())
                    db.execute('UPDATE tasks SET title=?,course_id=?,kind=?,due_date=?,priority=?,notes=?,completed=? WHERE id=?', (*values, tid))
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
                course_exists(db, cid)
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
                    result = db.execute('INSERT INTO lectures(filename,stored_name,course_id,pages,extracted) VALUES(?,?,?,?,?)', (name[:200], stored, cid, len(pages), json.dumps(pages)))
                except Exception:
                    (DATA / 'uploads' / stored).unlink(missing_ok=True)
                    raise
                return self.send(201, {'id': result.lastrowid})
            match = re.fullmatch(r'/api/lectures/(\d+)(/summary|/file|/ask|/questions)?', path)
            if match:
                lid, action = int(match[1]), match[2]
                lecture = db.execute('SELECT * FROM lectures WHERE id=?', (lid,)).fetchone()
                if not lecture:
                    return self.send(404, {'error': 'Lecture not found.'})
                if method == 'POST' and action == '/summary':
                    summary = summarize(json.loads(lecture['extracted']))
                    db.execute('UPDATE lectures SET summary=? WHERE id=?', (json.dumps(summary), lid))
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
                    db.execute('DELETE FROM lectures WHERE id=?', (lid,))
                    db.commit()
                    (DATA / 'uploads' / lecture['stored_name']).unlink(missing_ok=True)
                    return self.send(200, {'ok': True})
        self.send(404, {'error': 'Endpoint not found.'})


def main():
    parser = argparse.ArgumentParser(description='Run UniPlan locally')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--open-browser', action='store_true', help='Open the application in your default browser')
    args = parser.parse_args()
    init_db()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'UniPlan is running at http://127.0.0.1:{args.port} — Ctrl+C to stop.', flush=True)
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
