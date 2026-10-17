import asyncio
import copy
import io
import json
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import httpx
from PIL import Image

from app.learning import LearningError, LearningService, normalized, source_quote
from app.learning_api import router, resolve_service
from app.learning_model import LearningModel, parse_result, FRAME_SCHEMA
from app.learning_store import LearningStore


def picture(color="white"):
    stream = io.BytesIO()
    Image.new("RGB", (300, 100), color).save(stream, "PNG")
    return stream.getvalue()


def translated(source="It wasn't meant to be.", kind="subtitle", speaker=None):
    return {"blocks": [{"kind": kind, "speaker": speaker, "source": source,
                        "translation": "縁がなかったのかもしれない。", "uncertain": False, "note": ""}],
            "order_uncertain": False, "ocr_note": ""}


class FakeModel:
    def __init__(self):
        self.output = translated()
        self.calls = 0
        self.notes_calls = 0
        self.started = asyncio.Event()
        self.release = None
        self.notes_release = None
        self.fail = False
        self.bad_quote = False
        self.known = False
        self.refine_calls = 0

    async def translate(self, data, context, user_context):
        self.calls += 1
        self.started.set()
        if self.release:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("secret raw diagnostic should not leak")
        return copy.deepcopy(self.output)

    async def explain(self, entries, known_words, user_context):
        self.notes_calls += 1
        if self.notes_release:
            await self.notes_release.wait()
        if self.fail:
            raise RuntimeError("failure")
        return {"items": [{"entry_id": entries[0]["entry_id"],
            "quote": "invented" if self.bad_quote else entries[0]["source"],
            "expression": "insecure" if self.known else "be meant to be",
            "meaning_ja": "縁がなかったという意味。", "context_ja": "この発言の仮の解釈。",
            "register_ja": "日常会話", "uncertain": False}]}

    async def refine_quote(self, item, entry, user_context):
        self.refine_calls += 1
        return {"meaning_ja": "縁がなかったのかもしれない、という意味。",
                "context_ja": "引用の否定と可能性を保った説明。", "register_ja": "日常会話",
                "uncertain": False}


class LearningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1_700_000_000.0
        self.model = FakeModel()
        self.service = LearningService(LearningStore(self.temp.name), self.model, lambda: self.now)
        self.sid = self.service.create("Synthetic show / fixture", "https://example.test/reference")["id"]
        self.seq = 0
        self.control_seq = 0

    async def asyncTearDown(self):
        if self.model.release:
            self.model.release.set()
        if self.model.notes_release:
            self.model.notes_release.set()
        await self.service.close()
        self.temp.cleanup()

    def control(self, active):
        self.control_seq += 1
        if not active:
            self.service.arm_analysis_on_stop(self.sid)
        return self.service.control(self.sid, active, "fixture", self.control_seq)

    async def frame(self, color="white", source=None, kind="subtitle"):
        if source is not None:
            self.model.output = translated(source, kind)
        self.seq += 1
        generation = self.service.snapshot(self.sid)["generation"]
        return await self.service.frame(self.sid, picture(color), generation, self.seq, self.now)

    async def analyse(self, retry_failed=False):
        self.service.start_analysis(self.sid, retry_failed)
        await self.service.analysis_task
        return self.service.snapshot(self.sid)

    async def test_exact_image_and_text_dedup_but_negation_is_distinct(self):
        self.control(True)
        self.assertEqual((await self.frame())["status"], "ok")
        self.assertEqual((await self.frame())["status"], "duplicate")
        self.assertEqual(self.model.calls, 1)
        await self.frame("red")
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 1)
        await self.frame("blue", "It was meant to be.")
        await self.frame("green", "It wasn't meant to be.")
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 3)
        self.assertNotEqual(normalized("I can."), normalized("I can't."))

    async def test_sms_sticky_dedup_and_identical_bubble_occurrences(self):
        self.control(True)
        self.model.output = translated("OK", "sms", "Mia")
        self.model.output["blocks"].append(copy.deepcopy(self.model.output["blocks"][0]))
        await self.frame()
        await self.frame("red")
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 2)
        self.model.output = translated("Next", "subtitle")
        await self.frame("green")
        await self.frame("blue", "OK", "sms")
        # Different visibly supplied speaker is a distinct source.
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 4)

    async def test_empty_screen_clears_current_and_caption_can_recur(self):
        self.control(True)
        await self.frame()
        self.model.output["blocks"] = []
        await self.frame("red")
        self.assertEqual(self.service.snapshot(self.sid)["current"], [])
        await self.frame("blue", "It wasn't meant to be.")
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 2)

    async def test_lost_maybe_modality_is_flagged_for_review(self):
        self.control(True)
        self.model.output = translated("Maybe it wasn't meant to be.")
        self.model.output["blocks"][0]["translation"] = "きっと、そうなる運命じゃなかったのね。"
        await self.frame()
        entry = self.service.snapshot(self.sid)["entries"][0]
        self.assertTrue(entry["uncertain"])
        self.assertIn("推量", entry["note"])

    async def test_stop_discards_inflight_and_blocks_manual_analysis_until_drained(self):
        generation = self.control(True)["generation"]
        self.model.release = asyncio.Event()
        work = asyncio.create_task(self.service.frame(self.sid, picture(), generation, 1, self.now))
        await self.model.started.wait()
        self.control(False)
        with self.assertRaises(LearningError):
            self.service.start_analysis(self.sid)
        self.model.release.set()
        self.assertEqual((await work)["status"], "stale")
        self.assertEqual(self.service.snapshot(self.sid)["entries"], [])
        self.assertEqual(self.service.snapshot(self.sid)["current"], [])

    async def test_busy_request_does_not_queue_and_supersedes_older(self):
        generation = self.control(True)["generation"]
        self.model.release = asyncio.Event()
        work = asyncio.create_task(self.service.frame(self.sid, picture(), generation, 1, self.now))
        await self.model.started.wait()
        result = await self.service.frame(self.sid, picture("red"), generation, 2, self.now)
        self.assertEqual(result["status"], "busy")
        self.model.release.set()
        self.assertEqual((await work)["status"], "stale")
        result = await self.service.frame(self.sid, picture("red"), generation, 3, self.now)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(self.service.snapshot(self.sid)["entries"]), 1)

    async def test_too_old_result_and_out_of_order_controls(self):
        generation = self.control(True)["generation"]
        again = self.control(True)
        self.assertEqual(again["generation"], generation)
        self.model.release = asyncio.Event()
        work = asyncio.create_task(self.service.frame(self.sid, picture(), generation, 1, self.now))
        await self.model.started.wait()
        self.now += 9
        self.model.release.set()
        self.assertEqual((await work)["status"], "stale")
        self.control(False)
        self.service.control(self.sid, True, "fixture", 1)
        self.assertFalse(self.service.snapshot(self.sid)["capturing"])

    async def test_lease_expiry_and_heartbeat_owner(self):
        generation = self.control(True)["generation"]
        with self.assertRaises(LearningError):
            self.service.heartbeat(self.sid, generation, "other")
        self.now += 10
        self.service.heartbeat(self.sid, generation, "fixture")
        self.now += 10
        self.assertTrue(self.service.snapshot(self.sid)["capturing"])
        self.now += 6
        self.assertFalse(self.service.snapshot(self.sid)["capturing"])
        self.assertGreater(self.service.snapshot(self.sid)["generation"], generation)

    async def test_old_controller_stop_cannot_pause_new_owner(self):
        self.control(True)
        self.control(False)
        generation = self.service.control(self.sid, True, "new-owner", 1)["generation"]
        with self.assertRaises(LearningError):
            self.service.control(self.sid, False, "fixture", 3)
        self.assertTrue(self.service.snapshot(self.sid)["capturing"])
        self.assertEqual(self.service.snapshot(self.sid)["generation"], generation)

    async def test_translation_failure_can_retry_without_accepting_hash(self):
        self.control(True)
        self.model.fail = True
        with self.assertRaises(LearningError):
            await self.frame()
        self.assertNotIn("secret", self.service.snapshot(self.sid)["last_error"])
        self.model.fail = False
        self.assertEqual((await self.frame())["status"], "ok")
        self.assertEqual(self.service.snapshot(self.sid)["last_error"], "")

    async def test_stop_automatically_analyses_and_manual_start_is_idempotent(self):
        self.control(True)
        await self.frame()
        with self.assertRaises(LearningError):
            self.service.start_analysis(self.sid)
        self.control(False)
        self.model.notes_release = asyncio.Event()
        self.service.start_analysis(self.sid)
        self.service.start_analysis(self.sid)  # repeated clicks share one job
        await asyncio.sleep(0)
        self.model.notes_release.set()
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(self.model.notes_calls, 1)
        self.assertTrue(snap["notes"][0]["uncertain"])
        self.assertEqual(self.model.refine_calls, 1)
        self.assertIn("なかった", snap["notes"][0]["meaning_ja"])
        self.assertEqual(snap["notes"][0]["quote"], snap["entries"][0]["source"])
        self.assertEqual(snap["entries"][0]["analysis_status"], "done")

    async def test_invalid_quote_is_explicit_failure_and_only_manual_retry(self):
        self.control(True)
        await self.frame()
        self.control(False)
        self.model.bad_quote = True
        snap = await self.analyse()
        self.assertEqual(snap["notes"], [])
        self.assertEqual(snap["entries"][0]["analysis_status"], "failed")
        self.model.bad_quote = False
        snap = await self.analyse(retry_failed=True)
        self.assertEqual(len(snap["notes"]), 1)

    async def test_known_word_filtered_and_analysis_stop_bounded(self):
        self.control(True)
        for index, color in enumerate(["red", "green", "blue", "black"]):
            await self.frame(color, f"Phrase {index} insecure")
        self.control(False)
        self.model.known = True
        self.model.notes_release = asyncio.Event()
        self.service.start_analysis(self.sid)
        await asyncio.sleep(0)
        self.service.stop_analysis(self.sid)
        self.model.notes_release.set()
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(snap["notes"], [])
        self.assertEqual(self.model.notes_calls, 1)
        self.assertEqual(snap["analysis"]["pending"], 1)

    async def test_repeated_context_failures_stop_after_three_batches(self):
        self.control(True)
        for index in range(12):
            await self.frame(f"#{index:06x}", f"Maybe we should call it off, example {index}.")

        async def select_all(entries, known_words, user_context):
            self.model.notes_calls += 1
            return {"items": [{"entry_id": entry["entry_id"], "quote": entry["source"],
                               "expression": "call it off", "meaning_ja": "中止する。",
                               "context_ja": "合成例。", "register_ja": "日常会話",
                               "uncertain": False} for entry in entries]}

        async def failed_context(item, entry, user_context):
            self.model.refine_calls += 1
            raise httpx.ConnectError("synthetic context endpoint failure")

        self.model.explain = select_all
        self.model.refine_quote = failed_context
        self.control(False)
        snap = await self.analyse()
        self.assertEqual(self.model.notes_calls, 3)
        self.assertEqual(self.model.refine_calls, 9)
        self.assertEqual(snap["analysis"]["failed"], 9)
        self.assertEqual(snap["analysis"]["pending"], 3)
        self.assertFalse(snap["analysis"]["running"])
        self.assertIn("3組", snap["analysis"]["error"])

    async def test_persistence_export_retry_user_edit_and_restart_paused(self):
        self.control(True)
        with patch.object(self.service.store, "export", side_effect=OSError):
            await self.frame()
        self.assertTrue(self.service.snapshot(self.sid)["export_error"])
        self.control(False)
        snap = await self.analyse()
        self.assertFalse(snap["export_error"])
        note = snap["notes"][0]
        self.service.edit_note(self.sid, note["id"], {"meaning_ja": "恋愛なら『縁がなかった』。"})
        personal = Path(snap["user_notes_path"])
        personal.write_text("keep my hand-written note")
        self.service.persist(self.service.document(self.sid))
        self.assertEqual(personal.read_text(), "keep my hand-written note")
        self.assertIn("恋愛なら", Path(snap["markdown_path"]).read_text())
        self.control(True)
        # Simulate a restart without graceful shutdown of old runtime state.
        self.service.store.close()
        self.service = LearningService(LearningStore(self.temp.name), self.model, lambda: self.now)
        restored = self.service.snapshot(self.sid)
        self.assertFalse(restored["capturing"])
        self.assertTrue(restored["notes"][0]["edited"])
        self.assertFalse(list(Path(self.temp.name).glob("*.png")))

    async def test_database_write_failure_visible_and_manual_retry(self):
        self.control(True)
        with patch.object(self.service.store, "save", side_effect=sqlite3.OperationalError("disk full")):
            await self.frame()
        snap = self.service.snapshot(self.sid)
        self.assertEqual(snap["entry_count"], 1)
        self.assertIn("DB", snap["export_error"])
        self.service.persist(self.service.document(self.sid))
        self.assertEqual(self.service.snapshot(self.sid)["export_error"], "")

    async def test_http_routes_multipart_validation_and_browser_origin_rejection(self):
        app = FastAPI()
        app.include_router(router)
        async def test_service():
            return self.service
        app.dependency_overrides[resolve_service] = test_service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            base = "/api/learning/sessions/" + self.sid
            response = await client.get(base, headers={"Origin": "https://example.test"})
            self.assertEqual(response.status_code, 403)
            response = await client.post(base + "/control", json={"active": True, "controller_id": "http", "control_seq": 1})
            self.assertEqual(response.status_code, 200)
            gen = response.json()["generation"]
            response = await client.post(base + "/frames", files={"image": ("screen.png", picture(), "image/png")},
                data={"generation": gen, "sequence": 1, "captured_at": self.now})
            self.assertEqual(response.json()["status"], "ok")
            response = await client.post(base + "/analysis", json={})
            self.assertEqual(response.status_code, 409)
            response = await client.patch(base, json={"known_words": ["a" * 201]})
            self.assertEqual(response.status_code, 422)
            response = await client.get(base)
            self.assertEqual(response.json()["entry_count"], 1)

    async def test_local_endpoint_checked_before_network_and_schema_validation(self):
        class Settings:
            api_base = "https://external.invalid"
            model_name = "unused"
        with patch("app.learning_model.get_settings", return_value=Settings()):
            with self.assertRaises(ValueError):
                await LearningModel().translate(picture(), [], "")
        with self.assertRaises(ValueError):
            parse_result('{"blocks": []}', FRAME_SCHEMA)

    async def test_model_schema_limits_quote_ids_to_current_batch(self):
        class CaptureModel(LearningModel):
            async def _request(inner, prompt, schema, image=None, max_tokens=1400):
                inner.schema = schema
                inner.prompt = prompt
                return {"items": []}
        model = CaptureModel()
        await model.explain([{"entry_id": "current-id", "source": "OK",
                              "preceding": [{"source": "interesting idiom"}]}], [], "")
        ids = model.schema["properties"]["items"]["items"]["properties"]["entry_id"]
        self.assertEqual(ids["enum"], ["1"])
        self.assertNotIn("interesting idiom", model.prompt)
        self.assertNotIn("current-id", model.prompt)

    async def test_resume_during_selection_preserves_cache_and_stops_new_requests(self):
        self.control(True)
        for index in range(7):
            await self.frame((index * 30, 0, 0), f"Phrase {index}")
        self.model.notes_release = asyncio.Event()
        self.control(False)  # explicit stop automatically starts pending-only work
        await asyncio.sleep(0)
        self.assertEqual(self.model.notes_calls, 1)
        state = self.control(True)
        self.assertTrue(state["capturing"])
        self.assertTrue(self.service.snapshot(self.sid)["analysis"]["resume_pending"])
        self.assertEqual((await self.frame("white", "New caption"))["status"], "busy")
        self.model.notes_release.set()
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(snap["entry_count"], 7)
        self.assertEqual(self.model.refine_calls, 0)
        self.assertEqual(snap["analysis"]["pending"], 5)
        self.assertEqual(snap["analysis"]["done"], 2)
        self.assertEqual(snap["analysis"]["failed"], 0)
        self.control(False)
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(snap["analysis"]["pending"], 0)
        self.assertEqual(snap["analysis"]["done"], 7)
        self.assertEqual(snap["analysis"]["processed"], 5)
        self.assertEqual(snap["analysis"]["total_run"], 5)

    async def test_resume_during_context_request_saves_finished_note(self):
        self.control(True)
        for index, color in enumerate(["red", "green", "blue", "black"]):
            await self.frame(color, f"Phrase {index}")
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.model.refine_quote
        async def refine(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        self.model.refine_quote = refine
        self.control(False)
        await entered.wait()
        self.control(True)
        release.set()
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(len(snap["notes"]), 1)
        self.assertEqual(snap["analysis"]["done"], 3)
        self.assertEqual(snap["analysis"]["pending"], 1)
        self.assertEqual(self.model.notes_calls, 1)

    async def test_fast_resume_stop_drains_then_continues_without_losing_work(self):
        self.control(True)
        for index, color in enumerate(["red", "green", "blue", "black"]):
            await self.frame(color, f"Phrase {index}")
        self.model.notes_release = asyncio.Event()
        self.control(False)
        await asyncio.sleep(0)
        self.control(True)
        self.control(False)
        first_task = self.service.analysis_task
        self.model.notes_release.set()
        await first_task
        await asyncio.sleep(0)
        await self.service.analysis_task
        self.assertEqual(self.service.snapshot(self.sid)["analysis"]["pending"], 0)
        self.assertEqual(self.service.snapshot(self.sid)["analysis"]["done"], 4)

    async def test_invalid_candidate_does_not_starve_siblings_or_next_batch(self):
        self.control(True)
        for index, color in enumerate(["red", "green", "blue", "black"]):
            await self.frame(color, f"Phrase {index}")
        original = self.model.explain
        async def explain(entries, known, context):
            result = await original(entries, known, context)
            if len(entries) == 3:
                bad = copy.deepcopy(result["items"][0])
                bad["quote"] = "adjacent phrase absent from current source"
                good = {**result["items"][0], "entry_id": entries[1]["entry_id"], "quote": entries[1]["source"]}
                return {"items": [bad, good]}
            return {"items": []}
        self.model.explain = explain
        self.control(False)
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(snap["analysis"]["failed"], 1)
        self.assertEqual(snap["analysis"]["done"], 3)
        self.assertEqual(snap["analysis"]["no_notes"], 2)
        self.assertEqual(snap["analysis"]["pending"], 0)
        self.assertEqual(snap["analysis"]["note_count"], 1)
        self.assertEqual(snap["entries"][0]["analysis_error"], "provenance")
        calls = self.model.notes_calls
        self.control(False)  # repeat stop cannot retry failed entries
        self.service.start_analysis(self.sid)
        self.assertEqual(self.model.notes_calls, calls)
        self.model.explain = original
        await self.analyse(retry_failed=True)
        self.assertEqual(self.service.snapshot(self.sid)["analysis"]["failed"], 0)

    async def test_transport_outage_has_bounded_attempts_and_preserves_remaining(self):
        self.control(True)
        for index in range(20):
            await self.frame((index * 10, 0, 0), f"Phrase {index}")
        self.model.fail = True
        self.control(False)
        await self.service.analysis_task
        snap = self.service.snapshot(self.sid)
        self.assertEqual(self.model.notes_calls, 3)
        self.assertEqual(snap["analysis"]["failed"], 9)
        self.assertEqual(snap["analysis"]["pending"], 11)
        self.assertIn("3組", snap["analysis"]["error"])
        self.assertNotIn("failure", snap["analysis"]["error"])

    async def test_restart_and_lease_expiry_do_not_start_inference(self):
        self.control(True)
        await self.frame()
        self.now += 16
        self.assertFalse(self.service.snapshot(self.sid)["capturing"])
        self.assertEqual(self.model.notes_calls, 0)
        await self.service.close()
        self.service = LearningService(LearningStore(self.temp.name), self.model, lambda: self.now)
        await asyncio.sleep(0)
        self.assertEqual(self.model.notes_calls, 0)
        self.assertEqual(self.service.snapshot(self.sid)["analysis"]["pending"], 1)
        self.control(False)
        self.assertEqual(self.model.notes_calls, 0)

    async def test_safety_pause_does_not_implicitly_start_learning(self):
        self.control(True)
        await self.frame()
        self.service.control(self.sid, False, "fixture", self.control_seq + 1)
        await asyncio.sleep(0)
        self.assertFalse(self.service.snapshot(self.sid)["capturing"])
        self.assertEqual(self.model.notes_calls, 0)
        self.assertEqual(self.service.snapshot(self.sid)["analysis"]["pending"], 1)

    async def test_quote_whitespace_maps_to_original_but_never_changes_negation(self):
        self.assertEqual(source_quote("I use it as\na tool to text.", "as a tool to"), "as\na tool to")
        self.assertIsNone(source_quote("It wasn't meant to be", "It was meant to be"))
        self.assertIsNone(source_quote("I paid 15", "I paid 50"))

    async def test_record_rename_keeps_stable_files_and_trash_is_recoverable(self):
        first = self.service.snapshot(self.sid)
        other = self.service.create(first["title"])
        renamed = self.service.update(self.sid, {"title": other["title"]})
        self.assertEqual(renamed["markdown_path"], first["markdown_path"])
        self.assertNotEqual(renamed["markdown_path"], other["markdown_path"])
        personal = Path(first["user_notes_path"])
        personal.write_text("retain handwritten content")
        with self.assertRaises(LearningError):
            self.service.trash(self.sid, "wrong title")
        self.control(True)
        with self.assertRaises(LearningError):
            self.service.trash(self.sid, renamed["title"])
        self.control(False)  # empty capture has no inference to run
        result = self.service.trash(self.sid, renamed["title"])
        folder = Path(result["trash_path"])
        archived = json.loads((folder / "record.json").read_text())
        self.assertEqual(archived["document"]["id"], self.sid)
        self.assertEqual((folder / personal.name).read_text(), "retain handwritten content")
        self.assertTrue((folder / Path(first["markdown_path"]).name).exists())
        self.assertFalse(Path(first["markdown_path"]).exists())
        self.assertEqual(len(self.service.sessions()), 1)
        with self.assertRaises(LearningError):
            self.service.document(self.sid)
        self.assertEqual(self.service.document(other["id"])["title"], other["title"])

    async def test_trash_rejects_paths_and_inflight_translation(self):
        doc = self.service.document(self.sid)
        original = doc["filename"]
        for name in ["../outside", "/tmp/outside", "..\\outside"]:
            doc["filename"] = name
            with self.assertRaises(LearningError):
                self.service.trash(self.sid, doc["title"])
        doc["filename"] = original
        output = Path(self.service.snapshot(self.sid)["markdown_path"])
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("keep")
        output.unlink()
        output.symlink_to(outside)
        with self.assertRaises(LearningError):
            self.service.trash(self.sid, doc["title"])
        self.assertEqual(outside.read_text(), "keep")
        output.unlink()
        self.service.persist(doc)
        generation = self.control(True)["generation"]
        self.model.release = asyncio.Event()
        pending = asyncio.create_task(self.service.frame(self.sid, picture(), generation, 1, self.now))
        await self.model.started.wait()
        self.control(False)
        with self.assertRaises(LearningError):
            self.service.trash(self.sid, doc["title"])
        self.model.release.set()
        self.assertEqual((await pending)["status"], "stale")
        self.service.trash(self.sid, doc["title"])
        new = self.service.create("New record")
        self.assertEqual(new["entries"], [])
