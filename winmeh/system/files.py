"""Find files by meaning-ish keywords ("my wedding photo").

Strategy (fastest first):
  1. Windows Search index (SystemIndex) over OLE DB - already indexed by Windows,
     matches file names, folder names, tags, titles and Photos-app keywords.
  2. WinMeh's own SQLite index of the user's folders (built in the background),
     so it still works when Windows Search is disabled.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import IS_WINDOWS, data_dir

KINDS = {
    "picture": {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff",
                ".raw", ".cr2", ".cr3", ".nef", ".arw", ".dng"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm", ".mts", ".3gp"},
    "music": {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".wma"},
    "document": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".md"},
}
KIND_WORDS = {
    "picture": ["photo", "photos", "picture", "pictures", "pic", "pics", "image", "images", "selfie", "album"],
    "video": ["video", "videos", "clip", "movie", "recording", "footage"],
    "music": ["song", "songs", "music", "track", "audio"],
    "document": ["document", "doc", "docs", "pdf", "file", "report", "resume", "cv", "invoice", "spreadsheet"],
}
# Words people use for the same event. Extend freely.
SYNONYMS = {
    "wedding": ["wedding", "marriage", "bride", "groom", "nikah", "shaadi", "shadi", "biye", "holud", "reception", "vows"],
    "birthday": ["birthday", "bday", "b-day", "party"],
    "vacation": ["vacation", "holiday", "trip", "travel", "tour"],
    "graduation": ["graduation", "convocation", "grad"],
    "baby": ["baby", "newborn", "infant"],
    "resume": ["resume", "cv", "curriculum"],
}
STOP = {"my", "the", "a", "an", "of", "where", "is", "are", "find", "locate", "search", "for", "show", "me",
        "get", "open", "whats", "wher", "whr", "were", "put", "saved", "keep", "kept", "did", "what's", "where's", "wheres", "file", "files", "folder", "from", "our", "in", "on",
        "i", "can", "you", "please", "pc", "computer", "machine", "all", "some", "any", "that", "with", "to"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "appdata", "$recycle.bin", "windows", "program files",
             "program files (x86)", "programdata", ".cache", "venv", ".venv", "site-packages", "steamapps"}


@dataclass
class Hit:
    path: str
    score: float
    modified: float = 0.0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def parse_query(text: str) -> tuple[list[list[str]], str | None]:
    """'where is my wedding photo' -> ([['wedding','marriage',...]], 'picture')."""
    words = re.findall(r"[\w\-']+", text.lower())
    kind = None
    terms: list[list[str]] = []
    for w in words:
        hit_kind = next((k for k, ws in KIND_WORDS.items() if w in ws), None)
        if hit_kind:
            kind = kind or hit_kind
            continue
        if w in STOP or len(w) < 2:
            continue
        group = next((syn for key, syn in SYNONYMS.items() if w == key or w in syn), [w])
        terms.append(group)
    return terms, kind


# ---------------------------------------------------------------- Windows Search
def search_windows_index(terms: list[list[str]], kind: str | None, limit: int = 25) -> list[Hit] | None:
    """Returns None if Windows Search is unavailable (so the caller can fall back)."""
    if not IS_WINDOWS or not terms:
        return None
    try:
        import win32com.client  # type: ignore
    except ImportError:
        return None

    def esc(s: str) -> str:
        return re.sub(r"[^\w\- ]", "", s)

    clauses = []
    for group in terms:
        ors = []
        for t in group:
            t = esc(t)
            ors.append(f"System.ItemPathDisplay LIKE '%{t}%'")
            ors.append(f"CONTAINS(System.Keywords, '\"{t}*\"')")
            ors.append(f"CONTAINS(System.Title, '\"{t}*\"')")
        clauses.append("(" + " OR ".join(ors) + ")")
    where = " AND ".join(clauses)
    if kind:
        kind_map = {"picture": "picture", "video": "video", "music": "music", "document": "document"}
        where += f" AND System.Kind = '{kind_map[kind]}'"
    sql = (f"SELECT TOP {int(limit)} System.ItemPathDisplay, System.DateModified FROM SYSTEMINDEX "
           f"WHERE SCOPE='file:' AND {where} ORDER BY System.DateModified DESC")
    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(sql, conn)
        hits = []
        while not rs.EOF:
            hits.append(Hit(str(rs.Fields.Item("System.ItemPathDisplay").Value), 1.0))
            rs.MoveNext()
        rs.Close()
        conn.Close()
        return hits
    except Exception:  # COM errors: service stopped, provider missing, etc.
        return None


# ---------------------------------------------------------------- own index
class FileIndex:
    """Filename/folder index in SQLite. Indexing ~200k files takes ~20-60 s on an SSD."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or data_dir() / "files.db"
        self._lock = threading.Lock()
        self.indexing = False
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, lower TEXT, ext TEXT, mtime REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def last_built(self) -> float:
        with self._conn() as c:
            row = c.execute("SELECT v FROM meta WHERE k='built'").fetchone()
            return float(row[0]) if row else 0.0

    def build(self, roots: list[str], max_files: int = 500_000) -> int:
        if self.indexing:
            return 0
        self.indexing = True
        try:
            rows, n = [], 0
            # Build into a side table and swap at the end, so searches keep working mid-rebuild.
            with self._lock, self._conn() as c:
                c.execute("DROP TABLE IF EXISTS files_new")
                c.execute("CREATE TABLE files_new (path TEXT PRIMARY KEY, lower TEXT, ext TEXT, mtime REAL)")
                for root in roots:
                    for path, mtime in _walk(root):
                        # match only below the root, so "C:\\Users\\Wedding Planner Ltd\\" can't match everything
                        rel = os.path.relpath(path, root).lower()
                        rows.append((path, rel, os.path.splitext(path)[1].lower(), mtime))
                        n += 1
                        if len(rows) >= 5000:
                            c.executemany("INSERT OR REPLACE INTO files_new VALUES (?,?,?,?)", rows)
                            rows.clear()
                        if n >= max_files:
                            break
                c.executemany("INSERT OR REPLACE INTO files_new VALUES (?,?,?,?)", rows)
                c.execute("DROP TABLE files")
                c.execute("ALTER TABLE files_new RENAME TO files")
                c.execute("INSERT OR REPLACE INTO meta VALUES ('built', ?)", (str(time.time()),))
            return n
        finally:
            self.indexing = False

    def search(self, terms: list[list[str]], kind: str | None, limit: int = 25) -> list[Hit]:
        if not terms:
            return []
        # Match ANY term, then keep only rows matching the most term-groups: robust to
        # stray words ("wedding photo from last year") without returning noise.
        flat = [t for g in terms for t in g]
        sql = "SELECT path, mtime, lower FROM files WHERE (" + " OR ".join("lower LIKE ?" for _ in flat) + ")"
        args: list = [f"%{t}%" for t in flat]
        if kind:
            exts = sorted(KINDS[kind])
            sql += f" AND ext IN ({','.join('?' * len(exts))})"
            args += exts
        sql += " ORDER BY mtime DESC LIMIT 2000"
        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        matched = {p: _groups_matched(rel, terms) for p, _, rel in rows}
        best = max(matched.values(), default=0)
        hits = [Hit(p, _score(rel, terms), m) for p, m, rel in rows if matched[p] == best]
        hits.sort(key=lambda h: (-h.score, -h.modified))
        return _collapse_folders(hits, limit)


