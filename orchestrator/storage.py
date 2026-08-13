import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from config import DATABASE_PATH


SERVER_REVISION_KEY = 'servers_revision'


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def future_iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def extend_iso(existing_value: Optional[str], days: int) -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    base = parse_iso(existing_value) or now
    if base < now:
        base = now
    return (base + timedelta(days=days)).replace(microsecond=0).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DATABASE_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _add_column_if_missing(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = [row['name'] for row in con.execute(f'PRAGMA table_info({table})').fetchall()]
    if column not in existing:
        con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def init_storage() -> None:
    with connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS activation_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT "active", device_id TEXT, note TEXT, created_at TEXT NOT NULL, expires_at TEXT, used_at TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS servers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, region TEXT NOT NULL, config TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS code_devices (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, device_id TEXT NOT NULL, activated_at TEXT NOT NULL, UNIQUE(code, device_id))')
        con.execute('CREATE TABLE IF NOT EXISTS checkout_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, external_id TEXT UNIQUE, plan TEXT NOT NULL, customer TEXT, code TEXT NOT NULL, status TEXT NOT NULL DEFAULT "paid", created_at TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS system_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, code TEXT, plan TEXT, message TEXT NOT NULL, created_at TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        con.execute('INSERT OR IGNORE INTO app_state(key, value) VALUES (?, 0)', (SERVER_REVISION_KEY,))
        _add_column_if_missing(con, 'activation_codes', 'max_devices', 'INTEGER NOT NULL DEFAULT 1')
        _add_column_if_missing(con, 'activation_codes', 'plan', 'TEXT NOT NULL DEFAULT "manual"')
        _add_column_if_missing(con, 'servers', 'pool_node_id', 'INTEGER')
        _ensure_pool_assignment_columns(con)


def _ensure_pool_assignment_columns(con: sqlite3.Connection) -> None:
    additions = {
        'pool_assignment_id': 'INTEGER',
        'pool_status': 'TEXT NOT NULL DEFAULT ""',
        'pool_confirmation_token': 'TEXT NOT NULL DEFAULT ""',
        'pool_node_id': 'INTEGER',
        'pool_node_name': 'TEXT NOT NULL DEFAULT ""',
        'pool_region': 'TEXT NOT NULL DEFAULT ""',
        'pool_config': 'TEXT NOT NULL DEFAULT ""',
        'pool_config_revision': 'INTEGER NOT NULL DEFAULT 0',
        'pool_speed_limit_mbps': 'INTEGER NOT NULL DEFAULT 0',
        'pool_entitlement_hash': 'TEXT NOT NULL DEFAULT ""',
        'pool_entitlement_expires_at': 'TEXT NOT NULL DEFAULT ""',
        'pool_updated_at': 'TEXT',
    }
    for column, definition in additions.items():
        _add_column_if_missing(con, 'code_devices', column, definition)
    con.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_code_devices_pool_assignment
        ON code_devices(pool_assignment_id)
        WHERE pool_assignment_id IS NOT NULL
        '''
    )


def save_device_pool_assignment_in_connection(
    con: sqlite3.Connection,
    code: str,
    device_id: str,
    assignment: Dict[str, Any],
) -> None:
    cursor = con.execute(
        '''
        UPDATE code_devices
        SET pool_assignment_id = ?,
            pool_status = ?,
            pool_confirmation_token = ?,
            pool_node_id = ?,
            pool_node_name = ?,
            pool_region = ?,
            pool_config = ?,
            pool_config_revision = ?,
            pool_speed_limit_mbps = ?,
            pool_entitlement_hash = ?,
            pool_entitlement_expires_at = ?,
            pool_updated_at = ?
        WHERE code = ? AND device_id = ?
        ''',
        (
            int(assignment.get('pool_assignment_id') or 0) or None,
            str(assignment.get('pool_status') or '')[:32],
            str(assignment.get('pool_confirmation_token') or '')[:256],
            int(assignment.get('pool_node_id') or 0) or None,
            str(assignment.get('pool_node_name') or '')[:128],
            str(assignment.get('pool_region') or '')[:32],
            str(assignment.get('pool_config') or ''),
            int(assignment.get('pool_config_revision') or 0),
            int(assignment.get('pool_speed_limit_mbps') or 0),
            str(assignment.get('pool_entitlement_hash') or '')[:128],
            str(assignment.get('pool_entitlement_expires_at') or '')[:64],
            now_iso(),
            format_code(code),
            device_id.strip()[:128],
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError('device_pool_assignment_target_missing')


def save_device_pool_assignment(code: str, device_id: str, assignment: Dict[str, Any]) -> None:
    with connect() as con:
        _ensure_pool_assignment_columns(con)
        save_device_pool_assignment_in_connection(con, code, device_id, assignment)


def get_device_pool_assignment(code: str, device_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        _ensure_pool_assignment_columns(con)
        row = con.execute(
            '''
            SELECT pool_assignment_id,
                   pool_status,
                   pool_confirmation_token,
                   pool_node_id,
                   pool_node_name,
                   pool_region,
                   pool_config,
                   pool_config_revision,
                   pool_speed_limit_mbps,
                   pool_entitlement_hash,
                   pool_entitlement_expires_at,
                   pool_updated_at
            FROM code_devices
            WHERE code = ? AND device_id = ? AND active = 1
            ''',
            (format_code(code), device_id.strip()[:128]),
        ).fetchone()
    if not row or not row['pool_assignment_id'] or not str(row['pool_config'] or '').strip():
        return None
    return dict(row)


def get_device_pool_entitlement(code: str, device_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute(
            '''
            SELECT activation_codes.code,
                   activation_codes.plan,
                   activation_codes.expires_at
            FROM activation_codes
            JOIN code_devices ON code_devices.code = activation_codes.code
            WHERE activation_codes.code = ?
              AND activation_codes.status = 'active'
              AND code_devices.device_id = ?
              AND code_devices.active = 1
            ''',
            (format_code(code), device_id.strip()[:128]),
        ).fetchone()
    return dict(row) if row else None


def mark_device_pool_assignment_confirmed(assignment_id: int) -> None:
    with connect() as con:
        _ensure_pool_assignment_columns(con)
        con.execute(
            '''
            UPDATE code_devices
            SET pool_status = 'active',
                pool_confirmation_token = '',
                pool_updated_at = ?
            WHERE pool_assignment_id = ?
            ''',
            (now_iso(), int(assignment_id)),
        )


def _bump_server_revision(con: sqlite3.Connection) -> None:
    cursor = con.execute(
        'UPDATE app_state SET value = value + 1 WHERE key = ?',
        (SERVER_REVISION_KEY,),
    )
    if cursor.rowcount == 0:
        con.execute(
            'INSERT INTO app_state(key, value) VALUES (?, 1)',
            (SERVER_REVISION_KEY,),
        )


def add_event(event_type: str, message: str, code: str = '', plan: str = '') -> None:
    with connect() as con:
        con.execute(
            'INSERT INTO system_events(event_type, code, plan, message, created_at) VALUES (?, ?, ?, ?, ?)',
            (event_type, code, plan, message, now_iso()),
        )


def list_events(limit: int = 30) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            'SELECT id, event_type, code, plan, message, created_at FROM system_events ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def normalize_code(value: str) -> str:
    return ''.join(ch for ch in value.upper() if ch.isalnum())


def format_code(value: str) -> str:
    groups = [1, 3, 2, 2, 2, 1]
    normalized = normalize_code(value)
    parts: List[str] = []
    index = 0
    for size in groups:
        part = normalized[index:index + size]
        if part:
            parts.append(part)
        index += size
    return '-'.join(parts)


def make_code() -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    raw = ''.join(secrets.choice(alphabet) for _ in range(11))
    return format_code(raw)


def create_activation_code(days: int = 30, note: str = '', max_devices: int = 1, plan: str = 'manual') -> str:
    code = make_code()
    safe_max_devices = max(1, min(int(max_devices), 20))
    with connect() as con:
        con.execute(
            'INSERT INTO activation_codes(code, status, note, created_at, expires_at, max_devices, plan) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (code, 'active', note, now_iso(), future_iso(days), safe_max_devices, plan),
        )
    add_event('code_created', f'Code {code} created: {plan}, devices {safe_max_devices}', code, plan)
    return code


def create_checkout_code(plan: str, max_devices: int, days: int = 30, customer: str = '', external_id: Optional[str] = None) -> Dict[str, Any]:
    external_id = external_id or secrets.token_urlsafe(18)
    with connect() as con:
        existing = con.execute('SELECT code FROM checkout_orders WHERE external_id = ?', (external_id,)).fetchone()
        if existing:
            code = existing['code']
            row = con.execute('SELECT code, plan, max_devices, expires_at FROM activation_codes WHERE code = ?', (code,)).fetchone()
            result = dict(row) if row else {'code': code, 'plan': plan, 'max_devices': max_devices}
            result['external_id'] = external_id
            return result
        code = make_code()
        note = customer or plan
        safe_max_devices = max(1, min(int(max_devices), 20))
        con.execute(
            'INSERT INTO activation_codes(code, status, note, created_at, expires_at, max_devices, plan) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (code, 'active', note, now_iso(), future_iso(days), safe_max_devices, plan),
        )
        con.execute(
            'INSERT INTO checkout_orders(external_id, plan, customer, code, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (external_id, plan, customer, code, 'paid', now_iso()),
        )
        row = con.execute('SELECT code, plan, max_devices, expires_at FROM activation_codes WHERE code = ?', (code,)).fetchone()
    add_event('checkout_code_created', f'Checkout issued {code}: {plan}, devices {safe_max_devices}', code, plan)
    result = dict(row)
    result['external_id'] = external_id
    return result


def get_activation_code(code: str) -> Optional[Dict[str, Any]]:
    formatted = format_code(code)
    with connect() as con:
        row = con.execute(
            'SELECT code, status, expires_at, max_devices, plan, (SELECT COUNT(*) FROM code_devices WHERE code_devices.code = activation_codes.code) AS used_devices FROM activation_codes WHERE code = ?',
            (formatted,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        expires_at = parse_iso(result.get('expires_at'))
        if result.get('status') == 'active' and expires_at and expires_at <= datetime.now(timezone.utc):
            con.execute('UPDATE activation_codes SET status = ? WHERE code = ?', ('expired', formatted))
            result['status'] = 'expired'
        return result


def renew_activation_code(code: str, plan: str, max_devices: int, days: int = 30, customer: str = '', external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    formatted = format_code(code)
    external_id = external_id or secrets.token_urlsafe(18)
    safe_max_devices = max(1, min(int(max_devices), 20))
    with connect() as con:
        row = con.execute('SELECT code, expires_at FROM activation_codes WHERE code = ?', (formatted,)).fetchone()
        if not row:
            return None

        new_expires_at = extend_iso(row['expires_at'], days)
        con.execute(
            'UPDATE activation_codes SET status = ?, note = ?, expires_at = ?, max_devices = ?, plan = ? WHERE code = ?',
            ('active', customer or plan, new_expires_at, safe_max_devices, plan, formatted),
        )
        con.execute(
            'INSERT INTO checkout_orders(external_id, plan, customer, code, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (external_id, plan, customer, formatted, 'paid', now_iso()),
        )
        updated = con.execute(
            'SELECT code, plan, max_devices, expires_at FROM activation_codes WHERE code = ?',
            (formatted,),
        ).fetchone()

    add_event('checkout_code_renewed', f'Checkout renewed {formatted}: {plan}, devices {safe_max_devices}', formatted, plan)
    result = dict(updated)
    result['external_id'] = external_id
    return result


def get_checkout_order(external_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute(
            'SELECT checkout_orders.external_id, checkout_orders.plan, checkout_orders.customer, checkout_orders.code, checkout_orders.status, checkout_orders.created_at, activation_codes.max_devices, activation_codes.expires_at, activation_codes.used_at FROM checkout_orders JOIN activation_codes ON activation_codes.code = checkout_orders.code WHERE checkout_orders.external_id = ?',
            (external_id,),
        ).fetchone()
    return dict(row) if row else None


def get_codes(limit: int = 20) -> List[Dict[str, Any]]:
    with connect() as con:
        con.execute(
            'UPDATE activation_codes SET status = ? WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?',
            ('expired', 'active', now_iso()),
        )
        rows = con.execute(
            'SELECT code, status, device_id, note, created_at, expires_at, used_at, max_devices, plan, (SELECT COUNT(*) FROM code_devices WHERE code_devices.code = activation_codes.code) AS used_devices FROM activation_codes ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_activation_code(code: str) -> bool:
    formatted = format_code(code)
    with connect() as con:
        cursor = con.execute('UPDATE activation_codes SET status = ? WHERE code = ?', ('banned', formatted))
    if cursor.rowcount > 0:
        add_event('code_revoked', f'Code {formatted} revoked', formatted, '')
        return True
    return False


def save_server(name: str, region: str, config_text: str) -> int:
    with connect() as con:
        con.execute('UPDATE servers SET is_active = 0')
        cursor = con.execute('INSERT INTO servers(name, region, config, is_active, created_at) VALUES (?, ?, ?, 1, ?)', (name, region, config_text, now_iso()))
        _bump_server_revision(con)
    add_event('config_added', f'Config {name} saved and activated', '', region)
    return int(cursor.lastrowid)


def list_servers(limit: int = 20) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            'SELECT id, name, region, is_active, pool_node_id, created_at FROM servers ORDER BY id DESC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_server_records(limit: int = 500) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            'SELECT id, name, region, config, is_active, pool_node_id, created_at FROM servers ORDER BY id ASC LIMIT ?',
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_server(server_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute(
            'SELECT id, name, region, config, is_active, pool_node_id, created_at FROM servers WHERE id = ?',
            (server_id,),
        ).fetchone()
    return dict(row) if row else None


def set_server_pool_node_id(server_id: int, pool_node_id: int) -> bool:
    with connect() as con:
        cursor = con.execute(
            'UPDATE servers SET pool_node_id = ? WHERE id = ?',
            (pool_node_id, server_id),
        )
    return cursor.rowcount > 0


def set_active_server(server_id: int) -> bool:
    with connect() as con:
        row = con.execute('SELECT id FROM servers WHERE id = ?', (server_id,)).fetchone()
        if not row:
            return False
        con.execute('UPDATE servers SET is_active = 0')
        con.execute('UPDATE servers SET is_active = 1 WHERE id = ?', (server_id,))
        _bump_server_revision(con)
    add_event('config_activated', f'Config {server_id} activated', '', '')
    return True


def delete_server(server_id: int) -> bool:
    with connect() as con:
        row = con.execute('SELECT id, is_active, name FROM servers WHERE id = ?', (server_id,)).fetchone()
        if not row:
            return False
        was_active = bool(row['is_active'])
        name = row['name']
        con.execute('DELETE FROM servers WHERE id = ?', (server_id,))
        if was_active:
            replacement = con.execute('SELECT id FROM servers ORDER BY id DESC LIMIT 1').fetchone()
            if replacement:
                con.execute('UPDATE servers SET is_active = 1 WHERE id = ?', (replacement['id'],))
        _bump_server_revision(con)
    add_event('config_deleted', f'Config {server_id} {name} deleted', '', '')
    return True


def get_active_server() -> Optional[Dict[str, Any]]:
    with connect() as con:
        row = con.execute('SELECT id, name, region, config FROM servers WHERE is_active = 1 ORDER BY id DESC LIMIT 1').fetchone()
    return dict(row) if row else None


def get_server_snapshot() -> Dict[str, Any]:
    with connect() as con:
        row = con.execute(
            '''
            SELECT
                COALESCE((SELECT value FROM app_state WHERE key = ?), 0) AS revision,
                active.id,
                active.name,
                active.region,
                active.config
            FROM (SELECT 1) AS singleton
            LEFT JOIN servers AS active
                ON active.id = (
                    SELECT id
                    FROM servers
                    WHERE is_active = 1
                    ORDER BY id DESC
                    LIMIT 1
                )
            ''',
            (SERVER_REVISION_KEY,),
        ).fetchone()

    server = None
    if row and row['id'] is not None:
        server = {
            'id': row['id'],
            'name': row['name'],
            'region': row['region'],
            'config': row['config'],
        }
    return {
        'revision': int(row['revision'] if row else 0),
        'server': server,
    }


def check_activation_access(code: str, device_id: str) -> Dict[str, Any]:
    formatted = format_code(code)
    safe_device_id = device_id.strip()[:128]
    if not safe_device_id:
        return {'ok': False, 'reason': 'not_bound'}

    with connect() as con:
        row = con.execute(
            'SELECT code, status, device_id, expires_at, max_devices, plan FROM activation_codes WHERE code = ?',
            (formatted,),
        ).fetchone()
        if not row:
            return {'ok': False, 'reason': 'not_found'}
        if row['status'] != 'active':
            return {'ok': False, 'reason': row['status']}

        expires_at = parse_iso(row['expires_at'])
        if expires_at and expires_at <= datetime.now(timezone.utc):
            con.execute('UPDATE activation_codes SET status = ? WHERE code = ?', ('expired', formatted))
            return {'ok': False, 'reason': 'expired'}

        existing_device = con.execute(
            'SELECT id FROM code_devices WHERE code = ? AND device_id = ?',
            (formatted, safe_device_id),
        ).fetchone()
        legacy_device_matches = row['device_id'] == safe_device_id
        if not existing_device and not legacy_device_matches:
            return {'ok': False, 'reason': 'not_bound'}

    return {
        'ok': True,
        'code': formatted,
        'maxDevices': int(row['max_devices'] or 1),
        'plan': row['plan'],
    }


def validate_activation_code(code: str, device_id: str) -> Dict[str, Any]:
    formatted = format_code(code)
    safe_device_id = device_id.strip()[:128]
    current_time = now_iso()
    with connect() as con:
        row = con.execute('SELECT * FROM activation_codes WHERE code = ?', (formatted,)).fetchone()
        if not row:
            return {'ok': False, 'reason': 'not_found'}
        if row['status'] != 'active':
            return {'ok': False, 'reason': row['status']}
        if row['expires_at'] and row['expires_at'] < current_time:
            con.execute('UPDATE activation_codes SET status = ? WHERE code = ?', ('expired', formatted))
            return {'ok': False, 'reason': 'expired'}

        existing_device = con.execute('SELECT id FROM code_devices WHERE code = ? AND device_id = ?', (formatted, safe_device_id)).fetchone()
        used_devices = con.execute('SELECT COUNT(*) AS count FROM code_devices WHERE code = ?', (formatted,)).fetchone()['count']
        max_devices = int(row['max_devices'] or 1)

        if not existing_device:
            if used_devices >= max_devices:
                return {'ok': False, 'reason': 'device_limit', 'usedDevices': used_devices, 'maxDevices': max_devices}
            con.execute('INSERT INTO code_devices(code, device_id, activated_at) VALUES (?, ?, ?)', (formatted, safe_device_id, current_time))
            used_devices += 1

        con.execute('UPDATE activation_codes SET device_id = COALESCE(device_id, ?), used_at = COALESCE(used_at, ?) WHERE code = ?', (safe_device_id, current_time, formatted))

    add_event('code_validated', f'Code {formatted} validated: {used_devices}/{max_devices}', formatted, row['plan'])
    return {'ok': True, 'code': formatted, 'usedDevices': used_devices, 'maxDevices': max_devices, 'plan': row['plan']}
