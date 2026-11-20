"""Local, deterministic study tools. No model, remote service, or AI API is used."""
from collections import Counter
from datetime import date, timedelta
import csv
import io
import re

STOPWORDS = set('a an and are as at be been but by can could did do does each for from had has have how if in into is it its of on or our should so than that the their them there these they this those to was were what when where which who why will with would you your explain describe list give me about lecture slide slides material according please'.split())


def terms(text):
    words = re.findall(r'[^\W\d_]+', text.casefold(), flags=re.UNICODE)
    return {w[:-1] if w.endswith('s') and len(w) > 4 else w for w in words if len(w) >= 3 and w not in STOPWORDS}


def passages(pages):
    """Yield bounded, page-attributed passages from extracted lecture text."""
    for page in pages:
        raw = page.get('text', '')
        if not isinstance(raw, str):
            continue
        # Preserve punctuation and list items, normalize line wrapping.
        clean = re.sub(r'\s+', ' ', raw).strip()
        for sentence in re.split(r'(?<=[.!?])\s+|(?<=:)\s+(?=[A-Z])', clean):
            sentence = sentence.strip(' \t\r\n•-')
            if len(sentence) < 28:
                continue
            # Very long extracted paragraphs are split into readable chunks.
            if len(sentence) > 580:
                chunks = re.findall(r'.{1,480}(?:\s|$)', sentence) or [sentence[:480]]
            else:
                chunks = [sentence]
            for chunk in chunks:
                content = chunk.strip()
                if len(content) >= 28:
                    yield {'text': content[:580], 'page': page['page']}


def answer_question(pages, question):
    """Return top matching VERBATIM passages, not a generated response."""
    q = terms(question)
    if not q:
        raise ValueError('Ask a more specific question with a lecture topic or keyword.')
    scored = []
    seen = set()
    for passage in passages(pages):
        content = passage['text']
        key = content.casefold()
        if key in seen:
            continue
        seen.add(key)
        p = terms(content)
        hits = q & p
        if not hits:
            continue
        # Coverage of user query dominates, with a modest brevity preference.
        score = 5.0 * len(hits) / len(q) + 0.25 * len(hits) + 0.2 / (1 + len(p) / 20)
        scored.append((score, passage))
    scored.sort(key=lambda item: (-item[0], item[1]['page']))
    if not scored or (len(q) >= 3 and len(q & terms(scored[0][1]['text'])) < 2):
        return {'mode': 'source-retrieval', 'found': False, 'answer': 'No clearly relevant passage was found in this lecture. Try using a term that appears in the PDF.', 'sources': []}
    best_hits = len(q & terms(scored[0][1]['text']))
    minimum_hits = max(1, best_hits - 0) if len(q) > 1 else 1
    top = [p for _, p in scored if len(q & terms(p['text'])) >= minimum_hits][:3]
    return {'mode': 'source-retrieval', 'found': True, 'answer': 'These are the closest matching passages from your uploaded lecture. They are quoted from the PDF, not AI-generated explanations.', 'sources': top}


def make_practice_questions(pages, limit=7):
    """Extract definition or cloze questions with checkable source-page answers."""
    result, seen = [], set()
    for passage in passages(pages):
        text, page = passage['text'], passage['page']
        if len(text) > 330:
            continue
        match = re.match(r'^([A-Z][A-Za-z0-9\s()/\-]{3,65}?)\s+(?:is|are|means|refers to)\s+(.{15,230})[.!]?$', text)
        if match:
            subject = match.group(1).strip()
            explanation = match.group(2).strip().rstrip('.')
            if len(terms(subject)) > 6 or not terms(subject) or len(terms(explanation)) < 3:
                continue
            prompt = f'According to the lecture, what does “{subject}” mean?'
            answer = explanation
            kind = 'Definition'
        else:
            if not re.search(r'\b(is|are|means|can|will|should|must|includes|has|have|requires|uses|produces|creates|shows|provides|allows|describes|contains|helps|supports|occurs|happens|evaluates|represents|develops|consists|results|improves|inspects|checks)\b', text, flags=re.IGNORECASE):
                continue
            words = [(m.group(0), m.start(), m.end()) for m in re.finditer(r'[^\W\d_]{5,}', text, flags=re.UNICODE)]
            chosen = next(((w, start, end) for w, start, end in words if w.casefold() not in STOPWORDS), None)
            if not chosen:
                continue
            word, start, end = chosen
            prompt = 'Fill in the blank: ' + text[:start] + '_______' + text[end:]
            answer = word
            kind = 'Fill in the blank'
        key = prompt.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({'type': kind, 'question': prompt, 'answer': answer, 'page': page})
        if len(result) >= limit:
            break
    return result


def study_plan(tasks, courses, days=14, daily_minutes=90, today=None):
    """Greedy 30-minute study blocks, favoring urgent/high-priority work; no storage."""
    if days not in (7, 14, 21) or daily_minutes not in (60, 90, 120, 180):
        raise ValueError('Choose a supported plan length and daily study time.')
    today = today or date.today()
    finish = today + timedelta(days=days - 1)
    by_course = {c['id']: c['code'] for c in courses}
    remaining = {today + timedelta(days=i): daily_minutes for i in range(days)}
    sessions = []
    skipped = []
    priorities = {'High': 0, 'Medium': 1, 'Low': 2}
    pending = sorted((t for t in tasks if not t['completed']), key=lambda t: (t['due_date'], priorities.get(t['priority'], 3), t['id']))
    for task in pending:
        due = date.fromisoformat(task['due_date'])
        if due > finish:
            continue
        # Existing tasks are estimates, not measured durations.
        blocks = {'High': 4, 'Medium': 3, 'Low': 2}.get(task['priority'], 2)
        if task['kind'] == 'Exam':
            blocks += 2
        last_date = max(today, due)
        eligible = [d for d in remaining if d <= last_date]
        placed = 0
        for _ in range(blocks):
            candidates = [d for d in eligible if remaining[d] >= 30]
            if not candidates:
                break
            # Distribute across eligible days, while nudging closer to today.
            used = Counter(s['date'] for s in sessions if s['task_id'] == task['id'])
            chosen = min(candidates, key=lambda d: (used[d.isoformat()], -remaining[d], d))
            remaining[chosen] -= 30
            sessions.append({'date': chosen.isoformat(), 'task_id': task['id'], 'title': task['title'],
                             'course': by_course.get(task['course_id'], 'Course'), 'kind': task['kind'],
                             'priority': task['priority'], 'minutes': 30, 'due_date': task['due_date'],
                             'overdue': due < today})
            placed += 1
        if placed < blocks:
            skipped.append({'task_id': task['id'], 'title': task['title'], 'unscheduled_minutes': 30 * (blocks-placed)})
    sessions.sort(key=lambda s: (s['date'], s['due_date'], priorities[s['priority']], s['title']))
    return {'days': days, 'daily_minutes': daily_minutes, 'start_date': today.isoformat(), 'end_date': finish.isoformat(),
            'sessions': sessions, 'unscheduled': skipped, 'total_minutes': sum(s['minutes'] for s in sessions),
            'method': 'Suggested 30-minute blocks based on due dates, priority, and task type; not an AI prediction.'}


def safe_cell(value):
    text = str(value if value is not None else '')
    # Prevent spreadsheet formula execution for exports of untrusted user-entered text.
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


def to_csv(headers, rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_cell(value) for value in row])
    return ('\ufeff' + output.getvalue()).encode('utf-8')
