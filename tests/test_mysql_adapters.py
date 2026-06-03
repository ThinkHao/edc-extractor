from edc_extractor import mysql_adapters
from edc_extractor.config import DBConfig
from edc_extractor.mysql_adapters import MySQLEDCTarget, _load_enabled_entities


def test_connect_uses_mysql_connector_supported_timeout_args(monkeypatch):
    captured_kwargs = {}

    def fake_connect(**kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(mysql_adapters.mysql.connector, "connect", fake_connect)

    mysql_adapters.connect(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
            read_timeout_seconds=120,
        )
    )

    assert captured_kwargs["connection_timeout"] == 10
    assert "read_timeout" not in captured_kwargs
    assert "write_timeout" not in captured_kwargs


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


def test_load_entity_keys_ensures_schema_before_read(monkeypatch):
    conn = FakeWriteConnection()
    calls = []

    def fake_ensure_schema(schema_conn):
        calls.append(schema_conn)

    monkeypatch.setattr(mysql_adapters, "connect", lambda config: conn)
    monkeypatch.setattr(mysql_adapters, "_ensure_entity_schema", fake_ensure_schema)
    target = MySQLEDCTarget(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
        )
    )

    assert target.load_entity_keys() == set()
    assert calls == [conn]


def test_ensure_entity_schema_creates_mapping_table():
    conn = FakeSchemaConnection(column_count=1, index_count=1)

    mysql_adapters._ensure_entity_schema(conn)

    assert any("CREATE TABLE IF NOT EXISTS edc_entities" in query for query in conn.cursor_obj.executed_queries)


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

    def __iter__(self):
        return iter([])


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


class FakeSchemaCursor:
    def __init__(self, column_count, index_count):
        self.results = [(column_count,), (index_count,)]
        self.executed_queries = []

    def execute(self, query, params=None):
        self.executed_queries.append(query)

    def fetchone(self):
        return self.results.pop(0)


class FakeSchemaConnection:
    def __init__(self, column_count, index_count):
        self.cursor_obj = FakeSchemaCursor(column_count, index_count)

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        return None
