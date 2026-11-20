"""Self-contained integration tests: python -m unittest discover -s tests -v"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class UniPlanHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uniplan-test-')
        cls.port = free_port()
        env = dict(os.environ, UNIPLAN_DATA_DIR=cls.tmp.name)
        cls.proc = subprocess.Popen([sys.executable, 'app.py', '--port', str(cls.port)],
                                    cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.base = f'http://127.0.0.1:{cls.port}'
        for _ in range(60):
            try:
                with urlopen(cls.base + '/api/state', timeout=1):
                    break
            except (URLError, OSError):
                time.sleep(.1)
        else:
            raise RuntimeError('Test server did not start.')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.tmp.cleanup()

    def request(self, path, method='GET', data=None, content_type='application/json'):
        headers = {}
        if data is not None:
            if content_type == 'application/json':
                data = json.dumps(data).encode()
            headers['Content-Type'] = content_type
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=8) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
        except HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if error.headers.get_content_type() == 'application/json' else raw

    def test_01_initial_workspace_and_static_pages(self):
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state['courses'], [])
        for path in ('/', '/app.js', '/style.css'):
            status, contents = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(contents)

    def test_02_create_course_and_tasks_and_persistence(self):
        code, obj = self.request('/api/courses', 'POST', {'code': 'SW332', 'name': 'Agile Software Construction', 'color': '#6663d5'})
        self.assertEqual(code, 201)
        self.__class__.course_id = obj['id']
        tasks = [('Finish Sprint 2', 'High', 2), ('Review lecture', 'Medium', 5), ('Old assignment', 'Low', -1)]
        self.__class__.task_ids = []
        for title, priority, offset in tasks:
            data = {'title': title, 'course_id': self.course_id, 'kind': 'Assignment', 'due_date': (date.today() + timedelta(days=offset)).isoformat(), 'priority': priority, 'notes': 'Study for exam', 'completed': False}
            code, item = self.request('/api/tasks', 'POST', data)
            self.assertEqual(code, 201)
            self.task_ids.append(item['id'])
        status, state = self.request('/api/state')
        self.assertEqual(len(state['tasks']), 3)
        self.assertEqual(state['courses'][0]['code'], 'SW332')

    def test_03_study_plan_uses_dates_and_exports(self):
        code, result = self.request('/api/study-plan?days=7&minutes=60')
        self.assertEqual(code, 200)
        self.assertGreater(len(result['sessions']), 0)
        self.assertTrue(all(s['minutes'] == 30 for s in result['sessions']))
        self.assertTrue(all(s['date'] <= max(s['due_date'], date.today().isoformat()) for s in result['sessions']))
        code, csv_data = self.request('/api/export/study-plan.csv?days=7&minutes=60')
        self.assertEqual(code, 200)
        self.assertIn(b'Study date', csv_data)
        self.assertIn(b'Finish Sprint 2', csv_data)
        code, csv_data = self.request('/api/export/tasks.csv')
        self.assertEqual(code, 200)
        self.assertIn(b'Due date', csv_data)
        code, error = self.request('/api/study-plan?days=99&minutes=60')
        self.assertEqual(code, 400)

    def test_04_pdf_upload_summary_qa_practice_source(self):
        document = (ROOT / 'sample_materials' / 'Scrum_Practice_Lecture.pdf').read_bytes()
        boundary = 'UNIPLAN_TEST_BOUNDARY_2026'
        cid = str(self.course_id)
        payload = (
            b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="course_id"\r\n\r\n' + cid.encode() + b'\r\n'
            + b'--' + boundary.encode() + b'\r\nContent-Disposition: form-data; name="file"; filename="Scrum_Practice_Lecture.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            + document + b'\r\n--' + boundary.encode() + b'--\r\n'
        )
        status, item = self.request('/api/lectures', 'POST', payload, 'multipart/form-data; boundary='+boundary)
        self.assertEqual(status, 201)
        self.__class__.lecture_id = item['id']
        code, result = self.request(f'/api/lectures/{self.lecture_id}/summary', 'POST', {})
        self.assertEqual(code, 200)
        self.assertGreater(len(result['summary']), 1)
        code, result = self.request(f'/api/lectures/{self.lecture_id}/ask', 'POST', {'question':'What is the Sprint Review?'})
        self.assertEqual(code, 200)
        self.assertTrue(result['found'])
        self.assertEqual(result['sources'][0]['page'], 2)
        self.assertIn('stakeholders inspect', result['sources'][0]['text'])
        code, result = self.request(f'/api/lectures/{self.lecture_id}/ask', 'POST', {'question':'What is photosynthesis in sea turtles?'})
        self.assertEqual(code, 200)
        self.assertFalse(result['found'])
        code, result = self.request(f'/api/lectures/{self.lecture_id}/questions')
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(result['questions']), 4)
        self.assertTrue(all(x['page'] >= 1 and x['answer'] for x in result['questions']))
        code, filedata = self.request(f'/api/lectures/{self.lecture_id}/file')
        self.assertEqual(code, 200)
        self.assertEqual(filedata[:5], b'%PDF-')

    def test_05_legacy_task_edit_completion_and_delete(self):
        status, state = self.request('/api/state')
        self.assertEqual(len(state['lectures']), 1)
        original = state['tasks'][0]
        payload = {**original, 'title': 'Finish sprint review', 'completed': True}
        status, _ = self.request('/api/tasks/'+str(original['id']), 'PUT', payload)
        self.assertEqual(status, 200)
        status, state = self.request('/api/state')
        self.assertTrue(next(t for t in state['tasks'] if t['id']==original['id'])['completed'])
        status, _ = self.request('/api/tasks/'+str(original['id']), 'DELETE')
        self.assertEqual(status, 200)
        status, _ = self.request('/api/lectures/'+str(self.lecture_id), 'DELETE')
        self.assertEqual(status, 200)
        status, state = self.request('/api/state')
        self.assertEqual(len(state['lectures']), 0)

    def test_06_invalid_pdf_and_input(self):
        status, body = self.request('/api/lectures', 'POST', b'not an upload', 'text/plain')
        self.assertEqual(status, 400)
        status, body = self.request('/api/courses', 'POST', {'code':'SW332','name':'Duplicate','color':'#6663d5'})
        self.assertEqual(status, 409)
        status, body = self.request('/api/tasks', 'POST', {'title': '', 'course_id': self.course_id})
        self.assertEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
