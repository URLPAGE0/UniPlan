"""Account helpers for the UniPlan coursework application.

Uses the Python standard library: scrypt password hashes, random server-side
sessions, and scoped session-cookie authentication. No passwords are stored as text.
"""
from collections import defaultdict, deque
import hashlib
import hmac
import re
import secrets
import threading
import time

SESSION_SECONDS = 12 * 60 * 60
SESSION_COOKIE = 'uniplan_session'


def normal_email(raw):
    if not isinstance(raw, str):
        raise ValueError('Enter a valid email address.')
    email = raw.strip().casefold()
    if (len(email) > 254 or not re.fullmatch(r'[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}', email)
            or any(ord(c) < 33 or ord(c) > 126 for c in email)):
        raise ValueError('Enter a valid email address.')
    return email


def valid_name(raw):
    if not isinstance(raw, str):
        raise ValueError('Enter your name.')
    name = raw.strip()
    if not 2 <= len(name) <= 100 or any(ord(c) < 32 for c in name):
        raise ValueError('Name must be between 2 and 100 characters.')
    return name


def valid_password(raw):
    if not isinstance(raw, str) or not 10 <= len(raw) <= 128 or '\x00' in raw:
        raise ValueError('Password must contain 10–128 characters.')
    return raw


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(valid_password(password).encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$' + salt.hex() + '$' + digest.hex()


def verify_password(password, stored):
    # Run a dummy hash for nonexistent users, reducing username enumeration timing.
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        algorithm, n, r, p, salt, correct = stored.split('$')
        if algorithm != 'scrypt' or int(n) > 2**16 or int(r) > 8 or int(p) > 2:
            return False
        digest = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt),
                               n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest, bytes.fromhex(correct))
    except (ValueError, TypeError):
        return False


# Static invalid-user hash protects timing without storing an actual demo account.
DUMMY_HASH = 'scrypt$16384$8$1$00000000000000000000000000000000$' + '00' * 32


def create_session(db, uid):
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode('ascii')).hexdigest()
    expiry = int(time.time()) + SESSION_SECONDS
    db.execute('DELETE FROM sessions WHERE expires_at < ?', (int(time.time()),))
    db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)', (digest, uid, expiry))
    return token


def get_user(db, token):
    if not token or len(token) > 128:
        return None
    digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
    row = db.execute('''SELECT u.id, u.name, u.email, s.expires_at FROM sessions s
                        JOIN users u ON u.id=s.user_id
                        WHERE s.token_hash=?''', (digest,)).fetchone()
    if row is None or row['expires_at'] <= int(time.time()):
        return None
    return {'id': row['id'], 'name': row['name'], 'email': row['email']}


def revoke_session(db, token):
    if token:
        digest = hashlib.sha256(token.encode('utf-8')).hexdigest()
        db.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))


class AttemptLimiter:
    """Small per-process limit for repeated login/registration attempts."""
    def __init__(self):
        self.lock = threading.Lock()
        self.events = defaultdict(deque)

    def allow(self, key, max_attempts=8, window=900):
        now = time.monotonic()
        with self.lock:
            # Expire stale keys to prevent growth in long-running demos.
            if len(self.events) > 10000:
                self.events = defaultdict(deque, {
                    k: v for k, v in self.events.items() if v and now-v[-1] <= window
                })
            events = self.events[key]
            while events and now-events[0] > window:
                events.popleft()
            return len(events) < max_attempts

    def record(self, key):
        with self.lock:
            self.events[key].append(time.monotonic())

    def clear(self, key):
        with self.lock:
            self.events.pop(key, None)


login_limiter = AttemptLimiter()
register_limiter = AttemptLimiter()
