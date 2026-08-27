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
                "entity_type": "node",
                "src_region": "北京市",
                "dst_region": "天津市",
                "enabled": True,
                "is_backup": True,
                "remark": "backup node",
            }
        ]
    )

    query = conn.cursor_obj.executemany_query
    payload = conn.cursor_obj.executemany_payload
    assert "is_backup" in query
    assert payload[0][9] == 1


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


def test_load_entity_mappings_reads_manual_fields(monkeypatch):
    conn = FakeSelectConnection(
        [
            {
                "edc_name": "BJ-ali-01",
                "sn": "SN1",
                "display_name": "BJ-ali-01",
                "region": "北京市",
                "cp": "阿里",
                "is_backup": 0,
                "enabled": 1,
                "remark": "人工录入",
            }
        ]
    )
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

    mappings = target.load_entity_mappings()

    assert mappings[("BJ-ali-01", "SN1")].region == "北京市"
    assert mappings[("BJ-ali-01", "SN1")].cp == "阿里"
    assert mappings[("BJ-ali-01", "SN1")].enabled is True
    assert mappings[("BJ-ali-01", "SN1")].remark == "人工录入"
    assert calls == [conn]


def test_ensure_entity_schema_creates_mapping_table():
    conn = FakeSchemaConnection(column_count=1, index_count=1)

    mysql_adapters._ensure_entity_schema(conn)

    assert any("CREATE TABLE IF NOT EXISTS edc_entities" in query for query in conn.cursor_obj.executed_queries)


def test_reconcile_traffic_metadata_updates_only_mapping_snapshot(monkeypatch):
    conn = FakeMetadataConnection()
    monkeypatch.setattr(mysql_adapters, "connect", lambda config: conn)
    monkeypatch.setattr(mysql_adapters, "_ensure_entity_schema", lambda schema_conn: None)
    target = MySQLEDCTarget(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
        )
    )
    progress = []

    summary = target.reconcile_traffic_metadata([7, 3, 7], progress.append)

    assert summary["total_entities"] == 2
    assert summary["rows_scanned"] == 6
    assert summary["rows_updated"] == 4
    assert len(progress) == 2
    assert conn.commits == 2
    assert any("NOT (t.cp <=> e.cp)" in query for query in conn.cursor_obj.queries)


def test_set_entity_enabled_updates_state_and_writes_audit(monkeypatch):
    conn = FakeStatusConnection()
    monkeypatch.setattr(mysql_adapters, "connect", lambda config: conn)
    monkeypatch.setattr(mysql_adapters, "_ensure_entity_schema", lambda schema_conn: None)
    monkeypatch.setattr(mysql_adapters, "_ensure_entity_status_audit_schema", lambda schema_conn: None)
    target = MySQLEDCTarget(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
        )
    )

    result = target.set_entity_enabled(7, False, operator="web", source_ip="127.0.0.1")

    assert result["enabled"] == 0
    assert conn.commits == 1
    assert any("UPDATE edc_entities SET enabled" in query for query in conn.cursor_obj.queries)
    assert any("INSERT INTO edc_entity_status_audit" in query for query in conn.cursor_obj.queries)


def test_set_entity_candidate_enabled_updates_status_and_writes_audit(monkeypatch):
    conn = FakeCandidateConnection()
    monkeypatch.setattr(mysql_adapters, "connect", lambda config: conn)
    monkeypatch.setattr(mysql_adapters, "_ensure_candidate_schema_for_config", lambda config: None)
    monkeypatch.setattr(mysql_adapters, "_ensure_candidate_status_audit_schema", lambda schema_conn: None)
    target = MySQLEDCTarget(
        DBConfig(
            host="localhost",
            port=3306,
            user="root",
            password="",
            database="nfa",
        )
    )

    result = target.set_entity_candidate_enabled(9, False, operator="web", source_ip="127.0.0.1")

    assert result["enabled"] == 0
    assert result["status"] == "disabled"
    assert conn.commits == 1
    assert any("UPDATE edc_entity_candidates SET enabled" in query for query in conn.cursor_obj.queries)
    assert any("INSERT INTO edc_entity_candidate_status_audit" in query for query in conn.cursor_obj.queries)


class FakeSelectConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self, dictionary=False):
        return FakeSelectCursor(self.rows)

    def close(self):
        return None


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


class FakeMetadataCursor:
    def __init__(self):
        self.queries = []
        self.rowcount = 0
        self._next = (3,)

    def execute(self, query, params=None):
        self.queries.append(query)
        if query.lstrip().upper().startswith("SELECT COUNT"):
            self._next = (3,)
        else:
            self._next = None
            self.rowcount = 2

    def fetchone(self):
        return self._next


class FakeMetadataConnection:
    def __init__(self):
        self.cursor_obj = FakeMetadataCursor()
        self.commits = 0

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


class FakeStatusCursor:
    def __init__(self):
        self.queries = []
        self.rowcount = 0
        self.entity = {
            "id": 7,
            "edc_name": "BJ-ali-01",
            "sn": "SN1",
            "display_name": "BJ-ali-01",
            "alias": None,
            "region": "北京市",
            "cp": "阿里",
            "entity_type": "node",
            "src_region": None,
            "dst_region": None,
            "is_backup": 0,
            "enabled": 1,
            "remark": "",
            "created_at": None,
            "updated_at": None,
        }
        self._next = None

    def execute(self, query, params=None):
        self.queries.append(query)
        normalized = query.lstrip().upper()
        self.rowcount = 0
        if "FOR UPDATE" in normalized:
            self._next = dict(self.entity)
        elif normalized.startswith("UPDATE EDC_ENTITIES"):
            self.entity["enabled"] = params[0]
            self.rowcount = 1
        elif normalized.startswith("INSERT INTO EDC_ENTITY_STATUS_AUDIT"):
            self.rowcount = 1
        elif normalized.startswith("SELECT ID"):
            self._next = dict(self.entity)

    def fetchone(self):
        return self._next


class FakeStatusConnection:
    def __init__(self):
        self.cursor_obj = FakeStatusCursor()
        self.commits = 0

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


class FakeCandidateCursor:
    def __init__(self):
        self.queries = []
        self.rowcount = 0
        self.candidate = {
            "id": 9,
            "edc_name": "BJ-ali-01",
            "sn": "NEW-SN",
            "enabled": 1,
            "status": "pending",
            "entity_id": None,
            "updated_at": None,
        }
        self._next = None

    def execute(self, query, params=None):
        self.queries.append(query)
        normalized = query.lstrip().upper()
        self.rowcount = 0
        if "FOR UPDATE" in normalized:
            self._next = dict(self.candidate)
        elif normalized.startswith("UPDATE EDC_ENTITY_CANDIDATES"):
            self.candidate["enabled"] = params[0]
            self.candidate["status"] = params[1]
            self.rowcount = 1
        elif normalized.startswith("INSERT INTO EDC_ENTITY_CANDIDATE_STATUS_AUDIT"):
            self.rowcount = 1
        elif normalized.startswith("SELECT ID"):
            self._next = dict(self.candidate)

    def fetchone(self):
        return self._next


class FakeCandidateConnection:
    def __init__(self):
        self.cursor_obj = FakeCandidateCursor()
        self.commits = 0

    def cursor(self, dictionary=False):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        return None


class FakeSchemaCursor:
    def __init__(self, column_count, index_count):
        self.results = [(column_count,)] * 4 + [(column_count,), (column_count,), (column_count,), (index_count,)]
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
