"""存储层：单一 SQLite 文件承载 向量索引 + FTS5 关键词索引（PRD 5.3）。

表结构（PRD 原文）：
- files：file_id / path(唯一) / name / size / mtime / file_type / mod_date / indexed_at
- slices：slice_id / file_id / modality / location / text_snippet / keywords / vector(768 f32)
- slices_fts：FTS5 trigram（自管表，2026-10-14 外挂 content 模式崩溃后改）
- vec_index：sqlite-vec vec0，cosine 距离

2026-10-16 工程化改造：
- 连接自带 RLock：全部公共方法线程安全；索引线程（批量写）与查询线程
  （搜索）按"每次方法调用"粒度交错，取代 web.py 里锁死整个 /api/index 的
  粗粒度策略——索引期间搜索不再排队。
- batch() 上下文：批量写入时抑制中间 commit（原先每文件 2 次 fsync，
  10 万文件 = 20 万次事务提交；现在每 BATCH_COMMIT_FILES 个文件一次）。
- 向量缓存升级为矩阵：all_vectors_matrix() 缓存 (sids, fids, mods, matrix)，
  L2 归一化在缓存时一次完成，每次查询只做一次矩阵乘，不再反复 np.stack。
- vec_index 写入失败自动降级：建表/插入异常时置 _vec_ok=False，向量仍存
  slices.vector BLOB，检索走全量矩阵扫描（z-score 本来就需要全量分布）。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .log import get_logger

log = get_logger("trace.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id     INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    file_type   TEXT NOT NULL,          -- pdf / image / audio / video / text / code
    modality    TEXT NOT NULL,          -- 实际索引模态
    status      TEXT NOT NULL DEFAULT 'indexed',
    indexed_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slices (
    slice_id     INTEGER PRIMARY KEY,
    file_id      INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
    modality     TEXT NOT NULL,
    location     TEXT NOT NULL,
    text_snippet TEXT NOT NULL,
    keywords     TEXT NOT NULL DEFAULT '',
    vector       BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS slices_fts USING fts5(
    text_snippet, keywords,
    tokenize='trigram'
);
CREATE TABLE IF NOT EXISTS roots (
    path TEXT PRIMARY KEY, added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_slices_file ON slices(file_id);
"""

