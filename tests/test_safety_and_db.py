import json
import math
import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from safety import sanitize_json
from config import settings
import db


def test_sanitize_non_finite_numbers():
    value = {'a': float('inf'), 'b': float('-inf'), 'c': float('nan'), 'd': [1.2, float('inf')]}
    out = sanitize_json(value)
    assert out == {'a': None, 'b': None, 'c': None, 'd': [1.2, None]}
    json.dumps(out, allow_nan=False)


def test_latest_sanitizes_legacy_non_finite_json(tmp_path: Path):
    old = settings.db_path
    settings.db_path = str(tmp_path / 'legacy.db')
    try:
        db.init_db()
        c = db.conn()
        # Python's json module can parse legacy Infinity; strict API serialization cannot.
        c.execute("INSERT INTO runs(requirements,result_json) VALUES (?, ?)", ('x', '{"value": Infinity}'))
        c.commit(); c.close()
        result = db.latest_run()
        assert result['result']['value'] is None
        json.dumps(result, allow_nan=False)
    finally:
        settings.db_path = old