def _walk(root: str):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() not in SKIP_DIRS and not e.name.startswith("."):
                                stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            yield e.path, e.stat(follow_symlinks=False).st_mtime
                    except OSError:
                        continue
        except OSError:
            continue


def _groups_matched(path: str, terms: list[list[str]]) -> int:
    low = path.lower()
    return sum(1 for g in terms if any(t in low for t in g))


def _score(path: str, terms: list[list[str]]) -> float:
    low = path.lower()
    name = os.path.basename(low)
    s = 0.0
    for group in terms:
        if any(t in name for t in group):
            s += 2.0          # term in the filename
        elif any(t in low for t in group):
            s += 1.0          # term in a parent folder ("Wedding 2021/IMG_0042.jpg")
    return s


def _collapse_folders(hits: list[Hit], limit: int) -> list[Hit]:
    """If 300 photos sit in 'Wedding/', show a few and let the user open the folder."""
    per_dir: dict[str, int] = {}
    out = []
    for h in hits:
        d = os.path.dirname(h.path)
        per_dir[d] = per_dir.get(d, 0) + 1
        if per_dir[d] <= 3:
            out.append(h)
        if len(out) >= limit:
            break
    return out


def group_by_folder(hits: list[Hit]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for h in hits:
        d = os.path.dirname(h.path)
        counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def search(text: str, index: FileIndex | None, limit: int = 25) -> tuple[list[Hit], str]:
    terms, kind = parse_query(text)
    if not terms:
        return [], "none"
    hits = search_windows_index(terms, kind, limit)
    if hits:
        return hits, "windows-search"
    if index is not None:
        return index.search(terms, kind, limit), "winmeh-index"
    return [], "none"
