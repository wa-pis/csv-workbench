"""Disk-backed immutable edit generations and constant-size Undo snapshots."""

import json
import sqlite3
from collections.abc import MutableMapping
from contextlib import closing, contextmanager
from itertools import groupby, islice
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from weakref import finalize

MISSING = object()
NEW_ROW = 1 << 62


class EditStore:
    def __init__(self):
        self.scratch = TemporaryDirectory(prefix="csv-edits-")
        self.path = Path(self.scratch.name) / "edits.sqlite"
        self.lock = RLock()
        self.cache_lock = RLock()
        self.db = self.connect()
        self._finalizer = finalize(self, self._cleanup, self.db, self.scratch)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE versions (id INTEGER PRIMARY KEY, count INTEGER NOT NULL, columns TEXT NOT NULL DEFAULT '[]', structural INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE patches (
                version INTEGER, rid INTEGER, col INTEGER, val TEXT, missing INTEGER NOT NULL,
                PRIMARY KEY(version, rid, col)
            ) WITHOUT ROWID;
            CREATE INDEX address ON patches(rid, col, version);
        """)

    def connect(self):
        db = sqlite3.connect(self.path, check_same_thread=False, timeout=60)
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        return db

    @contextmanager
    def connection(self, cancel=None):
        try:
            with closing(self.connect()) as db:
                if cancel is not None:
                    db.set_progress_handler(lambda: int(cancel.cancelled), 10000)
                yield db
        except sqlite3.Error as exc:
            if cancel is not None:
                cancel.check()
            raise OSError(f"Edit database: {exc}") from exc

    @staticmethod
    def _cleanup(db, scratch):
        # Windows cannot remove the directory while SQLite still has its files open.
        db.close()
        scratch.cleanup()

    def backup(self, destination, check=lambda: None):
        with self.connection() as source, closing(sqlite3.connect(destination)) as output:
            def progress(*_):
                check()
            source.backup(output, pages=256, progress=progress)
            check()

    def rows(self, versions, *, cancel=None, ordered=False):
        if not versions:
            return
        with self.connection(cancel) as db:
            if len(versions) == 1:
                sql = "SELECT rid,col,val,missing FROM patches WHERE version=?"
                params = versions
            else:
                sql = """
                    SELECT p.rid,p.col,p.val,p.missing FROM patches p
                    JOIN (SELECT rid,col,max(version) AS version FROM patches
                          WHERE version IN (SELECT value FROM json_each(?)) GROUP BY rid,col) latest
                    USING(version,rid,col)
                """
                params = (json.dumps(versions),)
            if ordered:
                sql += " ORDER BY rid,col"
            cursor = db.execute(sql, params)
            while batch := cursor.fetchmany(2048):
                if cancel is not None:
                    cancel.check()
                yield from batch


class DiskEdits(MutableMapping):
    def __init__(self, store=None, versions=(), count=0):
        self.store = store or EditStore()
        self.versions = tuple(versions)
        self.count = count
        self.writable = False

    def copy(self):
        self.writable = False
        return DiskEdits(self.store, self.versions, self.count)

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        if not self.versions:
            raise KeyError(key)
        with self.store.lock:
            row = self.store.db.execute("""
                SELECT val,missing FROM patches WHERE rid=? AND col=?
                AND version IN (SELECT value FROM json_each(?)) ORDER BY version DESC LIMIT 1
            """, (*key, json.dumps(self.versions))).fetchone()
        if row is None or row[1]:
            raise KeyError(key)
        return row[0]

    def __iter__(self):
        for row, col, _, missing in self.store.rows(self.versions):
            if not missing:
                yield row, col

    def items(self):
        for row, col, value, missing in self.store.rows(self.versions):
            if not missing:
                yield (row, col), value

    def iter_rows(self, *, cancel):
        """Stream a frozen snapshot's active patches in source-row order."""
        snapshot = self.copy()

        def rows():
            cancel.check()
            with closing(snapshot.store.rows(snapshot.versions, cancel=cancel, ordered=True)) as patches:
                for rid, group in groupby(patches, key=lambda patch: patch[0]):
                    cancel.check()
                    cells = {col: value for _, col, value, missing in group if not missing}
                    cancel.check()
                    if cells:
                        yield rid, cells

        return rows()

    def __eq__(self, other):
        if isinstance(other, DiskEdits):
            return self.store is other.store and self.versions == other.versions
        return super().__eq__(other)

    def _version(self, db=None):
        db = db or self.store.db
        if not self.writable:
            cursor = db.execute("INSERT INTO versions(count) VALUES (?)", (self.count,))
            self.versions += (cursor.lastrowid,)
            self.writable = True
        return self.versions[-1]

    def _write(self, key, value):
        original = self.versions, self.count, self.writable
        try:
            with self.store.lock, self.store.db:
                exists = key in self
                if value is MISSING and not exists:
                    raise KeyError(key)
                version = self._version()
                self.store.db.execute("INSERT OR REPLACE INTO patches VALUES (?,?,?,?,?)",
                                      (version, *key, None if value is MISSING else value, value is MISSING))
                self.count += (-1 if value is MISSING else int(not exists))
                columns, structural = self.store.db.execute('SELECT columns,structural FROM versions WHERE id=?', (version,)).fetchone()
                self.store.db.execute("UPDATE versions SET count=?,columns=?,structural=? WHERE id=?",
                                      (self.count, json.dumps(sorted(set(json.loads(columns)) | {key[1]})),
                                       structural or key[0] >= NEW_ROW or key[1] == -1, version))
        except sqlite3.Error as exc:
            self.versions, self.count, self.writable = original
            raise OSError(f"Edit database: {exc}") from exc

    def __setitem__(self, key, value):
        self._write(key, value)

    def __delitem__(self, key):
        self._write(key, MISSING)

    def clear(self):
        self.versions, self.count, self.writable = (), 0, False

    def updated(self, changes, *, cancel):
        """Build a new generation transactionally; never modify the caller's snapshot."""
        result = self.copy()
        with self.store.connection(cancel) as db, db:
            version = result._version(db)
            columns, structural, written = set(), False, 0
            rows = ((version, *key, None if value is MISSING else value, value is MISSING)
                    for key, value in changes)
            while batch := list(islice(rows, 2048)):
                cancel.check()
                written += len(batch)
                columns.update(row[2] for row in batch)
                structural = structural or any(row[1] >= NEW_ROW or row[2] == -1 for row in batch)
                db.execute("""
                    INSERT OR REPLACE INTO patches
                    SELECT value->>0,value->>1,value->>2,value->>3,value->>4 FROM json_each(?)
                """, (json.dumps(batch, ensure_ascii=False),))
            cancel.check()
            if not self.count and len(result.versions) == 1:
                count = db.execute(
                    "SELECT count(*) FROM patches WHERE version=? AND NOT missing", (version,),
                ).fetchone()[0]
            elif written <= 10000:
                count = self.count + db.execute("""
                    SELECT coalesce(sum((NOT n.missing) - coalesce((
                        SELECT NOT p.missing FROM patches p WHERE p.rid=n.rid AND p.col=n.col
                        AND p.version IN (SELECT value FROM json_each(?))
                        ORDER BY p.version DESC LIMIT 1
                    ),0)),0) FROM patches n WHERE n.version=?
                """, (json.dumps(self.versions), version)).fetchone()[0]
            else:
                count = db.execute("""
                    SELECT count(*) FROM patches p JOIN (
                        SELECT rid,col,max(version) AS version FROM patches
                        WHERE version IN (SELECT value FROM json_each(?)) GROUP BY rid,col
                    ) latest USING(version,rid,col) WHERE NOT p.missing
                """, (json.dumps(result.versions),)).fetchone()[0]
            cancel.check()
            db.execute("UPDATE versions SET count=?,columns=?,structural=? WHERE id=?",
                       (count, json.dumps(sorted(columns)), structural, version))
        result.count, result.writable = count, False
        return result

    @property
    def columns(self):
        with self.store.lock:
            return {col for row in self.store.db.execute(
                "SELECT columns FROM versions WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(self.versions),),
            ) for col in json.loads(row[0])}

    @property
    def structural(self):
        with self.store.lock:
            return self.store.db.execute("""
                SELECT 1 FROM versions WHERE id IN (SELECT value FROM json_each(?))
                AND structural LIMIT 1
            """, (json.dumps(self.versions),)).fetchone() is not None

    def load(self, connection, cancel):
        """Bridge SQLite to DuckDB through bounded Parquet batches."""
        self.writable = False
        import polars as pl

        paths = []
        for version in self.versions:
            cancel.check()
            directory = Path(self.store.scratch.name) / f"{version}.parquet"
            with self.store.cache_lock:
                cancel.check()
                if not directory.exists():
                    with TemporaryDirectory(dir=self.store.scratch.name) as temporary:
                        root = Path(temporary)
                        with self.store.connection(cancel) as db:
                            cursor = db.execute('SELECT rid,col,val,missing FROM patches WHERE version=?', (version,))
                            part = 0
                            while batch := cursor.fetchmany(32768):
                                cancel.check()
                                frame = pl.DataFrame(batch, orient='row', schema={
                                    'rid': pl.Int64, 'col': pl.Int32, 'val': pl.String, 'missing': pl.Int32,
                                }).with_columns(pl.lit(version, dtype=pl.Int64).alias('generation'))
                                frame.write_parquet(root / f'{part}.parquet')
                                part += 1
                        cancel.check()
                        root.rename(directory)
            paths.extend(str(path) for path in directory.glob('*.parquet'))
        if not paths:
            connection.execute('CREATE TEMP TABLE cell_edits (rid BIGINT,col INTEGER,val VARCHAR)')
            return
        sql = 'SELECT * FROM read_parquet(?)'
        if len(self.versions) > 1:
            sql += ' QUALIFY row_number() OVER (PARTITION BY rid,col ORDER BY generation DESC)=1'
        cancel.check()
        connection.execute(f"CREATE TEMP TABLE cell_edits AS SELECT rid,col,val FROM ({sql}) WHERE NOT missing", [paths])

    def descriptor(self):
        return {'versions': list(self.versions), 'count': self.count}


class DiskBatch:
    def __init__(self, before, after, count):
        self.before, self.after, self.count = before.copy(), after.copy(), count

    def __len__(self):
        return self.count

    @property
    def structural(self):
        return self.before.structural or self.after.structural

    def __iter__(self):
        for key, value in self.after.items():
            before = self.before.get(key, MISSING)
            if before != value:
                yield key, before, value
        for key, value in self.before.items():
            if key not in self.after:
                yield key, value, MISSING

    def __getitem__(self, index):
        return next(islice(iter(self), index, index + 1))
