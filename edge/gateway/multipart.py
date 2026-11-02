"""메모리 전용 multipart/form-data 파서.

Starlette 의 request.form() 은 파일 파트를 SpooledTemporaryFile 로 받아 1 MiB 를 넘으면 디스크로
넘긴다. Gateway 는 프레임을 디스크에 쓰지 않으므로 python-multipart 저수준 파서로 직접 처리한다.
요청 본문 전체 크기는 호출 전에 GATEWAY_MAX_REQUEST_BYTES 로 제한돼 있다고 가정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, MultipartState, parse_options_header


class MultipartError(ValueError):
    pass


@dataclass
class Part:
    name: Optional[str]
    content_type: Optional[str]  # 소문자, 파라미터 제외 (예: "image/jpeg")
    filename: Optional[str] = None
    data: bytearray = field(default_factory=bytearray)


def boundary_from_content_type(content_type: str) -> Optional[bytes]:
    """multipart/form-data 이면 boundary, 아니면 None."""
    ctype, params = parse_options_header(content_type)
    if ctype.lower() != b"multipart/form-data":
        return None
    boundary = params.get(b"boundary")
    if not boundary:
        raise MultipartError("multipart boundary missing")
    return boundary


def parse(body: bytes, boundary: bytes) -> Dict[str, Part]:
    """이름별 파트. 같은 이름이 두 번 나오면 오류."""
    parts: List[Part] = []
    headers: Dict[bytes, bytes] = {}
    cur_field = bytearray()
    cur_value = bytearray()
    current: List[Optional[Part]] = [None]

    def on_part_begin() -> None:
        headers.clear()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        cur_field.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        cur_value.extend(data[start:end])

    def on_header_end() -> None:
        headers[bytes(cur_field).lower()] = bytes(cur_value)
        cur_field.clear()
        cur_value.clear()

    def on_headers_finished() -> None:
        _, disp = parse_options_header(headers.get(b"content-disposition", b""))
        ctype = headers.get(b"content-type")
        name = disp.get(b"name")
        filename = disp.get(b"filename")
        part = Part(
            name=name.decode("utf-8", "replace") if name is not None else None,
            content_type=parse_options_header(ctype)[0].decode("latin-1").lower() if ctype else None,
            filename=filename.decode("utf-8", "replace") if filename is not None else None,
        )
        current[0] = part
        parts.append(part)

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part = current[0]
        if part is not None:
            part.data.extend(data[start:end])

    def on_part_end() -> None:
        current[0] = None

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    try:
        parser.write(body)
    except MultipartParseError as e:
        raise MultipartError(f"malformed multipart body: {e}") from None
    if parser.state != MultipartState.END:  # 닫는 boundary(--boundary--)까지 받지 못함
        raise MultipartError("incomplete multipart body")

    by_name: Dict[str, Part] = {}
    for p in parts:
        if p.name is None:
            raise MultipartError("multipart part without name")
        if p.name in by_name:
            raise MultipartError(f"duplicate multipart part {p.name!r}")
        by_name[p.name] = p
    return by_name
