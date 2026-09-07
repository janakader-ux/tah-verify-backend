"""Connections must close after polling, including failures, without relying on GC."""
import ast
import gc
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'api_server.py'

class CleanupTests(unittest.TestCase):
    def test_polling_releases_connections_and_preserves_transactions(self):
        names = {'deliver_pending_emails', 'deliver_trustid_notifications', '_deliver_outbox_email', '_brevo_send'}
        nodes = [n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name in names]
        real_connect = sqlite3.connect
        opened = []
        class Tracked(sqlite3.Connection):
            def close(self):
                super().close()
                if self in opened:
                    opened.remove(self)
        def connect(*args, **kwargs):
            con = real_connect(*args, **kwargs, factory=Tracked)
            opened.append(con)
            return con
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'test.db')
            with closing(real_connect(path)) as con, con:
                con.execute('CREATE TABLE notification_outbox (message_key TEXT PRIMARY KEY, recipient TEXT, subject TEXT, body TEXT, kind TEXT, accepted INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0)')
                con.execute('CREATE TABLE trustid_result_notifications (container_id TEXT, case_ref TEXT, sent INTEGER DEFAULT 0)')
            import hashlib, json
            ns = dict(sqlite3=sqlite3, closing=closing, DB_PATH=path, _email_lock=threading.RLock(), hashlib=hashlib, json=json)
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
            enabled = gc.isenabled()
            gc.disable()
            try:
                with patch.object(sqlite3, 'connect', connect):
                    for _ in range(1000):
                        ns['deliver_pending_emails']()
                        ns['deliver_trustid_notifications']()
                        self.assertEqual(len(opened), 0)
                    ns['_brevo_deliver'] = lambda *a: False
                    self.assertFalse(ns['_brevo_send']('test@example.invalid', 'test', 'body'))
                    self.assertEqual(len(opened), 0)
                    ns['_brevo_deliver'] = lambda *a: True
                    ns['deliver_pending_emails']()
                    self.assertEqual(len(opened), 0)
                    with closing(real_connect(path)) as con:
                        self.assertEqual(con.execute('SELECT accepted, attempts, body FROM notification_outbox').fetchone(), (1, 2, ''))
                    with closing(real_connect(path)) as con, con:
                        con.execute("INSERT INTO trustid_result_notifications VALUES ('container', 'case', 0)")
                    def fail(*a, **k):
                        raise RuntimeError('simulated notification failure')
                    ns['send_notification_email'] = fail
                    with self.assertRaises(RuntimeError):
                        ns['deliver_trustid_notifications']()
                    self.assertEqual(len(opened), 0)
                    ns['send_notification_email'] = lambda *a, **k: True
                    ns['deliver_trustid_notifications']()
                    self.assertEqual(len(opened), 0)
                    with closing(real_connect(path)) as con:
                        self.assertEqual(con.execute('SELECT sent FROM trustid_result_notifications').fetchone(), (1,))
            finally:
                for con in list(opened):
                    con.close()
                if enabled:
                    gc.enable()

if __name__ == '__main__':
    unittest.main()
