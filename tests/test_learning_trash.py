"""Irreversible cleanup tests use only TemporaryDirectory fixture records."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
import httpx

from app.learning import LearningError, LearningService
from app.learning_api import resolve_service, router
from app.learning_store import LearningStore


class TrashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1_700_000_000.0
        self.service = LearningService(LearningStore(self.root), clock=lambda: self.now)
        self.active = self.service.create("Keep current viewing record")
        self.sid = self.active["id"]
        self.active_bytes = Path(self.active["markdown_path"]).read_bytes()
        self.active_row = self.service.store.db.execute("SELECT data FROM sessions WHERE id=?", (self.sid,)).fetchone()[0]

    async def asyncTearDown(self):
        self.service.document(self.sid)["analysis"]["running"] = False
        await self.service.close()
        self.temp.cleanup()

    def archive(self, title="Disposable fixture", personal=False):
        session = self.service.create(title)
        if personal:
            Path(session["user_notes_path"]).write_text("disposable private fixture note")
        result = self.service.trash(session["id"], title)
        return Path(result["trash_path"])

    def files(self, folder):
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob("*") if p.is_file() and not p.is_symlink()}

    def assert_active_untouched(self):
        self.assertEqual(Path(self.active["markdown_path"]).read_bytes(), self.active_bytes)
        self.assertEqual(self.service.store.db.execute("SELECT data FROM sessions WHERE id=?", (self.sid,)).fetchone()[0], self.active_row)

    async def test_missing_and_empty_trash_are_safe_noops(self):
        preview = self.service.preview_trash()
        self.assertEqual(preview["count"], 0)
        self.assertFalse((self.root / ".trash").exists())
        self.assertEqual(self.service.empty_trash(True, preview["token"]), {"deleted": 0, "remaining": 0})
        (self.root / ".trash").mkdir()
        self.assertEqual(self.service.preview_trash()["count"], 0)
        self.assert_active_untouched()

    async def test_preview_cancel_and_explicit_purge_preserve_current_record(self):
        self.archive(personal=True)
        self.archive("Disposable B")
        before = self.files(self.root / ".trash")
        preview = self.service.preview_trash()
        self.assertEqual(preview["count"], 2)
        self.assertFalse(preview["busy"])
        self.assertEqual(self.files(self.root / ".trash"), before)  # Cancel: never POST
        with self.assertRaises(LearningError) as rejected:
            self.service.empty_trash(False, preview["token"])
        self.assertEqual(rejected.exception.status, 400)
        self.assertEqual(self.files(self.root / ".trash"), before)
        self.assertEqual(self.service.empty_trash(True, preview["token"]), {"deleted": 2, "remaining": 0})
        self.assertEqual(list((self.root / ".trash").iterdir()), [])
        with self.assertRaises(LearningError):
            self.service.empty_trash(True, preview["token"])
        self.assert_active_untouched()

    async def test_changed_inventory_requires_fresh_confirmation(self):
        first = self.archive()
        preview = self.service.preview_trash()
        second = self.archive("Arrived after confirmation")
        before = self.files(self.root / ".trash")
        with self.assertRaises(LearningError) as rejected:
            self.service.empty_trash(True, preview["token"])
        self.assertEqual(rejected.exception.status, 409)
        self.assertIn("内容が変わ", rejected.exception.message)
        self.assertTrue(first.exists() and second.exists())
        self.assertEqual(self.files(self.root / ".trash"), before)
        fresh = self.service.preview_trash()
        self.assertEqual(self.service.empty_trash(True, fresh["token"])["deleted"], 2)
        self.assert_active_untouched()

    async def test_expired_token_does_not_delete_anything(self):
        archive = self.archive()
        preview = self.service.preview_trash()
        self.now += 301
        with self.assertRaises(LearningError) as rejected:
            self.service.empty_trash(True, preview["token"])
        self.assertEqual(rejected.exception.status, 409)
        self.assertTrue((archive / "record.json").exists())

    async def test_capture_analysis_and_inflight_gate_reject_cleanup(self):
        archive = self.archive()
        self.service.control(self.sid, True, "fixture", 1)
        preview = self.service.preview_trash()
        self.assertTrue(preview["busy"])
        with self.assertRaises(LearningError) as rejected:
            self.service.empty_trash(True, preview["token"])
        self.assertEqual(rejected.exception.status, 409)
        self.service.control(self.sid, False, "fixture", 2)
        self.service.document(self.sid)["analysis"]["running"] = True
        with self.assertRaises(LearningError):
            self.service.empty_trash(True, preview["token"])
        self.service.document(self.sid)["analysis"]["running"] = False
        async with self.service.gate:
            self.assertTrue(self.service.preview_trash()["busy"])
            with self.assertRaises(LearningError):
                self.service.empty_trash(True, preview["token"])
        self.assertTrue((archive / "record.json").exists())

    async def test_expired_other_controller_does_not_leave_cleanup_permanently_busy(self):
        self.archive()
        other = self.service.create("Abandoned controller fixture")
        self.service.control(other["id"], True, "abandoned", 1)
        self.now += self.service.LEASE_SECONDS + 1
        # No separate snapshot/list call to expire this record first.
        preview = self.service.preview_trash()
        self.assertFalse(preview["busy"])
        self.assertFalse(self.service.store.documents[other["id"]]["capturing"])
        self.assertEqual(self.service.empty_trash(True, preview["token"])["deleted"], 1)
        self.assert_active_untouched()

    async def test_trash_root_symlink_is_never_followed(self):
        archive = self.archive()
        preview = self.service.preview_trash()
        (self.root / ".trash").rename(self.root / "preserved-archives")
        (self.root / ".trash").symlink_to(self.root / "preserved-archives", target_is_directory=True)
        with self.assertRaises(LearningError):
            self.service.preview_trash()
        with self.assertRaises(LearningError):
            self.service.empty_trash(True, preview["token"])
        self.assertTrue((self.root / "preserved-archives" / archive.name / "record.json").exists())
        self.assert_active_untouched()

    async def test_archive_directory_symlink_is_never_followed(self):
        archive = self.archive()
        preview = self.service.preview_trash()
        outside = self.root / "outside-archive"
        archive.rename(outside)
        archive.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(LearningError):
            self.service.empty_trash(True, preview["token"])
        self.assertTrue((outside / "record.json").exists())
        self.assertTrue(archive.is_symlink())
        self.assert_active_untouched()

    async def test_file_symlink_hardlink_and_unexpected_files_block_all_deletion(self):
        archive = self.archive()
        preview = self.service.preview_trash()
        outside = self.root / "outside-keep.txt"
        outside.write_text("Never remove this")
        unsafe = archive / "unexpected.md"
        unsafe.symlink_to(outside)
        with self.assertRaises(LearningError):
            self.service.empty_trash(True, preview["token"])
        self.assertEqual(outside.read_text(), "Never remove this")
        self.assertTrue((archive / "record.json").exists())
        unsafe.unlink()
        os.link(outside, unsafe)
        with self.assertRaises(LearningError):
            self.service.preview_trash()
        unsafe.unlink()
        unsafe.write_text("foreign file")
        with self.assertRaises(LearningError):
            self.service.preview_trash()
        self.assertEqual(outside.read_text(), "Never remove this")
        self.assert_active_untouched()

    async def test_foreign_top_level_and_traversal_manifest_block_deletion(self):
        archive = self.archive()
        foreign = self.root / ".trash" / "keep.txt"
        foreign.write_text("foreign trash file")
        with self.assertRaises(LearningError):
            self.service.preview_trash()
        self.assertTrue((archive / "record.json").exists())
        foreign.unlink()
        path = archive / "record.json"
        manifest = json.loads(path.read_text())
        manifest["document"]["filename"] = "../../outside"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(LearningError):
            self.service.preview_trash()
        self.assert_active_untouched()

    async def test_directory_replacement_after_preflight_does_not_escape(self):
        archive = self.archive()
        preview = self.service.preview_trash()
        outside = self.root / "outside-keep"
        outside.mkdir()
        (outside / "keep.txt").write_text("Never remove this")
        real_open = os.open
        opens = 0
        def replacing_open(path, flags, *args, **kwargs):
            nonlocal opens
            if path == archive.name:
                opens += 1
                if opens == 2:  # after inventory, just before destructive phase
                    archive.rename(self.root / "moved-disposable")
                    archive.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)
        with patch("app.learning_trash.os.open", side_effect=replacing_open):
            with self.assertRaises(LearningError) as rejected:
                self.service.empty_trash(True, preview["token"])
        self.assertEqual(rejected.exception.status, 503)
        self.assertEqual((outside / "keep.txt").read_text(), "Never remove this")
        self.assert_active_untouched()

    async def test_partial_file_failure_is_reported_and_fresh_confirmation_can_retry(self):
        archive = self.archive(personal=True)
        preview = self.service.preview_trash()
        real_unlink = os.unlink
        def failing_unlink(path, *args, **kwargs):
            if str(path).endswith(".user-notes.md"):
                raise PermissionError("synthetic failure")
            return real_unlink(path, *args, **kwargs)
        with patch("app.learning_trash.os.unlink", side_effect=failing_unlink):
            with self.assertRaises(LearningError) as rejected:
                self.service.empty_trash(True, preview["token"])
        self.assertIn("途中で失敗", rejected.exception.message)
        self.assertIn("一部ファイル", rejected.exception.message)
        self.assertTrue((archive / "record.json").exists())
        fresh = self.service.preview_trash()
        self.assertEqual(fresh["count"], 1)
        self.assertEqual(self.service.empty_trash(True, fresh["token"])["deleted"], 1)
        self.assert_active_untouched()

    async def test_partial_record_failure_reports_completed_count_and_allows_empty_folder_retry(self):
        folders = sorted([self.archive("Disposable A"), self.archive("Disposable B")])
        preview = self.service.preview_trash()
        real_rmdir = os.rmdir
        def failing_rmdir(path, *args, **kwargs):
            if path == folders[1].name:
                raise PermissionError("synthetic failure")
            return real_rmdir(path, *args, **kwargs)
        with patch("app.learning_trash.os.rmdir", side_effect=failing_rmdir):
            with self.assertRaises(LearningError) as rejected:
                self.service.empty_trash(True, preview["token"])
        self.assertIn("1件を削除完了", rejected.exception.message)
        self.assertFalse(folders[0].exists())
        self.assertEqual(list(folders[1].iterdir()), [])
        fresh = self.service.preview_trash()
        self.assertEqual(fresh["count"], 1)
        self.assertEqual(self.service.empty_trash(True, fresh["token"])["deleted"], 1)
        self.assert_active_untouched()

    async def test_http_requires_confirmation_and_never_accepts_a_target_path(self):
        self.archive()
        app = FastAPI()
        app.include_router(router)
        async def fixture_service():
            return self.service
        app.dependency_overrides[resolve_service] = fixture_service
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            base = "/api/learning/trash"
            self.assertEqual((await client.get(base, headers={"Origin": "https://example.test"})).status_code, 403)
            preview = (await client.get(base)).json()
            for body in [{}, {"confirm_permanent": False, "token": preview["token"]},
                         {"confirm_permanent": True, "token": preview["token"], "path": "/tmp"}]:
                self.assertEqual((await client.post(base + "/empty", json=body)).status_code, 422)
            self.assertEqual((await client.get(base)).json()["count"], 1)
            response = await client.post(base + "/empty", json={"confirm_permanent": True, "token": preview["token"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"deleted": 1, "remaining": 0})
            self.assertEqual((await client.get(base)).json()["count"], 0)
        self.assert_active_untouched()
