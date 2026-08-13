"""Bounded attachment collection and CID image embedding for outbound mail."""

from __future__ import annotations

import asyncio
import hashlib
import html
import mimetypes
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from astrbot.api.message_components import File, Image, Reply

from .config import MailRelaySettings
from .policy import MailRelayValidationError

_INLINE_PLACEHOLDER_RE = re.compile(r"\{\{\s*image_(\d+)\s*\}\}", re.IGNORECASE)
_INLINE_SRC_RE = re.compile(
    r"(?P<prefix>\bsrc\s*=\s*)(?P<quote>['\"])\s*"
    r"\{\{\s*image_(?P<index>\d+)\s*\}\}\s*(?P=quote)",
    re.IGNORECASE,
)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_RESERVED_CHARS_RE = re.compile(r'[:*?"<>|]')


@dataclass(frozen=True)
class MailAttachment:
    """An in-memory MIME attachment with optional inline Content-ID."""

    filename: str
    content_type: str
    data: bytes
    content_id: str | None = None

    @property
    def is_inline(self) -> bool:
        return self.content_id is not None

    @property
    def is_embeddable_image(self) -> bool:
        return self.content_type in {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/bmp",
        }


@dataclass(frozen=True)
class MailMediaSummary:
    """Small metadata record suitable for delivery history and responses."""

    attachment_count: int = 0
    inline_image_count: int = 0
    total_bytes: int = 0
    filenames: tuple[str, ...] = ()


def contains_cid_placeholders(value: str | None) -> bool:
    """Return whether a template contains one or more supported image tokens."""

    return bool(_INLINE_PLACEHOLDER_RE.search(str(value or "")))


