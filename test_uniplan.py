"""HTTP integration regression suite: python -m unittest discover -s tests -v

Creates throwaway accounts and temporary test data; no demo account is shipped.
"""
from datetime import date, timedelta
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPCookieProcessor, build_opener, urlopen

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class UniPlanHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='uniplan-auth-tests-')
        cls.port = free_port()
        env = dict(os.environ, UNIPLAN_DATA_DIR=cls.tmp.name)
        cls.proc = subprocess.Popen([sys.executable, 'app.py', '--port', str(cls.port)],
                                    cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.alice, cls.bob, cls.anonymous = [build_opener(HTTPCookieProcessor(CookieJar())) for _ in range(3)]
        for _ in range(100):
            try:
                with urlopen(cls.base + '/api/auth/me', timeout=1):
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

    def request(self, path, method='GET', data=None, content_type='application/json', user='alice', headers=None):
        req_headers = headers.copy() if headers else {}
        if data is not None:
            if content_type == 'application/json':
                data = json.dumps(data).encode()
            req_headers['Content-Type'] = content_type
        req = Request(self.base + path, data=data, headers=req_headers, method=method)
        opener = {'alice':self.alice,'bob':self.bob,'anonymous':self.anonymous}[user]
        try:
            with opener.open(req, timeout=10) as response:
                raw = response.read()
                payload = json.loads(raw) if response.headers.get_content_type() == 'application/json' else raw
                return response.status, payload
        except HTTPError as error:
            raw = error.read()
            payload = json.loads(raw) if error.headers.get_content_type() == 'application/json' else raw
            return error.code, payload

    def test_01_empty_start_and_protected_routes(self):
        status, payload = self.request('/api/auth/me', user='anonymous')
        self.assertEqual((status, payload['authenticated']), (200, False))
        for path in ('/api/state', '/api/study-plan', '/api/export/tasks.csv', '/api/export/study-plan.csv', '/api/lectures/1/file'):
            status, _ = self.request(path, user='anonymous')
            self.assertEqual(status, 401, path)
        for path in ('/', '/app.js', '/style.css'):
            status, page = self.request(path, user='anonymous')
            self.assertEqual(status, 200)
            self.assertTrue(page)
        status, _ = self.request('/api/demo', 'POST', {}, user='anonymous')
        self.assertEqual(status, 401)

    def test_02_registration_empty_workspace_and_password_hash(self):
        status, err = self.request('/api/auth/register', 'POST', {'name':'A', 'email':'x', 'password':'short'}, user='alice')
        self.assertEqual(status, 400)
        for user, name, email in (('alice','Student Alice','ALICE@example.com'), ('bob','Student Bob','bob@example.com')):
            status, payload = self.request('/api/auth/register', 'POST',
                                           {'name':name, 'email':email, 'password':'A very strong student password!'}, user=user)
            self.assertEqual(status, 201, payload)
            self.assertEqual(payload['user']['name'], name)
            status, state = self.request('/api/state', user=user)
            self.assertEqual(status, 200)
            self.assertEqual(state, {'courses':[], 'tasks':[], 'lectures':[]})
        status, err = self.request('/api/auth/register', 'POST',
                                   {'name':'Duplicate','email':'alice@example.com','password':'A very strong student password!'},
                                   user='anonymous')
        self.assertEqual(status, 409)
        with sqlite3.connect(Path(self.tmp.name)/'uniplan_accounts.sqlite3') as db:
            rows = db.execute('SELECT email,password_hash FROM users').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(h.startswith('scrypt$') and 'very strong' not in h.lower() for _,h in rows))

    def test_03_user_isolation_and_task_crud(self):
        status, course = self.request('/api/courses', 'POST', {'code':'SW332','name':'Agile Methods','color':'#6663d5'})
        self.assertEqual(status, 201)
        self.__class__.alice_course = course['id']
        status, course = self.request('/api/courses', 'POST', {'code':'SW332','name':'A different course','color':'#aaaaaa'},user='bob')
        self.assertEqual(status, 201)
        self.__class__.bob_course = course['id']
        self.assertNotEqual(self.alice_course, self.bob_course)
        data={'title':'Alice private assignment', 'course_id':self.alice_course, 'kind':'Assignment',
              'due_date':(date.today()+timedelta(days=3)).isoformat(), 'priority':'High',
              'notes':'Private notes about class', 'completed':False}
        status, task = self.request('/api/tasks', 'POST', data)
        self.assertEqual(status, 201)
        self.__class__.alice_task=task['id']
        status, err = self.request('/api/tasks', 'POST', data, user='bob')
        self.assertEqual(status, 400, 'Cross-account course ID should be rejected')
        status, err = self.request('/api/tasks/'+str(self.alice_task), 'PUT', {**data,'title':'Tampered'}, user='bob')
        self.assertEqual(status, 404)
        status, err = self.request('/api/tasks/'+str(self.alice_task), 'DELETE', user='bob')
        self.assertEqual(status, 404)
        status, state = self.request('/api/state', user='bob')
        self.assertEqual(len(state['tasks']),0)
        self.assertEqual(state['courses'][0]['name'], 'A different course')
        status, state = self.request('/api/state')
        self.assertEqual(state['tasks'][0]['title'],'Alice private assignment')
        self.assertEqual(state['courses'][0]['name'],'Agile Methods')
        status, err = self.request('/api/courses','POST',{'code':'SW332','name':'Duplicate','color':'#6663d5'})
        self.assertEqual(status,409)

    def test_04_study_plan_and_export_isolation(self):
        status, result = self.request('/api/study-plan?days=14&minutes=60')
        self.assertEqual(status, 200)
        self.assertGreater(len(result['sessions']), 0)
        status, data = self.request('/api/export/tasks.csv')
        self.assertIn(b'Alice private assignment', data)
        status, data = self.request('/api/export/tasks.csv', user='bob')
        self.assertNotIn(b'Alice private assignment', data)
        status, plan = self.request('/api/study-plan?days=14&minutes=60', user='bob')
        self.assertEqual(plan['sessions'], [])
        status, export = self.request('/api/export/study-plan.csv?days=7&minutes=60')
        self.assertIn(b'Study date', export)
        status, err = self.request('/api/study-plan?days=99&minutes=60')
        self.assertEqual(status, 400)

    def test_05_pdf_upload_and_foreign_id_denials(self):
        document = (ROOT / 'sample_materials' / 'Scrum_Practice_Lecture.pdf').read_bytes()
        boundary = 'UNIPLAN_AUTH_TEST_BOUNDARY'
        self.__class__.multipart = (
            b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="course_id"\r\n\r\n'+str(self.alice_course).encode()+b'\r\n'
            +b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="file"; filename="Scrum_Practice_Lecture.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            +document+b'\r\n--'+boundary.encode()+b'--\r\n'
        )
        status, result = self.request('/api/lectures', 'POST', self.multipart,
                                      content_type='multipart/form-data; boundary='+boundary)
        self.assertEqual(status, 201)
        self.__class__.alice_lecture=result['id']
        status, result = self.request('/api/lectures', 'POST', self.multipart,
                                      content_type='multipart/form-data; boundary='+boundary,user='bob')
        self.assertEqual(status,400)
        for method,suffix,data in [('POST','/summary',{}),('POST','/ask',{'question':'What is a sprint review?'}),('GET','/questions',None),('GET','/file',None),('DELETE','',None)]:
            status, err = self.request(f'/api/lectures/{self.alice_lecture}{suffix}', method, data, user='bob')
            self.assertEqual(status,404,(method,suffix,err))
        status, result = self.request(f'/api/lectures/{self.alice_lecture}/summary', 'POST', {})
        self.assertEqual(status,200)
        self.assertGreater(len(result['summary']),1)
        status, result = self.request(f'/api/lectures/{self.alice_lecture}/ask', 'POST', {'question':'What is the Sprint Review?'})
        self.assertEqual(status,200)
        self.assertTrue(result['found'])
        self.assertEqual(result['sources'][0]['page'],2)
        status, result = self.request(f'/api/lectures/{self.alice_lecture}/questions')
        self.assertEqual(status,200)
        self.assertGreaterEqual(len(result['questions']),4)
        status, document = self.request(f'/api/lectures/{self.alice_lecture}/file')
        self.assertEqual(status,200)
        self.assertTrue(document.startswith(b'%PDF-'))
        status, state = self.request('/api/state',user='bob')
        self.assertEqual(state['lectures'],[])

    def test_06_session_logout_login_and_bad_password(self):
        status, result = self.request('/api/auth/logout','POST',{})
        self.assertEqual(status,200)
        for path in ('/api/state','/api/export/tasks.csv',f'/api/lectures/{self.alice_lecture}/file'):
            status,err=self.request(path)
            self.assertEqual(status,401)
        status, err = self.request('/api/auth/login','POST',{'email':'alice@example.com','password':'wrong-password'})
        self.assertEqual(status,401)
        status, result = self.request('/api/auth/login','POST',{'email':'alice@example.com','password':'A very strong student password!'})
        self.assertEqual(status,200)
        status, state = self.request('/api/state')
        self.assertEqual(state['tasks'][0]['title'],'Alice private assignment')
        self.assertEqual(state['lectures'][0]['filename'],'Scrum_Practice_Lecture.pdf')
        # A second browser cannot share Alice's session without the cookie.
        status, body = self.request('/api/state', user='anonymous')
        self.assertEqual(status,401)

    def test_07_csrf_and_auth_request_validation(self):
        status, err = self.request('/api/courses','POST',{'code':'CS999','name':'Protected','color':'#777777'},
                                   headers={'Origin':'https://evil.example'})
        self.assertEqual(status,403)
        status, err = self.request('/api/courses','POST',{'code':'CS999','name':'Protected','color':'#777777'},
                                   headers={'Sec-Fetch-Site':'cross-site'})
        self.assertEqual(status,403)
        status, err = self.request('/api/courses','POST',b'wrong content type',content_type='text/plain')
        self.assertEqual(status,400)


if __name__ == '__main__':
    unittest.main()
