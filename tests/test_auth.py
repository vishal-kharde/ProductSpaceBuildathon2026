import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
os.environ.setdefault('BIDLENS_PASSWORD', 'productspace')
os.environ.setdefault('AUTH_SECRET', 'test-secret')
from auth import issue_token, verify_token
from fastapi.testclient import TestClient
import main
main.settings.bidlens_password = 'productspace'
main.settings.auth_secret = 'test-secret'


def test_token_roundtrip():
    token = issue_token('admin')
    assert verify_token(token) == 'admin'
    assert verify_token(token + 'x') is None


def test_login_and_protected_route():
    with TestClient(main.app) as client:
        bad = client.post('/api/auth/login', json={'username':'admin','password':'wrong'})
        assert bad.status_code == 401
        good = client.post('/api/auth/login', json={'username':'admin','password':'productspace'})
        assert good.status_code == 200
        token = good.json()['token']
        assert client.get('/api/templates').status_code == 401
        assert client.get('/api/templates', headers={'Authorization':f'Bearer {token}'}).status_code == 200
        assert client.get('/api/health').status_code == 200

def test_resources_require_auth_and_exclude_requirement_files():
    with TestClient(main.app) as client:
        assert client.get('/api/resources').status_code == 401
        good = client.post('/api/auth/login', json={'username':'admin','password':'productspace'})
        token = good.json()['token']
        r = client.get('/api/resources', headers={'Authorization':f'Bearer {token}'})
        assert r.status_code == 200
        payload = r.json()
        assert payload['resources']
        names = [f['name'].lower() for g in payload['resources'] for f in g['files']]
        assert 'requirements.txt' not in names
