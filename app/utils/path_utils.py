"""
文件路径解析工具

负责把模型或工具返回的虚拟路径、上传文件路径和相对路径统一转换为本地绝对路径
后续文件读取、Markdown 生成和 PDF 转换工具都可以复用这里的解析规则
"""

import os
from pathlib import Path
from typing import Optional


def resolve_path(filename: str, session_dir: Optional[str] = None, updated_dir: Optional[str] = None) -> str:
    """
    解析文件路径，并将任务产物限制在当前会话目录中

    :param filename: 模型、工具或用户传入的文件名/路径
    :param session_dir: 当前任务的会话目录
    :param updated_dir: 上传文件根目录（updated/），用于校验路径沙箱
    :return: 解析后的绝对路径
    """
    path = Path(filename)
    path_str = filename.replace("\\", "/")

    # 大模型常返回 /workspace、/mnt/data 这类沙箱路径，本地项目需要先剥离虚拟前缀
    for prefix in ["/workspace", "/mnt/data", "/home/user"]:
        if path_str.startswith(prefix):
            cleaned = path_str[len(prefix) :].lstrip("/")
            path = Path(cleaned)
            path_str = str(path).replace("\\", "/")
            break

    # updated/ 用于存放用户上传文件，应优先按项目根目录下的真实上传路径解析
    if "updated/" in path_str:
        idx = path_str.find("updated/")
        relative_part = path_str[idx:]

        if updated_dir:
            resolved = (Path(updated_dir) / relative_part[len("updated/"):]).resolve()
            updated_base = Path(updated_dir).resolve()
        else:
            resolved = Path(relative_part).resolve()
            updated_base = Path("updated").resolve()

        # 沙箱校验：防止 updated/../../etc/passwd 这类路径穿越攻击
        if not (updated_base in resolved.parents or resolved == updated_base):
            raise ValueError(f"路径 '{path_str}' 越界，不在上传目录内，操作被拒绝")

        return str(resolved)

    if not session_dir:
        return str(path.resolve())

    session_path = Path(session_dir).resolve()
    session_name = session_path.name
    is_unix_abs = path_str.startswith("/")

    if path.is_absolute() or (os.name == "nt" and is_unix_abs):
        # Windows 下 "/xxx" 没有盘符，按会话目录内的相对路径处理
        if os.name == "nt" and is_unix_abs and not path.drive:
            full_path = session_path / path_str.lstrip("/")
        else:
            full_path = path.resolve()

        try:
            if session_path in full_path.parents or full_path == session_path:
                return _fix_nested_session_path(full_path, session_path, session_name)
        except Exception:
            pass

        # 绝对路径不在 session_dir 内 → 拒绝，防御路径穿越攻击
        raise ValueError(f"路径 '{path_str}' 不在当前会话目录内，操作被拒绝")

    parts = path.parts

    # 避免模型把 session 名或 output 前缀重复拼到当前会话目录里
    if session_name in parts:
        return str(session_path / path.name)

    if parts and parts[0] == "output":
        return str(session_path / path.name)

    return str(session_path / path)


def _fix_nested_session_path(
    full_path: Path,
    session_path: Path,
    session_name: str,
) -> str:
    """
    修正 session_xxx/session_xxx/file.md 这类重复嵌套路径
    """
    parts = full_path.parts
    for index in range(len(parts) - 1):
        if parts[index] == session_name and parts[index + 1] == session_name:
            return str(session_path / full_path.name)
    return str(full_path)
