from pathlib import Path

path = Path('orchestrator/tests/test_pool_reservation_bridge.py')
text = path.read_text(encoding='utf-8')
needle = """    monkeypatch.setattr(bridge, 'POOL_BRIDGE_PSEUDONYM_KEY', 'pseudonym-secret')\n    captured = []\n"""
replacement = """    monkeypatch.setattr(bridge, 'POOL_BRIDGE_PSEUDONYM_KEY', 'pseudonym-secret')\n    monkeypatch.setattr(bridge, 'get_pool_subject_alias', lambda code, device_id: '')\n    captured = []\n"""
if needle not in text:
    raise SystemExit('pool bridge pseudonym test patch point missing')
path.write_text(text.replace(needle, replacement, 1), encoding='utf-8')
