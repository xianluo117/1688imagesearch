"""Persistent completion-order windows and one-shot alert events."""
import json
import time


async def initialize_alerts(db):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS alert_window (
            singleton INTEGER PRIMARY KEY, sequence INTEGER NOT NULL,
            consecutive INTEGER NOT NULL, recent TEXT NOT NULL, active INTEGER NOT NULL);
        INSERT OR IGNORE INTO alert_window VALUES (1,0,0,'[]',0);
        CREATE TABLE IF NOT EXISTS account_alert_cycles (
            account_id TEXT PRIMARY KEY, active INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, reason TEXT NOT NULL,
            task_kind TEXT NOT NULL, task_id TEXT NOT NULL, message TEXT NOT NULL,
            created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            sent_at REAL, error_details TEXT);
        UPDATE alert_events SET status='failed', error_details='进程在发送期间退出，发送结果未知；未自动重试'
            WHERE status='sending';
    """)


async def add_event(db, reason, kind, task_id, message):
    await db.execute("INSERT INTO alert_events(reason,task_kind,task_id,message,created_at) VALUES (?,?,?,?,?)",
                     (reason, kind, task_id, message, time.time()))


async def record_completion(db, row, status, message):
    if status not in ('succeeded', 'failed'):
        return
    cursor = await db.execute("SELECT * FROM alert_window WHERE singleton=1")
    window = await cursor.fetchone()
    failed = status == 'failed'
    recent = (json.loads(window['recent']) + [int(failed)])[-10:]
    consecutive = window['consecutive'] + 1 if failed else 0
    active = consecutive >= 3 or (len(recent) == 10 and sum(recent) >= 8)
    if active and not window['active']:
        await add_event(db, 'GLOBAL_FAILURE', row['kind'], row['task_id'],
                        f"连续失败 {consecutive}；最近 {len(recent)} 个失败 {sum(recent)}；{message or ''}")
    await db.execute("UPDATE alert_window SET sequence=sequence+1,consecutive=?,recent=?,active=? WHERE singleton=1",
                     (consecutive, json.dumps(recent), int(active)))


async def account_exception(db, account_id, reason):
    cursor = await db.execute("SELECT active FROM account_alert_cycles WHERE account_id=?", (account_id,))
    row = await cursor.fetchone()
    if row and row[0]:
        return
    await db.execute("INSERT INTO account_alert_cycles VALUES (?,1) ON CONFLICT(account_id) DO UPDATE SET active=1", (account_id,))
    await add_event(db, f'{account_id}:{reason}', 'account', '', f'账号 {account_id}: {reason}')


class AlertStoreMixin:
    async def claim_alert_event(self):
        async with self._queue_connection() as db:
            await db.execute('BEGIN IMMEDIATE')
            cursor = await db.execute("SELECT * FROM alert_events WHERE status='pending' ORDER BY id LIMIT 1")
            row = await cursor.fetchone()
            if row is None:
                return None
            await db.execute("UPDATE alert_events SET status='sending' WHERE id=?", (row['id'],))
            await db.commit()
            return dict(row)

    async def finish_alert_event(self, event_id, error_details=None):
        async with self._queue_connection() as db:
            await db.execute("UPDATE alert_events SET status=?,sent_at=?,error_details=? WHERE id=?",
                             ('failed' if error_details else 'sent', None if error_details else time.time(), error_details, event_id))
            await db.commit()

    async def list_alert_events(self):
        async with self._queue_connection() as db:
            cursor = await db.execute('SELECT * FROM alert_events ORDER BY id DESC')
            return [dict(row) for row in await cursor.fetchall()]
