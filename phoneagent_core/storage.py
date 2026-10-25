import json
import os
import time
from pathlib import Path


def migrate(conn):
    # Additive, transactional schema changes: old sessions always remain Work.
    cols = {r[1] for r in conn.execute('PRAGMA table_info(conversations)')}
    for name, kind in [('mode', "TEXT NOT NULL DEFAULT 'work'"),
                       ('settings', "TEXT NOT NULL DEFAULT '{}' ")]:
        if name not in cols:
            conn.execute(f'ALTER TABLE conversations ADD COLUMN {name} {kind}')
    conn.execute('''CREATE TABLE IF NOT EXISTS requests(
        id INTEGER PRIMARY KEY, model TEXT, ts REAL, day TEXT, estimated INTEGER,
        input_tokens INTEGER, output_tokens INTEGER, status TEXT, cooldown REAL DEFAULT 0)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS approvals(
        id TEXT PRIMARY KEY, conv_id TEXT, signature TEXT, tool TEXT, args TEXT,
        state TEXT, created REAL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY, conv_id TEXT, kind TEXT, content TEXT, entity TEXT,
        epistemic TEXT, validity TEXT, source_id INTEGER, occurred TEXT,
        recorded REAL, revises TEXT, vector TEXT, vector_model TEXT,
        audience TEXT, slot TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS memory_session ON memories(conv_id, validity)')
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, content, tokenize='unicode61')")
    except Exception:
        # FTS5 is optional on older Android SQLite builds; lexical fallback exists.
        pass
    conn.commit()


def session(conn, cid):
    row = conn.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
    if row is None:
        raise ValueError('会话不存在')
    result = dict(row)
    result['settings'] = json.loads(result.get('settings') or '{}')
    return result


def update_session(conn, cid, values, default_root, commit=True):
    info = session(conn, cid)
    settings = info['settings']
    if 'mode' in values and values['mode'] != info['mode']:
        raise ValueError('会话模式不可改写；切换模式会创建或恢复独立会话')
    if 'workdir' in values:
        if info['mode'] != 'work':
            raise ValueError('Chat 没有工作目录权限')
        from .policy import validate_root
        settings['workdir'] = str(validate_root(values['workdir'], default_root))
    if 'work_compaction_enabled' in values or 'work_context_budget' in values:
        if info['mode'] != 'work':
            raise ValueError('Work 上下文设置只适用于工作会话')
        if 'work_compaction_enabled' in values:
            if type(values['work_compaction_enabled']) is not bool:
                raise ValueError('压缩开关必须是布尔值')
            settings['work_compaction_enabled'] = values['work_compaction_enabled']
        if 'work_context_budget' in values:
            settings['work_context_budget'] = max(2048, min(64000, int(values['work_context_budget'])))
    for key in ('memory_enabled', 'semantic_enabled'):
        if key in values:
            settings[key] = bool(values[key])
    if 'identity' in values:
        settings['identity'] = str(values['identity'])[:10000]
    if 'scene' in values:
        settings['scene'] = str(values['scene'])[:2000]
    if 'perspective' in values:
        settings['perspective'] = str(values['perspective'])[:100]
    if 'context_budget' in values:
        settings['context_budget'] = max(512, min(6000, int(values['context_budget'])))
    if 'disabled_skills' in values:
        settings['disabled_skills'] = list(values['disabled_skills'])[:200]
    if 'model' in values:
        settings['model'] = str(values['model'])
    if 'reasoning_effort' in values:
        effort = str(values['reasoning_effort'])
        if effort not in {'none','minimal','low','medium','high','xhigh','max'}:
            raise ValueError('无效思考强度')
        settings['reasoning_effort'] = effort
    conn.execute('UPDATE conversations SET settings=? WHERE id=?',
                 (json.dumps(settings, ensure_ascii=False), cid))
    if commit:
        conn.commit()
    return session(conn, cid)
