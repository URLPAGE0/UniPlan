"""Final-release Sprint 2 features: PBI-16 to PBI-21, all account scoped.

No paid APIs or external background notification services are used.
"""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse
from collections import Counter
import json
import re
import sqlite3
import time
from study_features import make_practice_questions, study_plan


def setup_extensions(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS lecture_notes (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
      title TEXT NOT NULL, body TEXT NOT NULL, page INTEGER NOT NULL, created_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_notes_user ON lecture_notes(user_id,lecture_id);
    CREATE TABLE IF NOT EXISTS bookmarks (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
      page INTEGER NOT NULL, label TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(user_id,lecture_id,page));
    CREATE TABLE IF NOT EXISTS quiz_attempts (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
      score INTEGER NOT NULL, total INTEGER NOT NULL, review TEXT NOT NULL,
      created_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_quiz_user ON quiz_attempts(user_id,lecture_id);
    CREATE TABLE IF NOT EXISTS study_session_edits (
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      block_number INTEGER NOT NULL, study_date TEXT NOT NULL, start_time TEXT NOT NULL,
      UNIQUE(user_id,task_id,block_number));
    ''')


def required_text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f'{label} is required (up to {maximum} characters).')
    return value.strip()


def good_date(raw):
    if not isinstance(raw, str):
        raise ValueError('Choose a valid study date.')
    try:
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError()
        if parsed < date.today()-timedelta(days=7) or parsed > date.today()+timedelta(days=366):
            raise ValueError('Study date must be within the allowed one-year range.')
        return raw
    except (TypeError, ValueError) as e:
        if str(e).startswith('Study date must'):
            raise
        raise ValueError('Choose a valid study date.') from e


def good_page(raw, pages):
    if type(raw) is not int or not 1 <= raw <= pages:
        raise ValueError(f'Page must be between 1 and {pages}.')
    return raw


def owned_lecture(db, user_id, lecture_id):
    lecture = db.execute('SELECT * FROM lectures WHERE id=? AND user_id=?', (lecture_id, user_id)).fetchone()
    return lecture


def prepare_plan(db, uid, days, minutes):
    tasks = [dict(t) for t in db.execute('SELECT * FROM tasks WHERE user_id=? ORDER BY due_date,id', (uid,))]
    courses = [dict(c) for c in db.execute('SELECT * FROM courses WHERE user_id=?', (uid,))]
    plan = study_plan(tasks, courses, days, minutes)
    overrides = {(r['task_id'],r['block_number']):r for r in db.execute(
        'SELECT task_id,block_number,study_date,start_time FROM study_session_edits WHERE user_id=?', (uid,))}
    counts = Counter()
    by_day = Counter()
    for s in plan['sessions']:
        number = counts[s['task_id']]
        counts[s['task_id']] += 1
        s['block_number'] = number
        s['customized'] = False
        override = overrides.get((s['task_id'],number))
        if override:
            s['date'] = override['study_date']
            s['start_time'] = override['start_time']
            s['customized'] = True
        else:
            day_offset = by_day[s['date']]
            s['start_time'] = f'{16+day_offset//2:02d}:{30 if day_offset%2 else 0:02d}' if day_offset < 14 else '20:00'
        by_day[s['date']] += 1
    plan['sessions'].sort(key=lambda s:(s['date'], s['start_time'], s['title'], s['block_number']))
    plan['method'] += ' You can edit and save individual blocks.'
    return plan


def normalize_answer(value):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', value.casefold(), flags=re.UNICODE)).strip()


def grade_answers(questions, submitted):
    if not isinstance(submitted, list) or len(submitted) != len(questions):
        raise ValueError('Answer each question before submitting the quiz.')
    review = []
    score = 0
    for question, raw in zip(questions, submitted):
        if not isinstance(raw, str) or len(raw) > 2000:
            raise ValueError('Quiz answers must be text of at most 2000 characters.')
        actual = normalize_answer(raw)
        expected = normalize_answer(question['answer'])
        # Objective, literal checking rather than pretending to understand semantic paraphrases.
        is_correct = bool(actual) and actual == expected
        score += int(is_correct)
        review.append({'question':question['question'], 'answer':question['answer'],
                       'submitted':raw.strip(), 'correct':is_correct, 'page':question['page'],
                       'type':question['type']})
    return score, review


def handle_extensions(h, db, uid, method, path):
    """Return True for handled extension endpoints; caller owns commit on normal exit."""
    if path == '/api/reminders' and method == 'GET':
        now = date.today()
        end = (now + timedelta(days=3)).isoformat()
        reminders = [dict(r) for r in db.execute('''
          SELECT t.id,t.title,t.kind,t.due_date,t.priority,c.code AS course
          FROM tasks t JOIN courses c ON c.id=t.course_id AND c.user_id=t.user_id
          WHERE t.user_id=? AND t.completed=0 AND t.due_date<=?
          ORDER BY t.due_date,t.id LIMIT 20''', (uid,end))]
        for r in reminders:
            remaining = (date.fromisoformat(r['due_date'])-now).days
            r['days_left'] = remaining
            r['message'] = ('Overdue by '+str(-remaining)+' day(s)' if remaining<0 else
                            'Due today' if remaining==0 else f'Due in {remaining} day(s)')
        h.send(200, {'reminders':reminders, 'mode':'in-app', 'note':'Reminders appear when UniPlan is open; no email or push alerts.'})
        return True
    if path == '/api/lectures/search' and method == 'GET':
        term = parse_qs(urlparse(h.path).query).get('q',[''])[0].strip()
        if len(term) > 100:
            raise ValueError('Search term must be at most 100 characters.')
        result = []
        if term:
            for l in db.execute('''SELECT l.id,l.filename,l.course_id,l.pages,l.extracted,c.code
                        FROM lectures l JOIN courses c ON c.id=l.course_id AND c.user_id=l.user_id
                        WHERE l.user_id=? ORDER BY l.id DESC''', (uid,)):
                pages = json.loads(l['extracted'])
                matched_pages = [p['page'] for p in pages if term.casefold() in p['text'].casefold()]
                if term.casefold() in l['filename'].casefold() or term.casefold() in l['code'].casefold() or matched_pages:
                    result.append({'id':l['id'],'filename':l['filename'],'course_id':l['course_id'],
                                   'course':l['code'],'pages':l['pages'],'matching_pages':matched_pages[:5]})
        h.send(200, {'results':result[:50], 'query':term})
        return True
    if path == '/api/saved' and method == 'GET':
        notes = [dict(r) for r in db.execute('''SELECT n.id,n.lecture_id,n.title,n.body,n.page,n.created_at,l.filename
                      FROM lecture_notes n JOIN lectures l ON l.id=n.lecture_id AND l.user_id=n.user_id
                      WHERE n.user_id=? ORDER BY n.created_at DESC,n.id DESC''', (uid,))]
        bookmarks = [dict(r) for r in db.execute('''SELECT b.id,b.lecture_id,b.label,b.page,b.created_at,l.filename
                      FROM bookmarks b JOIN lectures l ON l.id=b.lecture_id AND l.user_id=b.user_id
                      WHERE b.user_id=? ORDER BY b.created_at DESC,b.id DESC''', (uid,))]
        h.send(200, {'notes':notes, 'bookmarks':bookmarks})
        return True
    if path == '/api/progress' and method == 'GET':
        recent = [dict(r) for r in db.execute('''SELECT a.id,a.lecture_id,a.score,a.total,a.created_at,a.review,l.filename
                   FROM quiz_attempts a JOIN lectures l ON l.id=a.lecture_id AND l.user_id=a.user_id
                   WHERE a.user_id=? ORDER BY a.created_at DESC,a.id DESC LIMIT 30''', (uid,))]
        missed=[]
        for attempt in recent:
            for q in json.loads(attempt['review']):
                if not q['correct']:
                    missed.append({'lecture':attempt['filename'], 'lecture_id':attempt['lecture_id'],
                                   'question':q['question'],'page':q['page']})
        for attempt in recent:
            del attempt['review']
        h.send(200, {'attempts':recent, 'needs_review':missed[:12],
                     'completed_tasks':db.execute('SELECT count(*) FROM tasks WHERE user_id=? AND completed=1',(uid,)).fetchone()[0],
                     'total_tasks':db.execute('SELECT count(*) FROM tasks WHERE user_id=?',(uid,)).fetchone()[0]})
        return True
    session_match = re.fullmatch(r'/api/study-sessions/(\d+)/(\d+)', path)
    if session_match and method in ('PUT','DELETE'):
        task_id, block = int(session_match[1]),int(session_match[2])
        if not db.execute('SELECT 1 FROM tasks WHERE id=? AND user_id=?', (task_id,uid)).fetchone():
            h.send(404,{'error':'Study task not found.'});return True
        if not 0 <= block <= 15:
            raise ValueError('Invalid study block.')
        if method == 'DELETE':
            db.execute('DELETE FROM study_session_edits WHERE user_id=? AND task_id=? AND block_number=?',(uid,task_id,block))
        else:
            body=h.json_body()
            study_date=good_date(body.get('date'))
            hour=body.get('time')
            if not isinstance(hour,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',hour):
                raise ValueError('Enter time in HH:MM format.')
            db.execute('''INSERT INTO study_session_edits(user_id,task_id,block_number,study_date,start_time)
                    VALUES(?,?,?,?,?) ON CONFLICT(user_id,task_id,block_number)
                    DO UPDATE SET study_date=excluded.study_date,start_time=excluded.start_time''', (uid,task_id,block,study_date,hour))
        db.commit()
        h.send(200,{'ok':True})
        return True
    note_action = re.fullmatch(r'/api/notes/(\d+)', path)
    if note_action and method in ('PUT','DELETE'):
        note_id = int(note_action[1])
        row=db.execute('''SELECT n.id,l.pages FROM lecture_notes n JOIN lectures l
             ON l.id=n.lecture_id AND l.user_id=n.user_id WHERE n.id=? AND n.user_id=?''', (note_id,uid)).fetchone()
        if not row:
            h.send(404,{'error':'Note not found.'});return True
        if method == 'DELETE':
            db.execute('DELETE FROM lecture_notes WHERE id=? AND user_id=?',(note_id,uid))
        else:
            b=h.json_body()
            db.execute('UPDATE lecture_notes SET title=?,body=?,page=? WHERE id=? AND user_id=?',
                       (required_text(b.get('title'),'Note title',120),required_text(b.get('body'),'Note',3000),
                        good_page(b.get('page'),row['pages']),note_id,uid))
        db.commit()
        h.send(200,{'ok':True});return True
    bookmark_action = re.fullmatch(r'/api/bookmarks/(\d+)', path)
    if bookmark_action and method == 'DELETE':
        deleted=db.execute('DELETE FROM bookmarks WHERE id=? AND user_id=?',(int(bookmark_action[1]),uid)).rowcount
        db.commit()
        h.send(200,{'ok':True}) if deleted else h.send(404,{'error':'Bookmark not found.'})
        return True
    match = re.fullmatch(r'/api/lectures/(\d+)/(notes|bookmarks|quiz-attempts)',path)
    if not match or method not in ('GET','POST'):
        return False
    lecture_id=int(match[1]); resource=match[2]
    lecture=owned_lecture(db,uid,lecture_id)
    if not lecture:
        h.send(404,{'error':'Lecture not found.'});return True
    if resource == 'notes':
        if method == 'GET':
            h.send(200,{'notes':[dict(r) for r in db.execute('SELECT * FROM lecture_notes WHERE lecture_id=? AND user_id=? ORDER BY id DESC',(lecture_id,uid))]})
        else:
            b=h.json_body()
            result=db.execute('INSERT INTO lecture_notes(user_id,lecture_id,title,body,page,created_at) VALUES(?,?,?,?,?,?)',
                              (uid,lecture_id,required_text(b.get('title'),'Note title',120),
                               required_text(b.get('body'),'Note',3000),good_page(b.get('page'),lecture['pages']),int(time.time())))
            db.commit();h.send(201,{'id':result.lastrowid})
        return True
    if resource == 'bookmarks':
        if method == 'GET':
            h.send(200,{'bookmarks':[dict(r) for r in db.execute('SELECT * FROM bookmarks WHERE lecture_id=? AND user_id=? ORDER BY page',(lecture_id,uid))]})
        else:
            b=h.json_body()
            page=good_page(b.get('page'),lecture['pages'])
            label=b.get('label') or f'Page {page}'
            result=db.execute('INSERT OR IGNORE INTO bookmarks(user_id,lecture_id,page,label,created_at) VALUES(?,?,?,?,?)',
                              (uid,lecture_id,page,required_text(label,'Bookmark label',120),int(time.time())))
            db.commit();h.send(201,{'id':result.lastrowid,'saved':bool(result.rowcount)})
        return True
    if resource == 'quiz-attempts':
        if method == 'GET':
            rows=db.execute('SELECT id,score,total,review,created_at FROM quiz_attempts WHERE user_id=? AND lecture_id=? ORDER BY id DESC LIMIT 20',(uid,lecture_id))
            h.send(200,{'attempts':[dict(r) | {'review':json.loads(r['review'])} for r in rows]})
        else:
            b=h.json_body()
            questions=make_practice_questions(json.loads(lecture['extracted']))
            if not questions:
                raise ValueError('This lecture does not contain enough questions to score.')
            score,review=grade_answers(questions,b.get('answers'))
            result=db.execute('INSERT INTO quiz_attempts(user_id,lecture_id,score,total,review,created_at) VALUES(?,?,?,?,?,?)',
                              (uid,lecture_id,score,len(questions),json.dumps(review),int(time.time())))
            db.commit()
            h.send(201,{'id':result.lastrowid,'score':score,'total':len(questions),'review':review})
        return True
    return False