async def collect_mail_attachments(
    *,
    context: Any | None,
    event: Any,
    settings: MailRelaySettings,
    include_message_media: bool,
    workspace_file_paths: Iterable[str],
) -> list[MailAttachment]:
    """Collect current/replied chat media and approved workspace files."""

    requested_paths = [str(value or "").strip() for value in workspace_file_paths]
    requested_paths = [value for value in requested_paths if value]
    if not include_message_media and not requested_paths:
        return []

    message_components: list[Image | File] = []
    if include_message_media:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None) or []
        message_components = list(_iter_media_components(chain))
    if not settings.enable_attachments:
        if requested_paths or message_components:
            raise MailRelayValidationError("管理员尚未开启邮件附件功能。")
        return []
    if requested_paths and not settings.allow_workspace_attachments:
        raise MailRelayValidationError("管理员尚未允许爱丽丝附加工作区文件。")
    if requested_paths and context is None:
        raise MailRelayValidationError("无法确认当前会话工作区，不能附加工作区文件。")

    attachments: list[MailAttachment] = []
    seen_digests: set[str] = set()
    used_names: set[str] = set()
    total_bytes = 0

    async def append_path(
        path: str,
        *,
        preferred_name: str,
        image_hint: bool,
        remove_after_read: bool = False,
    ) -> None:
        nonlocal total_bytes
        try:
            attachment = await _attachment_from_path(
                path,
                preferred_name=preferred_name,
                image_hint=image_hint,
                settings=settings,
            )
        finally:
            if remove_after_read:
                await asyncio.to_thread(_unlink_quietly, path)

        digest = hashlib.sha256(attachment.data).hexdigest()
        if digest in seen_digests:
            return
        if len(attachments) >= settings.max_attachments_per_message:
            raise MailRelayValidationError(
                f"单封邮件最多允许 {settings.max_attachments_per_message} 个附件。"
            )
        next_total = total_bytes + len(attachment.data)
        if next_total > settings.max_total_attachment_bytes:
            raise MailRelayValidationError(
                "附件总大小超过配置上限 "
                f"{settings.max_total_attachment_size_mb} MB。"
            )
        attachment = replace(
            attachment,
            filename=_unique_filename(attachment.filename, used_names),
        )
        attachments.append(attachment)
        seen_digests.add(digest)
        used_names.add(attachment.filename.casefold())
        total_bytes = next_total

    if include_message_media:
        image_number = 0
        file_number = 0
        for component in message_components:
            if isinstance(component, Image):
                if not settings.allow_message_images:
                    continue
                image_number += 1
                try:
                    path = await asyncio.wait_for(
                        component.convert_to_file_path(),
                        timeout=settings.attachment_fetch_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise MailRelayValidationError(
                        f"第 {image_number} 张 QQ 图片读取超时。"
                    ) from exc
                except Exception as exc:
                    raise MailRelayValidationError(
                        f"第 {image_number} 张 QQ 图片无法读取。"
                    ) from exc
                await append_path(
                    str(path or ""),
                    preferred_name=f"image_{image_number}",
                    image_hint=True,
                )
            elif isinstance(component, File):
                if not settings.allow_message_files:
                    continue
                file_number += 1
                try:
                    path = await asyncio.wait_for(
                        component.get_file(),
                        timeout=settings.attachment_fetch_timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise MailRelayValidationError(
                        f"第 {file_number} 个 QQ 文件读取超时。"
                    ) from exc
                except Exception as exc:
                    raise MailRelayValidationError(
                        f"第 {file_number} 个 QQ 文件无法读取。"
                    ) from exc
                await append_path(
                    str(path or ""),
                    preferred_name=str(getattr(component, "name", "") or "file"),
                    image_hint=False,
                )

    for raw_path in requested_paths:
        resolved_path, temporary = await _resolve_workspace_file(
            context,
            raw_path,
            timeout_seconds=settings.attachment_fetch_timeout_seconds,
        )
        await append_path(
            resolved_path,
            preferred_name=_portable_basename(raw_path) or "file",
            image_hint=False,
            remove_after_read=temporary,
        )

    return attachments


def embed_cid_placeholders(
    html_body: str,
    attachments: list[MailAttachment],
    *,
    cid_domain: str,
) -> tuple[str, list[MailAttachment]]:
    """Replace ``{{image_N}}`` placeholders and mark referenced images inline."""

    body = str(html_body or "")
    matches = list(_INLINE_PLACEHOLDER_RE.finditer(body))
    if not matches:
        return body, attachments

    image_positions = [
        index for index, item in enumerate(attachments) if item.is_embeddable_image
    ]
    requested = {int(match.group(1)) for match in matches}
    if 0 in requested or any(index > len(image_positions) for index in requested):
        available = len(image_positions)
        raise MailRelayValidationError(
            "HTML 图片占位符没有对应附件。"
            f"当前可嵌入图片为 image_1 至 image_{available}。"
            if available
            else "HTML 图片占位符没有对应附件；当前消息中没有可嵌入图片。"
        )

    updated = list(attachments)
    cid_by_number: dict[int, str] = {}
    for number in sorted(requested):
        attachment_index = image_positions[number - 1]
        cid = _new_content_id(number, cid_domain)
        updated[attachment_index] = replace(updated[attachment_index], content_id=cid)
        cid_by_number[number] = cid

    def replace_src(match: re.Match[str]) -> str:
        number = int(match.group("index"))
        quote = match.group("quote")
        return f"{match.group('prefix')}{quote}cid:{cid_by_number[number]}{quote}"

    body = _INLINE_SRC_RE.sub(replace_src, body)

    def replace_bare(match: re.Match[str]) -> str:
        number = int(match.group(1))
        attachment = updated[image_positions[number - 1]]
        alt = html.escape(attachment.filename, quote=True)
        return f'<img src="cid:{cid_by_number[number]}" alt="{alt}">'

    body = _INLINE_PLACEHOLDER_RE.sub(replace_bare, body)
    return body, updated


def summarize_media(attachments: Iterable[MailAttachment]) -> MailMediaSummary:
    items = tuple(attachments)
    return MailMediaSummary(
        attachment_count=len(items),
        inline_image_count=sum(1 for item in items if item.is_inline),
        total_bytes=sum(len(item.data) for item in items),
        filenames=tuple(item.filename for item in items),
    )


def _iter_media_components(chain: Iterable[Any], *, depth: int = 0):
    if depth > 3:
        return
    for component in chain:
        if isinstance(component, (Image, File)):
            yield component
        elif isinstance(component, Reply):
            reply_chain = getattr(component, "chain", None) or []
            yield from _iter_media_components(reply_chain, depth=depth + 1)


async def _attachment_from_path(
    path_value: str,
    *,
    preferred_name: str,
    image_hint: bool,
    settings: MailRelaySettings,
) -> MailAttachment:
    path_text = str(path_value or "").strip()
    if not path_text:
        raise MailRelayValidationError("附件没有可读取的本地文件。")
    path = Path(path_text)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MailRelayValidationError("附件文件不存在或无法访问。") from exc
    if not resolved.is_file():
        raise MailRelayValidationError("附件路径不是普通文件。")

    data = await asyncio.to_thread(
        _read_bounded,
        resolved,
        settings.max_attachment_bytes,
    )
    content_type, extension = _detect_content_type(
        resolved.name,
        data,
        image_hint=image_hint,
    )
    if _looks_like_executable(data):
        suffix = Path(preferred_name or resolved.name).suffix.casefold()
        raise MailRelayValidationError(
            f"安全策略禁止发送可执行内容{suffix or ''}。"
        )
    filename = _sanitize_filename(preferred_name or resolved.name, extension=extension)
    suffix = Path(filename).suffix.casefold()
    if suffix and suffix in settings.blocked_attachment_extensions:
        raise MailRelayValidationError(f"安全策略禁止发送 {suffix} 类型的附件。")
    return MailAttachment(filename=filename, content_type=content_type, data=data)


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
        if size <= 0:
            raise MailRelayValidationError("不能发送空附件。")
        if size > maximum_bytes:
            raise MailRelayValidationError(
                f"附件 {path.name} 超过单文件大小上限。"
            )
        with path.open("rb") as stream:
            data = stream.read(maximum_bytes + 1)
    except MailRelayValidationError:
        raise
    except OSError as exc:
        raise MailRelayValidationError(f"附件 {path.name} 无法读取。") from exc
    if len(data) > maximum_bytes:
        raise MailRelayValidationError(f"附件 {path.name} 超过单文件大小上限。")
    return data


async def _resolve_workspace_file(
    context: Any,
    raw_path: str,
    *,
    timeout_seconds: int,
) -> tuple[str, bool]:
    """Resolve a tool path using the active AstrBot workspace/runtime.

    AstrBot 4.27 exposes ``workspace_root(umo)`` while newer builds also
    expose the context-aware ``workspace_root_for_context`` helper.  Keep the
    attachment feature compatible with both APIs and only resolve a host path
    when the current tool runtime is local.
    """

    try:
        from astrbot.core.tools.computer_tools.util import is_local_runtime

        local_runtime = bool(is_local_runtime(context))
    except Exception as exc:
        raise MailRelayValidationError("无法确认当前会话运行时。") from exc

    if local_runtime:
        try:
            from astrbot.core.tools.computer_tools.util import (
                workspace_root_for_context,
            )
        except ImportError:
            try:
                from astrbot.core.tools.computer_tools.util import workspace_root

                umo = str(
                    getattr(
                        getattr(getattr(context, "context", None), "event", None),
                        "unified_msg_origin",
                        "",
                    )
                    or ""
                )
                workspace_root_path = workspace_root(umo)
            except Exception as exc:
                raise MailRelayValidationError("无法确认当前会话工作区。") from exc
        else:
            try:
                workspace_root_path = await workspace_root_for_context(context)
            except Exception as exc:
                raise MailRelayValidationError("无法确认当前会话工作区。") from exc

        workspace_root = Path(workspace_root_path).resolve(strict=False)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise MailRelayValidationError(
                f"工作区文件不存在：{_portable_basename(raw_path) or raw_path}。"
            ) from exc
        event = getattr(getattr(context, "context", None), "event", None)
        tracked_temp_paths = {
            Path(str(value)).resolve(strict=False)
            for value in (getattr(event, "_temporary_local_files", None) or ())
            if str(value or "").strip()
        }
        if not resolved.is_relative_to(workspace_root) and resolved not in tracked_temp_paths:
            raise MailRelayValidationError(
                "工作区附件路径越界；只允许当前会话工作区，或当前事件已登记的临时文件。"
            )
        if not resolved.is_file():
            raise MailRelayValidationError("工作区附件路径不是普通文件。")
        return str(resolved), False

    # A generated image/file may be a host-local temporary path even when the
    # computer-use runtime is disabled.  Permit it only when AstrBot explicitly
    # registered the path on the current event; arbitrary absolute paths remain
    # rejected below.
    event = getattr(getattr(context, "context", None), "event", None)
    tracked_temp_paths = {
        Path(str(value)).resolve(strict=False)
        for value in (getattr(event, "_temporary_local_files", None) or ())
        if str(value or "").strip()
    }
    try:
        candidate = Path(raw_path).resolve(strict=True)
    except (OSError, RuntimeError):
        candidate = None
    if candidate is not None and candidate in tracked_temp_paths and candidate.is_file():
        return str(candidate), False

    remote_path = _safe_sandbox_path(raw_path)
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    filename = _sanitize_filename(_portable_basename(remote_path) or "file")
    local_path = Path(get_astrbot_temp_path()) / (
        f"mailrelay_{secrets.token_hex(6)}_{filename}"
    )
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from astrbot.core.computer.computer_client import get_booter

        booter = await get_booter(
            context.context.context,
            context.context.event.unified_msg_origin,
        )
        await asyncio.wait_for(
            booter.download_file(remote_path, str(local_path)),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        _unlink_quietly(local_path)
        raise MailRelayValidationError("下载爱丽丝工作区文件超时。") from exc
    except Exception as exc:
        _unlink_quietly(local_path)
        raise MailRelayValidationError("无法从爱丽丝工作区取得指定文件。") from exc
    return str(local_path), True


def _safe_sandbox_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if any(part == ".." for part in candidate.parts):
        raise MailRelayValidationError("工作区附件路径不能包含上级目录跳转。")
    if candidate.is_absolute():
        if candidate != PurePosixPath("/workspace") and not candidate.is_relative_to(
            PurePosixPath("/workspace")
        ):
            raise MailRelayValidationError("沙箱附件只能来自 /workspace。")
        return str(candidate)
    return str(PurePosixPath("/workspace") / candidate)


def _detect_content_type(
    filename: str,
    data: bytes,
    *,
    image_hint: bool,
) -> tuple[str, str]:
    image_type = _image_type_from_magic(data)
    if image_type is not None:
        return image_type

    guessed, _ = mimetypes.guess_type(_portable_basename(filename))
    if image_hint and (guessed or "").startswith("image/"):
        extension = mimetypes.guess_extension(guessed or "") or ""
        return guessed or "application/octet-stream", extension
    return guessed or "application/octet-stream", ""


def _looks_like_executable(data: bytes) -> bool:
    """Reject common executable/script signatures even when renamed."""

    stripped = data.lstrip()
    return bool(
        data.startswith(
            (
                b"MZ",
                b"\x7fELF",
                b"\xfe\xed\xfa\xce",
                b"\xce\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
                b"\xcf\xfa\xed\xfe",
            )
        )
        or stripped.startswith(b"#!")
    )


def _image_type_from_magic(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"BM"):
        return "image/bmp", ".bmp"
    return None


def _sanitize_filename(value: str, *, extension: str = "") -> str:
    basename = _portable_basename(value)
    basename = _CONTROL_CHARS_RE.sub("", basename)
    basename = _WINDOWS_RESERVED_CHARS_RE.sub("_", basename).strip(" .")
    if not basename or basename in {".", ".."}:
        basename = "attachment"
    if extension and not Path(basename).suffix:
        basename += extension
    suffix = Path(basename).suffix
    stem = Path(basename).stem
    maximum_stem = max(1, 180 - len(suffix))
    return f"{stem[:maximum_stem]}{suffix}"


def _portable_basename(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    return PurePosixPath(normalized).name


def _unique_filename(filename: str, used_names: set[str]) -> str:
    if filename.casefold() not in used_names:
        return filename
    path = Path(filename)
    for number in range(2, 10_000):
        candidate = f"{path.stem}-{number}{path.suffix}"
        if candidate.casefold() not in used_names:
            return candidate
    raise MailRelayValidationError("附件文件名重复过多。")


def _new_content_id(number: int, domain: str) -> str:
    safe_domain = re.sub(r"[^A-Za-z0-9.-]", "", str(domain or "")) or "localhost"
    return f"mailrelay-image-{number}-{secrets.token_hex(8)}@{safe_domain}"


def _unlink_quietly(path: str | Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        return
