"""Private, restart-safe learning documents. Never stores screenshot bytes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def quoted(value):
    return "\n".join("> " + line for line in str(value).splitlines()) or ">"


class LearningStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("LEARNING_DATA_DIR") or
                         Path(__file__).resolve().parent.parent / ".learning-data")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.db = sqlite3.connect(self.root / "history.sqlite3")
        os.chmod(self.root / "history.sqlite3", 0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.documents = {}
        for sid, data in self.db.execute("SELECT id,data FROM sessions"):
            doc = json.loads(data)
            doc["capturing"] = False
            doc["generation"] += 1
            doc["lease_until"] = 0
            doc["current"] = []
            doc["analyse_on_stop"] = False
            if doc["analysis"]["running"]:
                doc["analysis"]["error"] = "前回の解説が中断されました。停止中に再試行できます。"
            doc["analysis"]["running"] = False
            doc["analysis"]["stop_requested"] = False
            for entry in doc["entries"]:
                if entry["analysis_status"] == "running":
                    entry["analysis_status"] = "pending"
            self.documents[sid] = doc
        for doc in self.documents.values():
            self.save(doc)

    def save(self, doc):
        self.documents[doc["id"]] = doc
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)",
                            (doc["id"], json.dumps(doc, ensure_ascii=False)))

    def paths(self, doc):
        name = doc["filename"]
        if not name or Path(name).name != name or name in {".", ".."} or "\\" in name:
            raise ValueError("unsafe record filename")
        paths = self.root / (name + ".md"), self.root / (name + ".user-notes.md")
        if any(p.is_symlink() for p in paths):
            raise ValueError("record file is a symlink")
        return paths

    def filename(self, title, now, sid):
        slug = re.sub(r"[^\w-]+", "-", title, flags=re.UNICODE).strip("-_")[:70] or "viewing"
        return datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + slug + "-" + sid

    def export(self, doc):
        output, personal = self.paths(doc)
        title = doc["title"].replace("\n", " ").replace("\r", " ")
        lines = ["# " + title, "", "日時: " + timestamp(doc["created_at"]),
                 "", "出典: " + doc["source_url"], "", "## 視聴メモ", "", quoted(doc["user_context"]),
                 "", "## 原文と翻訳", ""]
        for entry in doc["entries"]:
            label = entry["kind"] + (" / " + entry["speaker"] if entry["speaker"] else "")
            lines += ["### " + timestamp(entry["captured_at"]) + " · " + label,
                      "", "ID: " + entry["id"], "", quoted(entry["source"]), "",
                      quoted(entry["translation"]), ""]
            if entry["uncertain"] or entry["note"]:
                lines += ["**要確認** " + entry["note"], ""]
        lines += ["## 学習メモ（編集可能な下書き）", ""]
        by_id = {entry["id"]: entry for entry in doc["entries"]}
        for note in doc["notes"]:
            source = by_id[note["entry_id"]]
            lines += ["### " + note["expression"].replace("\n", " "), "",
                      "出典ID: " + note["entry_id"] + " / " + timestamp(source["captured_at"]),
                      "", quoted(note["quote"]), "", quoted(note["meaning_ja"]), "",
                      quoted(note["context_ja"]), "", quoted(note["register_ja"]), "",
                      "状態: " + ("ユーザー編集済み" if note["edited"] else "下書き") +
                      (" · 文脈／意味を要確認" if note["uncertain"] else ""), ""]
        counts = {state: sum(e["analysis_status"] == state for e in doc["entries"])
                  for state in ("done", "pending", "failed")}
        lines += ["---", f"学習メモ: {len(doc['notes'])}件 / 解析済み: {counts['done']}件 / "
                  f"未処理: {counts['pending']}件 / 失敗: {counts['failed']}件", "",
                  "取得中は原文・訳を保存し、取得停止後に未処理の学習メモを生成します。", "",
                  "生成ファイルは保存時に更新されます。学習メモの編集はパネルで行ってください。", ""]
        temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with open(temporary, "x", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                stream.write("\n".join(lines))
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)

    def trash(self, doc):
        """Archive a complete recoverable document before removing the active row."""
        paths = self.paths(doc)
        trash = self.root / ".trash"
        if trash.is_symlink():
            raise ValueError("trash is a symlink")
        trash.mkdir(mode=0o700, exist_ok=True)
        os.chmod(trash, 0o700)
        destination = trash / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex)
        destination.mkdir(mode=0o700)
        archive = destination / "record.json"
        with archive.open("x", encoding="utf-8") as stream:
            os.chmod(archive, 0o600)
            json.dump({"format": 1, "document": doc, "original_files": [p.name for p in paths]},
                      stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        for path in paths:
            if path.exists():
                shutil.copyfile(path, destination / path.name)
                os.chmod(destination / path.name, 0o600)
        # All content is recoverable before the active record is removed.
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE id=?", (doc["id"],))
        self.documents.pop(doc["id"])
        for path in paths:
            if path.exists():
                try:
                    os.replace(path, destination / path.name)
                except OSError:
                    # A stale export may remain visible, but the archived source is
                    # complete and this operation must not lose the active document.
                    pass
        return destination

    def close(self):
        self.db.close()
