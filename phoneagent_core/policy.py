"""Session-scoped paths and one-shot approval of unrestricted side effects.

cwd is not an OS sandbox. Side effects require manual or explicit Work-wide approval.
"""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path


def validate_root(raw, protected):
    if not raw or str(raw).startswith('content:'):
        raise ValueError('请选择 Termux 可访问的目录；SAF URI 需要 Android 桥接，不能当作路径')
    p = Path(str(raw)).expanduser().resolve(strict=True)
    if not p.is_dir() or not os.access(p, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError('目录无效或权限已撤销')
    forbidden = {Path(p.anchor), Path.home().resolve(), Path(protected).resolve(),
                 Path('/storage/emulated/0').resolve(), Path('/sdcard').resolve()}
    if p in forbidden or Path(protected).resolve() in p.parents:
        raise ValueError('请选择具体项目子目录，不能授权根目录、主目录、共享存储根或密钥目录')
    return p


def resolve_path(root, raw, protected, allow_outside=False):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
        raise ValueError('工作目录失效或权限已撤销')
    p = Path(str(raw or '.')).expanduser()
    p = (p if p.is_absolute() else root / p).resolve()
    if not allow_outside:
        try:
            p.relative_to(root)
        except ValueError:
            raise ValueError('路径超出本会话工作目录；可在工具权限允许访问工作区外文件') from None
    secret = Path(protected).resolve()
    if p == secret or secret in p.parents or (not allow_outside and p in secret.parents):
        raise ValueError('不能通过工具访问或删除密钥配置目录')
    if p.name in {'secrets.json', 'gemini.key', 'chatgpt_accounts.json', 'chatgpt_hostid'} or (not allow_outside and (p.name == '.env' or p.suffix.lower() in {'.key', '.pem'})):
        raise ValueError('不能通过文件工具访问密钥文件')
    return p


def prepare(conn, info, name, args, protected, auto_approve=False, allow_outside=False):
    if info['mode'] != 'work':
        raise ValueError('Chat 禁止执行工具')
    args = dict(args)
    root = info['settings'].get('workdir')
    if not root:
        if allow_outside:
            root = str(Path.home())
        else:
            raise ValueError('请先选择本会话工作目录；或在工具权限开启工作区外访问')
    needs_approval = name in {'run_command', 'delete_file', 'open_in_cxxdroid'}
    if name in {'read_file', 'write_file', 'delete_file', 'list_files', 'view_image', 'open_in_cxxdroid'}:
        p = resolve_path(root, args.get('path', '.'), protected, allow_outside)
        if name == 'delete_file' and p == Path(root).resolve():
            raise ValueError('禁止删除工作目录本身')
        if name == 'delete_file' and (p in {Path(p.anchor), Path.home().resolve(), Path('/sdcard').resolve(), Path('/storage/emulated/0').resolve()} or p in Path(protected).resolve().parents):
            raise ValueError('禁止删除系统/主目录/共享存储根及包含私有配置的祖先目录')
        if name == 'write_file' and p.exists():
            needs_approval = True
        args['path'] = str(p)
    if name == 'run_command':
        args['workdir'] = str(resolve_path(root, args.get('workdir') or '.', protected, allow_outside))
        if not Path(args['workdir']).is_dir():
            raise ValueError('命令工作目录不存在')
    if needs_approval:
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        sig = hashlib.sha256((name + serialized).encode()).hexdigest()
        rejected = conn.execute("SELECT 1 FROM approvals WHERE conv_id=? AND signature=? AND state='rejected' AND created>?",
                                (info['id'], sig, time.time()-900)).fetchone()
        if rejected:
            raise ValueError('用户已拒绝这项操作；禁止重试同一操作')
        if auto_approve is True:
            # Keep an audit record, but never turn blanket permission into a
            # reusable manual approval after the user disables the setting.
            conn.execute("UPDATE approvals SET state='auto-used' WHERE conv_id=? AND signature=? AND state='pending'",
                         (info['id'], sig))
            conn.execute('INSERT INTO approvals(id,conv_id,signature,tool,args,state,created) VALUES(?,?,?,?,?,?,?)',
                         (uuid.uuid4().hex, info['id'], sig, name, serialized, 'auto-used', time.time()))
            conn.commit()
            return args, None
        approved = conn.execute("SELECT id FROM approvals WHERE conv_id=? AND signature=? AND state='approved' AND created>?",
                                (info['id'], sig, time.time() - 900)).fetchone()
        if approved:
            conn.execute("UPDATE approvals SET state='used' WHERE id=?", (approved['id'],))
            conn.commit()
        else:
            existing = conn.execute("SELECT id FROM approvals WHERE conv_id=? AND signature=? AND state='pending' AND created>?",
                                    (info['id'], sig, time.time() - 900)).fetchone()
            aid = existing['id'] if existing else uuid.uuid4().hex
            if not existing:
                conn.execute('INSERT INTO approvals(id,conv_id,signature,tool,args,state,created) VALUES(?,?,?,?,?,?,?)',
                             (aid, info['id'], sig, name, serialized, 'pending', time.time()))
                conn.commit()
            return None, {'ok': False, 'approval_id': aid,
                          'output': '等待批准这项具体操作；批准后点击重试本轮。命令可以访问 Termux 已有权限内的文件，工作目录不是系统沙箱。'}
    return args, None
