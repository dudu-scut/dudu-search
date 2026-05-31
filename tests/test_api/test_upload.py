"""File Upload API 端点测试 — 文件类型安全校验。"""

import io

import pytest


class TestFileUpload:
    """POST /api/upload 相关测试。"""

    async def test_upload_allowed_file_type(self, test_app, auth_headers):
        """上传允许的文件类型（.txt）成功。"""
        file_content = b"Hello, this is test content."
        resp = await test_app.post(
            "/api/upload",
            files={"files": ("test.txt", io.BytesIO(file_content), "text/plain")},
            data={"thread_id": "test-thread-upload-001"},
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "uploaded"
        assert "test.txt" in data["files"]

    async def test_upload_csv_allowed(self, test_app, auth_headers):
        """上传 .csv 文件成功。"""
        file_content = b"col1,col2\nval1,val2"
        resp = await test_app.post(
            "/api/upload",
            files={"files": ("data.csv", io.BytesIO(file_content), "text/csv")},
            data={"thread_id": "test-thread-upload-002"},
            headers=auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "uploaded"

    async def test_upload_exe_rejected(self, test_app, auth_headers):
        """上传可执行文件（.exe）被拒绝。"""
        file_content = b"MZ\x90\x00"  # EXE magic bytes
        resp = await test_app.post(
            "/api/upload",
            files={"files": ("malware.exe", io.BytesIO(file_content), "application/x-msdownload")},
            data={"thread_id": "test-thread-upload-003"},
            headers=auth_headers,
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "不支持" in data["error"]["message"]

    async def test_upload_no_extension_rejected(self, test_app, auth_headers):
        """上传无扩展名的文件被拒绝。"""
        file_content = b"some content"
        resp = await test_app.post(
            "/api/upload",
            files={"files": ("noextension", io.BytesIO(file_content), "text/plain")},
            data={"thread_id": "test-thread-upload-004"},
            headers=auth_headers,
        )

        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"

    async def test_upload_requires_auth(self, test_app):
        """未认证时上传被拒绝。"""
        file_content = b"test"
        resp = await test_app.post(
            "/api/upload",
            files={"files": ("test.txt", io.BytesIO(file_content), "text/plain")},
            data={"thread_id": "test-thread-upload-005"},
        )

        assert resp.status_code == 422
