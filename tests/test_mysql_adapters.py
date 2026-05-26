from edc_extractor import mysql_adapters
from edc_extractor.config import DBConfig
from edc_extractor.mysql_adapters import MySQLEDCTarget, _load_enabled_entities


def test_load_enabled_entities_reads_explicit_backup_flag():
    conn = FakeSelectConnection(
        [
            {
                "id": 1,
                "edc_name": "TJ-Bilibili-backup",
                "sn": "SN1",
                "display_name": "TJ-Bilibili",
                "region": "天津",
                "cp": "bilibili",
                "is_backup": 1,
            }
        ]
    )

    entities = _load_enabled_entities(conn)

    assert entities[0].is_backup is True


def test_upsert_entities_writes_explicit_backup_flag(monkeypatch):
    conn = FakeWriteConnection()
    monkeypatch.setattr(mysql_adapters, "connect", lambda config: conn)
    target = MySQLEDCTarget(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
        )
    )

    target.upsert_entities(
        [
            {
                "edc_name": "TJ-Bilibili-backup",
                "sn": "SN1",
                "display_name": "TJ-Bilibili",
                "region": "天津",
                "cp": "bilibili",
                "enabled": True,
                "is_backup": True,
                "remark": "backup node",
            }
        ]
    )

    query = conn.cursor_obj.executemany_query
    payload = conn.cursor_obj.executemany_payload
    assert "is_backup" in query
    assert payload[0][5] == 1


class FakeSelectConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self, dictionary=False):
        return FakeSelectCursor(self.rows)


class FakeSelectCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, query, params=None):
        return None

    def __iter__(self):
        return iter(self.rows)


class FakeWriteCursor:
    def __init__(self):
        self.executemany_query = ""
        self.executemany_payload = []

    def execute(self, query, params=None):
        return None

    def fetchone(self):
        return (1,)

    def executemany(self, query, payload):
        self.executemany_query = query
        self.executemany_payload = payload


class FakeWriteConnection:
    def __init__(self):
        self.cursor_obj = FakeWriteCursor()
        self.committed = False

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        return None

    def close(self):
        return None
