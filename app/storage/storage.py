"""文件对象存储抽象层。

定义 StorageBackend 协议接口和 LocalStorageBackend 本地实现，
为未来迁移到 MinIO / S3 兼容对象存储提供基础。

当前所有文件操作默认使用本地文件系统实现，迁移时只需新增
S3StorageBackend 实现并在 config.py 中切换 STORAGE_BACKEND 即可。
"""

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from app.config import settings


# ── 数据结构 ──


@dataclass
class FileObject:
    """存储对象的元数据。"""

    name: str
    size: int
    mtime: float
    path: str  # 相对于存储根目录的路径


@dataclass
class UploadResult:
    """上传操作的结果。"""

    path: str  # 存储后的相对路径
    size: int


# ── 抽象接口 ──


class StorageBackend(ABC):
    """存储后端抽象基类。

    所有方法使用相对于存储根目录的路径，路径安全校验由实现类负责。
    """

    @abstractmethod
    async def save(self, path: str, content: bytes) -> UploadResult:
        """保存字节内容到指定路径。"""
        ...

    @abstractmethod
    async def save_stream(self, path: str, file_obj) -> UploadResult:
        """保存文件流到指定路径（适用于大文件）。"""
        ...

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """读取文件内容为字节。"""
        ...

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """删除指定文件。返回是否成功删除。"""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """检查文件是否存在。"""
        ...

    @abstractmethod
    async def list_files(self, prefix: str = "") -> list[FileObject]:
        """列出指定前缀下的所有文件。"""
        ...

    @abstractmethod
    async def get_absolute_path(self, path: str) -> Path:
        """获取文件的绝对路径（仅本地实现有意义，S3 实现可下载到临时文件）。"""
        ...

    @abstractmethod
    async def ensure_dir(self, path: str) -> None:
        """确保目录存在（本地实现创建目录，S3 实现为空操作）。"""
        ...

    @abstractmethod
    async def copy(self, src: str, dst: str) -> None:
        """复制文件。"""
        ...

    @abstractmethod
    async def remove_dir(self, path: str) -> bool:
        """删除空目录。返回是否成功。"""
        ...


# ── 本地文件系统实现 ──


class LocalStorageBackend(StorageBackend):
    """本地文件系统存储实现。

    所有路径操作都在指定的根目录下执行，内置路径遍历安全检查。
    """

    def __init__(self, root: Path):
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        """解析路径并验证不超出根目录（防路径遍历）。"""
        resolved = (self._root / path).resolve()
        if not resolved.is_relative_to(self._root):
            raise PermissionError(f"路径越权: {path}")
        return resolved

    async def save(self, path: str, content: bytes) -> UploadResult:
        abs_path = self._safe_path(path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return UploadResult(path=path, size=len(content))

    async def save_stream(self, path: str, file_obj) -> UploadResult:
        abs_path = self._safe_path(path)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with abs_path.open("wb") as buffer:
            shutil.copyfileobj(file_obj, buffer)
        return UploadResult(path=path, size=abs_path.stat().st_size)

    async def read(self, path: str) -> bytes:
        abs_path = self._safe_path(path)
        return abs_path.read_bytes()

    async def delete(self, path: str) -> bool:
        abs_path = self._safe_path(path)
        if abs_path.exists():
            abs_path.unlink()
            return True
        return False

    async def exists(self, path: str) -> bool:
        try:
            return self._safe_path(path).exists()
        except PermissionError:
            return False

    async def list_files(self, prefix: str = "") -> list[FileObject]:
        search_dir = self._safe_path(prefix) if prefix else self._root
        if not search_dir.exists():
            return []
        files = []
        for fp in search_dir.rglob("*"):
            if fp.is_file():
                stat = fp.stat()
                files.append(FileObject(
                    name=fp.name,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    path=str(fp.relative_to(self._root)),
                ))
        files.sort(key=lambda f: f.mtime, reverse=True)
        return files

    async def get_absolute_path(self, path: str) -> Path:
        return self._safe_path(path)

    async def ensure_dir(self, path: str) -> None:
        self._safe_path(path).mkdir(parents=True, exist_ok=True)

    async def copy(self, src: str, dst: str) -> None:
        src_path = self._safe_path(src)
        dst_path = self._safe_path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        # 尝试硬链接（同一文件系统最快），失败则回退到复制
        try:
            os.link(src_path, dst_path)
        except OSError:
            shutil.copy2(src_path, dst_path)

    async def remove_dir(self, path: str) -> bool:
        abs_path = self._safe_path(path)
        if abs_path.exists() and abs_path.is_dir():
            try:
                abs_path.rmdir()  # 仅删除空目录
                return True
            except OSError:
                return False
        return False


# ── 跨存储复制 ──


async def copy_between_storage(
    src_backend: StorageBackend,
    src_path: str,
    dst_backend: StorageBackend,
    dst_path: str,
) -> None:
    """在不同存储后端之间复制文件（读取源 → 写入目标）。

    对于两个 LocalStorageBackend 指向同一文件系统的情况，
    自动优化为硬链接（O(1)），失败则回退到物理复制。
    """
    if isinstance(src_backend, LocalStorageBackend) and isinstance(dst_backend, LocalStorageBackend):
        src_abs = await src_backend.get_absolute_path(src_path)
        dst_abs = await dst_backend.get_absolute_path(dst_path)
        dst_abs.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src_abs, dst_abs)
        except OSError:
            shutil.copy2(src_abs, dst_abs)
    else:
        content = await src_backend.read(src_path)
        await dst_backend.save(dst_path, content)


# ── 工厂函数 ──

# 全局存储实例（懒初始化）
_output_storage: Optional[StorageBackend] = None
_upload_storage: Optional[StorageBackend] = None
_doc_storage: Optional[StorageBackend] = None


def get_output_storage() -> StorageBackend:
    """获取会话输出文件存储后端。"""
    global _output_storage
    if _output_storage is None:
        project_root = Path(__file__).resolve().parent.parent
        _output_storage = LocalStorageBackend(project_root / "output")
    return _output_storage


def get_upload_storage() -> StorageBackend:
    """获取用户上传文件存储后端。"""
    global _upload_storage
    if _upload_storage is None:
        project_root = Path(__file__).resolve().parent.parent
        _upload_storage = LocalStorageBackend(project_root / "updated")
    return _upload_storage


def get_doc_storage() -> StorageBackend:
    """获取知识库文档存储后端。"""
    global _doc_storage
    if _doc_storage is None:
        from app.self_rag.config import DOC_STORE_DIR
        _doc_storage = LocalStorageBackend(Path(DOC_STORE_DIR))
    return _doc_storage