SCHEMA_VEC = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_index USING vec0(
    slice_id INTEGER PRIMARY KEY,
    embedding FLOAT[{dim}] distance_metric=cosine
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _chunked(seq: list, size: int = config.FTS_IN_CHUNK):
    """SQL IN(...) 参数分批（规避旧 sqlite 999 变量上限）。"""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class Store:
    def __init__(self, db_path: Path | None = None, check_same_thread: bool | None = None):
        import sqlite_vec

        self.db_path = db_path or config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 线程模型：无论 check_same_thread 传什么，公共方法都在 RLock 内执行，
        # 由本类保证同一时刻只有一个线程碰连接（web 边车多线程场景）。
        self._lock = threading.RLock()
        self._batch_depth = 0
        kwargs = {} if check_same_thread is None else {"check_same_thread": check_same_thread}
        self.db = sqlite3.connect(str(self.db_path), **kwargs)
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.execute("PRAGMA foreign_keys = ON")
        # WAL：索引期读写并发不互斥（DigUp 同款：journal_mode=WAL + synchronous=NORMAL）
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA synchronous = NORMAL")
        self.db.commit()
        # 向量维度管理：支持不同模型（768 vs 2048）
        self._vector_dim = self._detect_vector_dim()
        self._vec_ok = True
        self._ensure_vec_index()
        # 矩阵缓存：首次 all_vectors_matrix() 后常驻；写操作失效
        self._matrix_cache: tuple | None = None

    # ------------------------------------------------------------------ #
    # 事务：batch 模式下抑制中间 commit
    # ------------------------------------------------------------------ #
    def _commit(self) -> None:
        if self._batch_depth == 0:
            self.db.commit()

    @contextmanager
    def batch(self):
        """批量写入上下文：块内所有写方法的 commit 被推迟到退出时统一提交。

        索引器每 BATCH_COMMIT_FILES 个文件包一层。中途崩溃丢一批（下次
        增量按 mtime 补回），换来数量级的写入吞吐。
        """
        with self._lock:
            self._batch_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    self.db.commit()

    def _invalidate_cache(self) -> None:
        self._matrix_cache = None

    def _detect_vector_dim(self) -> int:
        """检测当前向量维度（从 meta 表或默认 768）。"""
        v = self.get_meta("vector_dim")
        if v:
            return int(v)
        return 768

    def _ensure_vec_index(self) -> None:
        """确保 vec_index 表存在且维度正确。失败降级而非静默吞掉。"""
        try:
            row = self.db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='vec_index'"
            ).fetchone()
            if row:
                expected = f"FLOAT[{self._vector_dim}]"
                if expected not in row["sql"]:
                    self.db.execute("DROP TABLE IF EXISTS vec_index")
                    self.db.commit()
                    self.db.executescript(SCHEMA_VEC.format(dim=self._vector_dim))
                    self.db.commit()
            else:
                self.db.executescript(SCHEMA_VEC.format(dim=self._vector_dim))
                self.db.commit()
        except Exception as e:
            # 2026-10-16：原先 except: pass 静默吞掉，检索结果无声劣化且无人知晓。
            # 现在显式记录 + 置降级标记：写入跳过 vec_index，检索走全量矩阵扫描。
            self._vec_ok = False
            log.warning("vec_index 不可用（%s），检索将走全量矩阵扫描", e)

    def set_vector_dim(self, dim: int) -> None:
        """设置向量维度（切换模型时调用）。

        同步清空 slices.vector（旧维度 BLOB 留着会污染后续 np.stack），
        并把全部文件状态置为 metadata_only，让上层触发 reindex。
        """
        if dim == self._vector_dim:
            return
        with self._lock:
            self._vector_dim = dim
            self.set_meta("vector_dim", str(dim))
            self.db.execute("UPDATE slices SET vector=NULL")
            self.db.execute(
                "UPDATE files SET status='metadata_only' WHERE status IN ('indexed','stale')"
            )
            self._commit()
            self._invalidate_cache()
            self._ensure_vec_index()

    # ------------------------------------------------------------------ #
    # roots 管理
    # ------------------------------------------------------------------ #
    def add_root(self, path: str) -> None:
        p = str(Path(path).resolve())
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO roots(path, added_at) VALUES (?,?)", (p, _now()))
            self._commit()

    def remove_root(self, path: str) -> bool:
        p = str(Path(path).resolve())
        with self._lock:
            # 先删该 root 下的所有文件索引（files + slices + vec_index + fts）
            # ——光删 roots 留尸会让 GUI 列表里没显示但搜索还能命中
            prefix = p if p.endswith(("/", "\\")) else p + "/"
            fids = [r["file_id"] for r in self.db.execute(
                "SELECT file_id FROM files WHERE path LIKE ?", (prefix + "%",))]
            if fids:
                for chunk in _chunked(fids):
                    marks = ",".join("?" * len(chunk))
                    self.db.execute(f"DELETE FROM slices WHERE file_id IN ({marks})", chunk)
                    self.db.execute(f"DELETE FROM files WHERE file_id IN ({marks})", chunk)
                self._invalidate_cache()
            cur = self.db.execute("DELETE FROM roots WHERE path=?", (p,))
            self._commit()
            return cur.rowcount > 0

    def list_roots(self) -> list[str]:
        with self._lock:
            return [r["path"] for r in self.db.execute("SELECT path FROM roots ORDER BY added_at")]

    # ------------------------------------------------------------------ #
    # 文件 / 切片写入
    # ------------------------------------------------------------------ #
    def get_file(self, path: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def file_states(self, root_prefix: str) -> dict[str, tuple[int, float]]:
        """root 前缀下全部 {path: (size, mtime)}——watcher 增量比对用。"""
        prefix = root_prefix if root_prefix.endswith(("\\", "/")) else root_prefix + "\\"
        out: dict[str, tuple[int, float]] = {}
        with self._lock:
            for r in self.db.execute(
                "SELECT path, size, mtime FROM files WHERE path LIKE ?", (prefix + "%",)
            ):
                out[r["path"]] = (r["size"], r["mtime"])
        return out

    def upsert_file(self, path: str, size: int, mtime: float, file_type: str,
                    modality: str, status: str) -> int:
        """插入或更新文件记录，返回 file_id。

        两步走（先 SELECT 后 INSERT/UPDATE）：ON CONFLICT DO UPDATE 的
        lastrowid 在 UPDATE 分支不可靠，曾导致外键约束失败（2026-10-14 修）。
        """
        with self._lock:
            row = self.db.execute("SELECT file_id FROM files WHERE path=?", (path,)).fetchone()
            if row:
                self.db.execute(
                    """UPDATE files SET name=?, size=?, mtime=?, file_type=?, modality=?, status=?, indexed_at=?
                       WHERE file_id=?""",
                    (Path(path).name, size, mtime, file_type, modality, status, _now(), row["file_id"]),
                )
                self._commit()
                return row["file_id"]
            cur = self.db.execute(
                """INSERT INTO files(path, name, size, mtime, file_type, modality, status, indexed_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (path, Path(path).name, size, mtime, file_type, modality, status, _now()),
            )
            self._commit()
            return cur.lastrowid

    def replace_slices(self, file_id: int, slices: list[dict]) -> None:
        """整文件重建切片：先删旧（FTS 自管表按 rowid 删 + vec），再插新。

        vec_index 写入失败自动降级（只写主表 + FTS，向量仍在 slices.vector）。
        """
        import sqlite_vec
        with self._lock:
            self._invalidate_cache()  # 写后清缓存

            # ---- 1. 批量删旧 ----
            old_ids = self._last_slice_ids(file_id)
            if old_ids:
                for chunk in _chunked(old_ids):
                    marks = ",".join("?" * len(chunk))
                    if self._vec_ok:
                        try:
                            self.db.execute(f"DELETE FROM vec_index WHERE slice_id IN ({marks})", chunk)
                        except sqlite3.Error:
                            self._vec_ok = False
                    self.db.execute(f"DELETE FROM slices_fts WHERE rowid IN ({marks})", chunk)
            self.db.execute("DELETE FROM slices WHERE file_id=?", (file_id,))

            if not slices:
                self._commit()
                return

            # ---- 2. 插主表（逐条拿 lastrowid 串 vec/FTS）----
            vec_rows: list[tuple[int, bytes]] = []
            fts_rows: list[tuple[int, str, str]] = []
            for s in slices:
                vec = s.get("vector")
                blob = sqlite_vec.serialize_float32(vec.tolist()) if vec is not None else None
                cur = self.db.execute(
                    "INSERT INTO slices(file_id, modality, location, text_snippet, keywords, vector) VALUES (?,?,?,?,?,?)",
                    (file_id, s["modality"], s["location"], s["text"], s.get("keywords", ""), blob),
                )
                sid = cur.lastrowid
                if blob is not None:
                    vec_rows.append((sid, blob))
                fts_rows.append((sid, s["text"], s.get("keywords", "")))

            # ---- 3. 批量插 vec_index + slices_fts（一次 IPC）----
            if self._vec_ok and vec_rows:
                try:
                    self.db.executemany(
                        "INSERT INTO vec_index(slice_id, embedding) VALUES (?,?)", vec_rows)
                except sqlite3.Error as e:
                    self._vec_ok = False
                    log.warning("vec_index 写入失败（%s），降级为仅 BLOB 存储", e)
            if fts_rows:
                self.db.executemany(
                    "INSERT INTO slices_fts(rowid, text_snippet, keywords) VALUES (?,?,?)", fts_rows)
            self._commit()

    def _last_slice_ids(self, file_id: int) -> list[int]:
        return [r["slice_id"] for r in self.db.execute(
            "SELECT slice_id FROM slices WHERE file_id=? ORDER BY slice_id", (file_id,))]

    def delete_file(self, path: str) -> bool:
        with self._lock:
            row = self.get_file(path)
            if not row:
                return False
            for sid in self._last_slice_ids(row["file_id"]):
                if self._vec_ok:
                    try:
                        self.db.execute("DELETE FROM vec_index WHERE slice_id=?", (sid,))
                    except sqlite3.Error:
                        self._vec_ok = False
                self.db.execute("DELETE FROM slices_fts WHERE rowid=?", (sid,))
            self.db.execute("DELETE FROM slices WHERE file_id=?", (row["file_id"],))
            self.db.execute("DELETE FROM files WHERE file_id=?", (row["file_id"],))
            self._invalidate_cache()
            self._commit()
            return True

    def delete_files(self, paths: list[str]) -> int:
        """批量删除（watcher 对账用），返回成功删除数。"""
        n = 0
        with self._lock:
            with self.batch():
                for p in paths:
                    if self.delete_file(p):
                        n += 1
        return n

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def all_vectors_matrix(self) -> tuple:
        """全量向量矩阵：(sids ndarray, fids ndarray, mods list[str], matrix[n, dim])。

        matrix 已 L2 归一化（写库向量本就归一化，此处再保险一次）。
        首次拉全量后缓存；写操作通过 _invalidate_cache() 清掉。
        z-score 按模态分组归一，需要全量分布，故保留全量扫描路线
        （sqlite-vec KNN 只给 top-k，给不了分布）——矩阵缓存把每次查询
        的成本降为一次矩阵乘。
        """
        if self._matrix_cache is not None:
            return self._matrix_cache
        import numpy as np

        with self._lock:
            rows = self.db.execute(
                """SELECT s.slice_id, s.file_id, s.modality, s.vector
                   FROM slices s WHERE s.vector IS NOT NULL"""
            ).fetchall()
        if not rows:
            cache = (np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
                     [], np.zeros((0, self._vector_dim), dtype=np.float32))
            self._matrix_cache = cache
            return cache
        sids = np.empty(len(rows), dtype=np.int64)
        fids = np.empty(len(rows), dtype=np.int64)
        mods: list[str] = []
        mat = np.empty((len(rows), self._vector_dim), dtype=np.float32)
        for i, r in enumerate(rows):
            sids[i] = r["slice_id"]
            fids[i] = r["file_id"]
            mods.append(r["modality"])
            mat[i] = np.frombuffer(r["vector"], dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        mat = mat / np.maximum(norms, 1e-9)
        cache = (sids, fids, mods, mat)
        self._matrix_cache = cache
        return cache

    def fts_short_terms(self, shorts: list[str], limit: int = 2000) -> dict[int, int]:
        """短词（<3 字符，trigram 匹配不到）走 LIKE 兜底。

        返回 {slice_id: 命中的短词数}。中文 2 字词（"睡眠"、"发票"）是主要场景。
        """
        hits: dict[int, int] = {}
        with self._lock:
            for t in shorts:
                esc = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                try:
                    rows = self.db.execute(
                        """SELECT slice_id FROM slices
                           WHERE text_snippet LIKE ? ESCAPE '\\' OR keywords LIKE ? ESCAPE '\\'
                           LIMIT ?""",
                        (f"%{esc}%", f"%{esc}%", limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                for r in rows:
                    hits[r["slice_id"]] = hits.get(r["slice_id"], 0) + 1
        return hits

    def fts_per_pattern(self, patterns: list[str], limit: int = 400) -> list[dict[int, float]]:
        """每个 FTS pattern 单独查询，返回与 patterns 等长的 [{slice_id: bm25}]。

        DigUp 同款：整句作为 phrase 几乎零命中（trigram 要求连续出现），
        必须逐词查询再算 coverage（查询词在文件中的命中比例）。
        bm25 第二列（keywords）权重 3.0：文件名/目录名命中更值钱。
        """
        per: list[dict[int, float]] = []
        with self._lock:
            for p in patterns:
                try:
                    rows = self.db.execute(
                        """SELECT rowid, bm25(slices_fts, 1.0, 3.0) AS rank
                           FROM slices_fts WHERE slices_fts MATCH ? ORDER BY rank LIMIT ?""",
                        (p, limit),
                    ).fetchall()
                    per.append({r["rowid"]: float(r["rank"]) for r in rows})
                except sqlite3.OperationalError:
                    per.append({})
        return per

    def knn(self, query_vec, k: int) -> list[tuple[int, float]]:
        """返回 [(slice_id, cosine_distance)]，distance ∈ [0, 2]。"""
        import sqlite_vec

        blob = sqlite_vec.serialize_float32(query_vec.tolist())
        with self._lock:
            try:
                rows = self.db.execute(
                    "SELECT slice_id, distance FROM vec_index WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (blob, k),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = self.db.execute(
                    "SELECT slice_id, distance FROM vec_index WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
                    (blob, k),
                ).fetchall()
        return [(r["slice_id"], float(r["distance"])) for r in rows]

    def fts_rank(self, query: str, limit: int = 200) -> dict[int, float]:
        """FTS5 trigram 匹配，返回 {slice_id: bm25}（越小越好，负数）。"""
        q = '"' + query.replace('"', '""') + '"'
        with self._lock:
            try:
                rows = self.db.execute(
                    "SELECT rowid, bm25(slices_fts) AS rank FROM slices_fts WHERE slices_fts MATCH ? ORDER BY rank LIMIT ?",
                    (q, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
            return {r["rowid"]: float(r["rank"]) for r in rows}

    def slice_text_by_ids(self, slice_ids: list[int]) -> dict[int, tuple[int, str]]:
        """轻量取 (file_id, text_snippet)：关键词通道组装 coverage 用。"""
        out: dict[int, tuple[int, str]] = {}
        if not slice_ids:
            return out
        with self._lock:
            for chunk in _chunked(list(slice_ids)):
                marks = ",".join("?" * len(chunk))
                for r in self.db.execute(
                    f"SELECT slice_id, file_id, text_snippet FROM slices WHERE slice_id IN ({marks})",
                    chunk,
                ):
                    out[r["slice_id"]] = (r["file_id"], r["text_snippet"] or "")
        return out

    def slices_by_ids(self, slice_ids: list[int]) -> dict[int, sqlite3.Row]:
        out: dict[int, sqlite3.Row] = {}
        if not slice_ids:
            return out
        with self._lock:
            for chunk in _chunked(list(slice_ids)):
                marks = ",".join("?" * len(chunk))
                rows = self.db.execute(
                    f"""SELECT s.*, f.path, f.name, f.mtime, f.file_type, f.status
                        FROM slices s JOIN files f ON f.file_id = s.file_id
                        WHERE s.slice_id IN ({marks})""",
                    chunk,
                ).fetchall()
                for r in rows:
                    out[r["slice_id"]] = r
        return out

    def files_by_ids(self, file_ids) -> dict[int, sqlite3.Row]:
        """按 file_id 批量取文件元信息（空集安全，分批 IN）。"""
        ids = list(file_ids)
        out: dict[int, sqlite3.Row] = {}
        if not ids:
            return out
        with self._lock:
            for chunk in _chunked(ids):
                marks = ",".join("?" * len(chunk))
                rows = self.db.execute(
                    f"SELECT file_id, path, name, file_type, mtime FROM files WHERE file_id IN ({marks})",
                    chunk,
                ).fetchall()
                for r in rows:
                    out[r["file_id"]] = r
        return out

    def paths_under(self, root_prefix: str) -> list[str]:
        """root 前缀下全部已索引路径——索引对账用（替代直接摸 store.db）。"""
        with self._lock:
            return [r["path"] for r in self.db.execute(
                "SELECT path FROM files WHERE path LIKE ?", (root_prefix + "%",))]

    def frame_locations(self, file_id: int) -> list[str]:
        """某视频文件的全部帧 location（视频附近帧条用）。"""
        with self._lock:
            return [r["location"] for r in self.db.execute(
                """SELECT location FROM slices
                   WHERE file_id=? AND modality='video_frame'
                   AND location IS NOT NULL AND location != ''
                   ORDER BY slice_id""",
                (file_id,))]

    # ------------------------------------------------------------------ #
    # meta（索引指纹等）
    # ------------------------------------------------------------------ #
    def get_meta(self, k: str) -> str | None:
        with self._lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return r["v"] if r else None

    def set_meta(self, k: str, v: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO meta(k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
            self._commit()

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def stats(self) -> dict:
        with self._lock:
            def one(sql):
                return self.db.execute(sql).fetchone()[0]
            t0 = time.time()
            out = {
                "roots": one("SELECT COUNT(*) FROM roots"),
                "files": one("SELECT COUNT(*) FROM files"),
                "files_indexed": one("SELECT COUNT(*) FROM files WHERE status='indexed'"),
                "files_metadata_only": one("SELECT COUNT(*) FROM files WHERE status='metadata_only'"),
                "slices": one("SELECT COUNT(*) FROM slices"),
                "db_size_mb": round(self.db_path.stat().st_size / 1e6, 2) if self.db_path.exists() else 0,
            }
            out["stats_ms"] = round((time.time() - t0) * 1000, 1)
            return out

    def close(self):
        with self._lock:
            self.db.close()
