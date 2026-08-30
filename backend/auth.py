from __future__ import annotations
import base64, hashlib, hmac, json, time
from fastapi import Header, HTTPException
from config import settings
TOKEN_TTL_SECONDS = 12 * 60 * 60

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')

def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))

def _sign(payload: str) -> str:
    return _b64(hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).digest())

def issue_token(username: str) -> str:
    payload = _b64(json.dumps({'sub': username, 'exp': int(time.time()) + TOKEN_TTL_SECONDS}, separators=(',', ':')).encode())
    return f'{payload}.{_sign(payload)}'

def verify_token(token: str) -> str | None:
    try:
        payload, signature = token.split('.', 1)
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        data = json.loads(_unb64(payload).decode())
        if int(data.get('exp', 0)) < int(time.time()):
            return None
        username = str(data.get('sub', ''))
        return username or None
    except Exception:
        return None

def require_auth(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(status_code=401, detail='Authentication required')
    username = verify_token(authorization[7:].strip())
    if username is None:
        raise HTTPException(status_code=401, detail='Session expired. Please sign in again.')
    return username
