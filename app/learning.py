"""Bounded learning workflow, separate from legacy overlay/TTS state."""
import asyncio
import copy
import hashlib
import io
import re
import sqlite3
import time
import unicodedata
import uuid

from PIL import Image

from .learning_model import LearningModel, FRAME_SCHEMA, NOTES_SCHEMA, SELECTED_SCHEMA, validate
from .learning_store import LearningStore
from .learning_trash import preview_trash, empty_trash, TrashUnsafe, TrashChanged, TrashDeleteFailed


class LearningError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def normalized(text):
    # Deliberately no fuzzy matching: can/can't, wasn't/was and numbers must differ.
    return " ".join(unicodedata.normalize("NFKC", text).replace("’", "'").split())


def source_quote(source, quote):
    """Map display whitespace only, then retain the exact stored source span."""
    if not quote.strip():
        return None
    if quote in source:
        return quote
    pattern = r"\s+".join(re.escape(word) for word in quote.strip().split())
    match = re.search(pattern, source)
    return match.group() if match else None


class LearningService:
    LEASE_SECONDS = 15
    FRAME_MAX_AGE = 8

    def __init__(self, store=None, model=None, clock=time.time):
        self.store = store or LearningStore()
        self.model = model or LearningModel()
        self.clock = clock
        self.gate = asyncio.Lock()
        self.analysis_task = None
        self.auto_analysis_sid = None
        self.closing = False
        self.trash_confirmations = {}

    def document(self, sid):
        try:
            doc = self.store.documents[sid]
        except KeyError:
            raise LearningError(404, "視聴記録が見つかりません。")
        if doc["capturing"] and doc["lease_until"] < self.clock():
            self._pause(doc)
            doc["last_error"] = "パネルとの接続が切れたため停止しました。"
            self.store.save(doc)
        return doc

    def _pause(self, doc):
        doc["capturing"] = False
        doc["generation"] += 1
        doc["lease_until"] = 0
        doc["current"] = []
        doc["image_hash"] = ""
        doc["analyse_on_stop"] = False

    def persist(self, doc):
        errors = []
        try:
            self.store.save(doc)
        except (sqlite3.Error, OSError):
            errors.append("履歴DBの保存に失敗しました。保存を再試行してください。")
        try:
            self.store.export(doc)
        except OSError:
            errors.append("Markdownの保存に失敗しました。保存を再試行してください。")
        doc["export_error"] = " ".join(errors)
        try:
            self.store.save(doc)
        except (sqlite3.Error, OSError):
            if not errors:
                doc["export_error"] = "履歴DBの保存に失敗しました。保存を再試行してください。"

    def create(self, title, source_url=""):
        now, sid = self.clock(), uuid.uuid4().hex
        doc = {"id": sid, "title": title.strip() or "視聴記録", "source_url": source_url,
               "created_at": now, "filename": self.store.filename(title, now, sid),
               "capturing": False, "generation": 0, "lease_until": 0,
               "controller_id": "", "control_sequences": {}, "latest_sequence": -1,
               "accepted_sequence": -1, "image_hash": "", "current": [],
               "entries": [], "notes": [], "known_words": ["insecure"], "user_context": "",
               "last_error": "", "export_error": "",
               "analyse_on_stop": False,
               "analysis": {"running": False, "stop_requested": False, "error": ""}}
        self.persist(doc)
        return self.snapshot(sid)

    def snapshot(self, sid):
        doc = self.document(sid)
        snap = copy.deepcopy({key: value for key, value in doc.items() if key != "entries"})
        snap["entries"] = copy.deepcopy(doc["entries"][-200:])
        for key in ("control_sequences", "lease_until", "image_hash"):
            snap.pop(key, None)
        snap["entry_count"] = len(doc["entries"])
        counts = {state: sum(e["analysis_status"] == state for e in doc["entries"])
                  for state in ("pending", "failed", "done")}
        noted = {n["entry_id"] for n in doc["notes"]}
        snap["analysis"].update(**counts, total=len(doc["entries"]), note_count=len(doc["notes"]),
            with_notes=sum(e["id"] in noted for e in doc["entries"]),
            no_notes=sum(e["analysis_status"] == "done" and e["id"] not in noted for e in doc["entries"]),
            resume_pending=doc["analysis"]["running"] and doc["analysis"]["stop_requested"] and
                           any(d["capturing"] for d in self.store.documents.values()))
        snap["resume_pending"] = snap["analysis"]["resume_pending"]
        output, personal = self.store.paths(doc)
        snap["markdown_path"], snap["user_notes_path"] = str(output), str(personal)
        snap["directory_path"] = str(self.store.root)
        snap["trash_path"] = str(self.store.root / ".trash")
        return snap

    def sessions(self):
        return [{key: snap[key] for key in ("id", "title", "created_at", "capturing", "entry_count")}
                for snap in (self.snapshot(sid) for sid in reversed(self.store.documents))]

    def update(self, sid, changes):
        doc = self.document(sid)
        if doc["analysis"]["running"]:
            raise LearningError(409, "解説を停止してから設定を編集してください。")
        for key in ("title", "source_url", "user_context", "known_words"):
            if key in changes:
                doc[key] = changes[key]
        self.persist(doc)
        return self.snapshot(sid)

    def control(self, sid, active, controller_id, control_seq):
        doc = self.document(sid)
        previous = doc["control_sequences"].get(controller_id, -1)
        if control_seq <= previous:
            return {"capturing": doc["capturing"], "generation": doc["generation"]}
        if active:
            self.auto_analysis_sid = None
            doc["analyse_on_stop"] = False
            if self.analysis_task and not self.analysis_task.done():
                # Never cancel an HTTP inference: the server may still be working.
                # Finish its result, but submit no new explanation/refinement.
                for other in self.store.documents.values():
                    if other["analysis"]["running"]:
                        other["analysis"]["stop_requested"] = True
            if any(self.document(other)["capturing"] for other in self.store.documents if other != sid):
                raise LearningError(409, "別の視聴記録が取得中です。先に停止してください。")
            if doc["capturing"] and doc["controller_id"] != controller_id:
                raise LearningError(409, "別のパネルが取得中です。")
            if not doc["capturing"]:
                doc["generation"] += 1
                doc["latest_sequence"] = doc["accepted_sequence"] = -1
                doc["image_hash"] = ""
            doc["capturing"] = True
            doc["controller_id"] = controller_id
            doc["lease_until"] = self.clock() + self.LEASE_SECONDS
            doc["last_error"] = ""
        else:
            if doc["capturing"] and doc["controller_id"] != controller_id:
                raise LearningError(409, "古いパネルからの停止要求です。")
            if doc["capturing"]:
                auto_analyse = doc.get("analyse_on_stop", False)
                self._pause(doc)
            else:
                auto_analyse = False
        doc["control_sequences"][controller_id] = control_seq
        # Controller IDs change on extension restart; retain a bounded retry ledger.
        if len(doc["control_sequences"]) > 32:
            first = next(iter(doc["control_sequences"]))
            del doc["control_sequences"][first]
        self.store.save(doc)
        if not active and auto_analyse and any(e["analysis_status"] == "pending" for e in doc["entries"]):
            # Only an explicit active -> stopped control transition starts inference.
            # A startup, lease expiry, repeat stop or failed entry never triggers it.
            self.auto_analysis_sid = sid
            self._start_deferred_analysis()
        return {"capturing": doc["capturing"], "generation": doc["generation"]}

    def arm_analysis_on_stop(self, sid):
        doc = self.document(sid)
        # The native panel arms only its explicit Stop action. Safety pauses,
        # mode switches and opening a window do not cause background inference.
        doc["analyse_on_stop"] = doc["capturing"]
        self.store.save(doc)
        return {"armed": doc["analyse_on_stop"]}

    def heartbeat(self, sid, generation, controller_id):
        doc = self.document(sid)
        if not doc["capturing"] or generation != doc["generation"] or controller_id != doc["controller_id"]:
            raise LearningError(409, "取得は停止中、または古い接続です。")
        doc["lease_until"] = self.clock() + self.LEASE_SECONDS
        return {"capturing": True, "generation": generation}

    def _fresh(self, doc, generation, sequence, captured_at):
        return (doc["capturing"] and doc["generation"] == generation
                and sequence == doc["latest_sequence"] and sequence > doc["accepted_sequence"]
                and -5 <= self.clock() - captured_at <= self.FRAME_MAX_AGE)

    async def frame(self, sid, data, generation, sequence, captured_at):
        doc = self.document(sid)
        result = {"status": "stale", "generation": generation, "sequence": sequence}
        if not doc["capturing"] or doc["generation"] != generation:
            raise LearningError(409, "取得は停止中、または古い画像です。")
        if (sequence <= doc["latest_sequence"] or sequence <= doc["accepted_sequence"]
                or not -5 <= self.clock() - captured_at <= self.FRAME_MAX_AGE):
            return result
        doc["latest_sequence"] = sequence
        if self.gate.locked():
            return {**result, "status": "busy"}
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 16_000_000 or image.width < 1 or image.height < 1:
                    raise ValueError("image dimensions")
                # Canonical PNG; strips metadata and ensures model receives declared image format.
                pixels = image.convert("RGB")
                stream = io.BytesIO()
                pixels.save(stream, format="PNG")
                image_bytes = stream.getvalue()
        except Exception:
            raise LearningError(422, "画像を読み取れません。")
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest == doc["image_hash"]:
            doc["accepted_sequence"] = sequence
            return {**result, "status": "duplicate"}
        async with self.gate:
            try:
                context = [{"kind": e["kind"], "speaker": e["speaker"], "source": e["source"][:1200]}
                           for e in doc["entries"][-4:]]
                output = await self.model.translate(image_bytes, context, doc["user_context"])
                validate(output, FRAME_SCHEMA)
            except Exception:
                if self._fresh(self.document(sid), generation, sequence, captured_at):
                    doc["last_error"] = "翻訳に失敗しました。次の取得で再試行します。ローカルモデルを確認してください。"
                    self.store.save(doc)
                raise LearningError(503, "翻訳を再試行してください。")
            doc = self.document(sid)
            if not self._fresh(doc, generation, sequence, captured_at):
                return result
            blocks = [block for block in output["blocks"] if block["source"].strip()]
            previous = [(e["kind"], e["speaker"], normalized(e["source"])) for e in doc["current"]]
            sticky = {(e["kind"], e["speaker"], normalized(e["source"]), e["occurrence"])
                      for e in doc["entries"] if e["kind"] != "subtitle"}
            occurrences = {}
            current = []
            for block in blocks:
                key = (block["kind"], block["speaker"], normalized(block["source"]))
                occurrence = occurrences.get(key, 0)
                occurrences[key] = occurrence + 1
                repeated = (key in previous if block["kind"] == "subtitle"
                            else (*key, occurrence) in sticky)
                shown = dict(block)
                shown["uncertain"] |= output["order_uncertain"]
                if (re.search(r"\b(?:maybe|perhaps)\b", block["source"], re.I)
                        and not re.search(r"かも|たぶん|多分|おそらく|恐らく|ひょっと|もしか|可能性|だろう|でしょう",
                                          block["translation"])):
                    shown["uncertain"] = True
                    shown["note"] = " ".join(filter(None, [shown["note"],
                        "原文は推量を含みます。訳の断定度を確認してください。"]))
                if output["ocr_note"]:
                    shown["note"] = " ".join(filter(None, [shown["note"], output["ocr_note"]]))
                current.append(shown)
                if repeated:
                    continue
                entry = {**shown, "id": uuid.uuid4().hex, "sequence": sequence,
                         "captured_at": captured_at, "occurrence": occurrence,
                         "context": [{k: e[k] for k in ("id", "kind", "speaker", "source")}
                                     for e in doc["entries"][-4:]], "analysis_status": "pending"}
                doc["entries"].append(entry)
                sticky.add((*key, occurrence))
            doc["current"] = current
            doc["accepted_sequence"], doc["image_hash"] = sequence, digest
            doc["last_error"] = ""
            self.persist(doc)
            return {**result, "status": "ok"}

    def start_analysis(self, sid, retry_failed=False, wait_for_frame=False):
        doc = self.document(sid)
        if doc["analysis"]["running"]:
            return self.snapshot(sid)
        if (self.gate.locked() and not wait_for_frame) or (self.analysis_task and not self.analysis_task.done()):
            raise LearningError(409, "実行中の処理が終わってから解説を開始してください。")
        if any(self.document(other)["capturing"] for other in self.store.documents):
            raise LearningError(409, "画面取得を停止してから解説してください。")
        targets = [e for e in doc["entries"] if e["analysis_status"] ==
                   ("failed" if retry_failed else "pending")]
        doc["analysis"].update(running=bool(targets), stop_requested=False, error="",
                               processed=0, total_run=len(targets))
        self.store.save(doc)
        if targets:
            if self.auto_analysis_sid == sid:
                self.auto_analysis_sid = None
            self.analysis_task = asyncio.create_task(self._analyse(doc, targets))
        return self.snapshot(sid)

    def _start_deferred_analysis(self):
        sid = self.auto_analysis_sid
        if (self.closing or not sid or sid not in self.store.documents or
                any(d["capturing"] for d in self.store.documents.values()) or
                (self.analysis_task and not self.analysis_task.done())):
            return
        self.auto_analysis_sid = None
        self.start_analysis(sid, wait_for_frame=True)

    def stop_analysis(self, sid):
        doc = self.document(sid)
        if self.auto_analysis_sid == sid:
            self.auto_analysis_sid = None
        doc["analysis"]["stop_requested"] = True
        self.store.save(doc)
        return self.snapshot(sid)

    def _supplied(self, doc, batch):
        supplied = []
        by_id = {e["id"]: e for e in doc["entries"]}
        for e in batch:
            idx = doc["entries"].index(e)
            supplied.append({"entry_id": e["id"], "source": e["source"][:1800],
                             "source_truncated": len(e["source"]) > 1800,
                             "preceding": [{"kind": x["kind"], "speaker": x["speaker"],
                                            "source": x["source"][:300]} for x in e["context"][-4:]
                                           if abs(e["captured_at"] - by_id.get(x.get("id"), e)["captured_at"]) <= 90],
                             "following": [{"source": x["source"][:300], "speaker": x["speaker"]}
                                           for x in doc["entries"][idx + 1:idx + 3]
                                           if x["captured_at"] - e["captured_at"] <= 90]})
        return supplied

    def _analysis_stopped(self, doc):
        return doc["analysis"]["stop_requested"] or any(d["capturing"] for d in self.store.documents.values())

    async def _analyse(self, doc, targets):
        consecutive_errors = 0
        try:
            for start in range(0, len(targets), 3):
                if self._analysis_stopped(doc):
                    break
                batch = targets[start:start + 3]
                original = {e["id"]: e["analysis_status"] for e in batch}
                supplied = self._supplied(doc, batch)
                try:
                    async with self.gate:
                        if self._analysis_stopped(doc):
                            break
                        for e in batch:
                            e["analysis_status"] = "running"
                        self.store.save(doc)
                        output = await self.model.explain(supplied, doc["known_words"], doc["user_context"])
                    validate(output, NOTES_SCHEMA)
                    by_id = {e["id"]: e for e in batch}
                    per_entry = {}
                    candidates = []
                    failures = {}
                    deferred = set()
                    known = {normalized(w).casefold() for w in doc["known_words"]}
                    for item in output["items"]:
                        entry = by_id.get(item["entry_id"])
                        quote = source_quote(entry["source"], item["quote"]) if entry else None
                        if quote is None:
                            # Reject rather than silently attach a context quote to a source.
                            for e in ([entry] if entry else batch):
                                failures[e["id"]] = "provenance"
                            continue
                        item = dict(item, quote=quote)
                        if not item["expression"].strip() or not item["meaning_ja"].strip():
                            failures[entry["id"]] = "invalid_note"
                            continue
                        if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", item["meaning_ja"]):
                            failures[entry["id"]] = "invalid_note"
                            continue
                        per_entry[entry["id"]] = per_entry.get(entry["id"], 0) + 1
                        if per_entry[entry["id"]] > 2:
                            failures[entry["id"]] = "invalid_note"
                            continue
                        expression = normalized(item["expression"]).casefold()
                        if expression in known:
                            continue
                        if normalized(item["quote"]).casefold().strip(" .!?,;:'\"()[]") in known:
                            continue
                        if expression in {normalized(n["expression"]).casefold() for n in doc["notes"] + candidates}:
                            continue
                        # Selection sees current sources only. Context enters this narrow
                        # selected-quote pass, so it cannot become a different source.
                        if self._analysis_stopped(doc):
                            deferred.add(entry["id"])
                            continue
                        try:
                            async with self.gate:
                                if self._analysis_stopped(doc):
                                    deferred.add(entry["id"])
                                    continue
                                evidence = next(e for e in supplied if e["entry_id"] == entry["id"])
                                contextual_entry = dict(entry, context=evidence["preceding"], following=evidence["following"])
                                refined = await self.model.refine_quote(item, contextual_entry, doc["user_context"])
                            validate(refined, SELECTED_SCHEMA)
                            item = {**item, **refined}
                            if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", item["meaning_ja"]):
                                raise ValueError("quote meaning must be Japanese")
                            if re.search(r"\b(?:not|never|neither|nor)\b|n['’]t\b", item["quote"], re.I):
                                item["uncertain"] = True
                        except Exception:
                            failures[entry["id"]] = "context_generation"
                            continue
                        item = dict(item, uncertain=item["uncertain"] or entry["uncertain"])
                        idx = doc["entries"].index(entry)
                        if not entry["context"] and idx + 1 == len(doc["entries"]) and not doc["user_context"]:
                            item = dict(item, uncertain=True,
                                        context_ja="前後の文脈がないため仮の解釈です。 " + item["context_ja"])
                        candidates.append({**item, "id": uuid.uuid4().hex, "edited": False,
                                           "created_at": self.clock()})
                    doc["notes"].extend(candidates)
                    for e in batch:
                        e["analysis_status"] = ("failed" if e["id"] in failures else
                                                original[e["id"]] if e["id"] in deferred else "done")
                        e["analysis_error"] = failures.get(e["id"], "")
                        if e["id"] not in deferred:
                            doc["analysis"]["processed"] += 1
                    # Context requests are model operations too. A successful
                    # selection must not reset repeated contextual failures.
                    consecutive_errors = (consecutive_errors + 1
                                          if "context_generation" in failures.values() else 0)
                except Exception:
                    consecutive_errors += 1
                    for e in batch:
                        e["analysis_status"] = "failed"
                        e["analysis_error"] = "model_request"
                    doc["analysis"]["processed"] += len(batch)
                self.persist(doc)
                if consecutive_errors >= 3:
                    doc["analysis"]["error"] = "モデルへの要求が3組続けて失敗したため停止しました。未処理分は保持しています。"
                    break
        finally:
            doc["analysis"]["running"] = False
            doc["analysis"]["stop_requested"] = False
            if not doc["analysis"]["error"] and any(e["analysis_status"] == "failed" for e in doc["entries"]):
                doc["analysis"]["error"] = "失敗した項目を保持しています。停止中に「失敗分を再試行」で再試行できます。"
            self.persist(doc)
            if self.auto_analysis_sid and not self.closing:
                asyncio.get_running_loop().call_soon(self._start_deferred_analysis)

    def trash(self, sid, confirm_title):
        doc = self.document(sid)
        if doc["capturing"] or doc["analysis"]["running"] or self.gate.locked():
            raise LearningError(409, "取得と学習メモ生成を停止してから削除してください。")
        if confirm_title != doc["title"]:
            raise LearningError(409, "記録名が変わりました。削除する対象を確認し直してください。")
        try:
            location = self.store.trash(doc)
        except (OSError, ValueError, sqlite3.Error):
            raise LearningError(503, "退避に失敗しました。記録は保持しています。")
        return {"id": sid, "trashed": True, "trash_path": str(location)}

    def _trash_busy(self):
        # Normalize abandoned controllers through the same lease checks as a
        # snapshot; another record's expired capture must not block cleanup.
        return (self.gate.locked() or any(self.document(sid)["capturing"] or
                self.store.documents[sid]["analysis"]["running"] for sid in self.store.documents) or
                bool(self.analysis_task and not self.analysis_task.done()))

    def preview_trash(self):
        try:
            preview = preview_trash(self.store.root)
        except (OSError, TrashUnsafe):
            raise LearningError(503, "ゴミ箱を安全に確認できません。想定外のファイルやリンク、アクセス権を確認してください。削除は行っていません。")
        now = self.clock()
        self.trash_confirmations = {key: value for key, value in self.trash_confirmations.items()
                                    if value[1] > now}
        token = uuid.uuid4().hex
        self.trash_confirmations[token] = (preview["fingerprint"], now + 300)
        while len(self.trash_confirmations) > 16:
            del self.trash_confirmations[next(iter(self.trash_confirmations))]
        return {"count": preview["count"], "token": token, "path": str(self.store.root / ".trash"),
                "busy": self._trash_busy()}

    def empty_trash(self, confirm_permanent, token):
        if not confirm_permanent:
            raise LearningError(400, "完全削除の確認が必要です。")
        if self._trash_busy():
            raise LearningError(409, "取得と学習メモ生成を停止してからゴミ箱を空にしてください。削除は行っていません。")
        confirmation = self.trash_confirmations.pop(token, None)
        if not confirmation or confirmation[1] <= self.clock():
            raise LearningError(409, "確認が期限切れ、または無効です。ゴミ箱の件数を確認し直してください。削除は行っていません。")
        try:
            return empty_trash(self.store.root, confirmation[0])
        except TrashChanged:
            raise LearningError(409, "確認後にゴミ箱の内容が変わりました。件数を確認し直してください。削除は行っていません。")
        except TrashDeleteFailed as error:
            raise LearningError(503, f"ゴミ箱の完全削除が途中で失敗しました。{error.deleted}件を削除完了しました。一部ファイルが削除済みの場合があります。残った内容を確認し直してください。")
        except (OSError, TrashUnsafe):
            raise LearningError(503, "ゴミ箱を安全に確認できないため削除を中止しました。削除は行っていません。")

    def edit_note(self, sid, note_id, changes):
        doc = self.document(sid)
        note = next((n for n in doc["notes"] if n["id"] == note_id), None)
        if note is None:
            raise LearningError(404, "学習メモが見つかりません。")
        for key in ("meaning_ja", "context_ja", "register_ja", "uncertain"):
            if key in changes:
                note[key] = changes[key]
        note["edited"] = True
        self.persist(doc)
        return self.snapshot(sid)

    async def close(self):
        self.closing = True
        self.auto_analysis_sid = None
        if self.analysis_task and not self.analysis_task.done():
            for doc in self.store.documents.values():
                doc["analysis"]["stop_requested"] = True
            await self.analysis_task
        for doc in self.store.documents.values():
            if doc["capturing"]:
                self._pause(doc)
                self.store.save(doc)
        self.store.close()
