#!/usr/bin/env python3
"""Generate LuaCATS and opcode lookup tables from a protobuf descriptor set."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / "schema.pb"
OUTPUT_DIR = SCRIPT_DIR.parent / "lualib" / "pb"
ERROR_PROTO_PATH = "client/common/error.proto"
ERROR_ENUM_NAME = "ErrorCode"

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5

LABEL_REPEATED = 3
TYPE_DOUBLE = 1
TYPE_FLOAT = 2
TYPE_INT64 = 3
TYPE_UINT64 = 4
TYPE_INT32 = 5
TYPE_FIXED64 = 6
TYPE_FIXED32 = 7
TYPE_BOOL = 8
TYPE_STRING = 9
TYPE_GROUP = 10
TYPE_MESSAGE = 11
TYPE_BYTES = 12
TYPE_UINT32 = 13
TYPE_ENUM = 14
TYPE_SFIXED32 = 15
TYPE_SFIXED64 = 16
TYPE_SINT32 = 17
TYPE_SINT64 = 18

INTEGER_TYPES = {
    TYPE_INT64,
    TYPE_UINT64,
    TYPE_INT32,
    TYPE_FIXED64,
    TYPE_FIXED32,
    TYPE_UINT32,
    TYPE_SFIXED32,
    TYPE_SFIXED64,
    TYPE_SINT32,
    TYPE_SINT64,
}


@dataclass
class ProtoField:
    name: str
    label: int
    type: int
    type_name: str = ""


@dataclass
class ProtoEnum:
    name: str
    full_name: str
    values: list[tuple[str, int]]


@dataclass
class ProtoMessage:
    name: str
    full_name: str
    fields: list[ProtoField] = field(default_factory=list)
    messages: list["ProtoMessage"] = field(default_factory=list)
    enums: list[ProtoEnum] = field(default_factory=list)
    map_entry: bool = False


@dataclass
class ProtoFile:
    name: str
    package: str
    messages: list[ProtoMessage]
    enums: list[ProtoEnum]


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Read one unsigned protobuf varint."""
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid or truncated protobuf varint")


def parse_wire_message(data: bytes) -> dict[int, list[int | bytes]]:
    """Decode the wire fields needed from a protobuf descriptor message."""
    fields: dict[int, list[int | bytes]] = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number = tag >> 3
        wire_type = tag & 7
        if number == 0:
            raise ValueError("invalid protobuf field number 0")

        if wire_type == WIRE_VARINT:
            value, offset = read_varint(data, offset)
        elif wire_type == WIRE_LENGTH_DELIMITED:
            size, offset = read_varint(data, offset)
            end = offset + size
            if end > len(data):
                raise ValueError("truncated length-delimited protobuf field")
            value = data[offset:end]
            offset = end
        elif wire_type == WIRE_FIXED64:
            end = offset + 8
            if end > len(data):
                raise ValueError("truncated fixed64 protobuf field")
            value = data[offset:end]
            offset = end
        elif wire_type == WIRE_FIXED32:
            end = offset + 4
            if end > len(data):
                raise ValueError("truncated fixed32 protobuf field")
            value = data[offset:end]
            offset = end
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        fields.setdefault(number, []).append(value)
    return fields


def first_bytes(fields: dict[int, list[int | bytes]], number: int) -> bytes:
    value = fields.get(number, [b""])[0]
    if not isinstance(value, bytes):
        raise ValueError(f"descriptor field {number} must be bytes")
    return value


def first_int(
    fields: dict[int, list[int | bytes]], number: int, default: int = 0
) -> int:
    value = fields.get(number, [default])[0]
    if not isinstance(value, int):
        raise ValueError(f"descriptor field {number} must be an integer")
    return value


def text_field(fields: dict[int, list[int | bytes]], number: int) -> str:
    return first_bytes(fields, number).decode("utf-8")


def repeated_bytes(
    fields: dict[int, list[int | bytes]], number: int
) -> list[bytes]:
    values = fields.get(number, [])
    if not all(isinstance(value, bytes) for value in values):
        raise ValueError(f"descriptor field {number} must contain bytes")
    return values  # type: ignore[return-value]


def parse_enum(data: bytes, parent_name: str) -> ProtoEnum:
    fields = parse_wire_message(data)
    name = text_field(fields, 1)
    values: list[tuple[str, int]] = []
    for raw_value in repeated_bytes(fields, 2):
        value_fields = parse_wire_message(raw_value)
        number = first_int(value_fields, 2) & 0xFFFFFFFF
        if number >= 1 << 31:
            number -= 1 << 32
        values.append((text_field(value_fields, 1), number))
    return ProtoEnum(name, f"{parent_name}.{name}", values)


def parse_message(data: bytes, parent_name: str) -> ProtoMessage:
    fields = parse_wire_message(data)
    name = text_field(fields, 1)
    full_name = f"{parent_name}.{name}"
    message = ProtoMessage(name, full_name)

    for raw_field in repeated_bytes(fields, 2):
        field_fields = parse_wire_message(raw_field)
        message.fields.append(
            ProtoField(
                name=text_field(field_fields, 1),
                label=first_int(field_fields, 4),
                type=first_int(field_fields, 5),
                type_name=text_field(field_fields, 6),
            )
        )
    message.messages = [
        parse_message(raw_message, full_name)
        for raw_message in repeated_bytes(fields, 3)
    ]
    message.enums = [
        parse_enum(raw_enum, full_name) for raw_enum in repeated_bytes(fields, 4)
    ]

    options = first_bytes(fields, 7)
    if options:
        message.map_entry = bool(first_int(parse_wire_message(options), 7))
    return message


def parse_file(data: bytes) -> ProtoFile:
    fields = parse_wire_message(data)
    name = text_field(fields, 1)
    package = text_field(fields, 2)
    parent_name = f".{package}" if package else ""
    return ProtoFile(
        name=name,
        package=package,
        messages=[
            parse_message(raw_message, parent_name)
            for raw_message in repeated_bytes(fields, 4)
        ],
        enums=[
            parse_enum(raw_enum, parent_name)
            for raw_enum in repeated_bytes(fields, 5)
        ],
    )


def load_schema(path: Path) -> list[ProtoFile]:
    """Load FileDescriptorSet without requiring the Python protobuf package."""
    fields = parse_wire_message(path.read_bytes())
    files = [parse_file(raw_file) for raw_file in repeated_bytes(fields, 1)]
    if not files:
        raise ValueError(f"descriptor set contains no files: {path}")
    return files


def walk_messages(messages: list[ProtoMessage]):
    for message in messages:
        yield message
        yield from walk_messages(message.messages)


def walk_enums(messages: list[ProtoMessage]):
    for message in messages:
        yield from message.enums
        yield from walk_enums(message.messages)


def lua_type_name(full_name: str, package: str = "") -> str:
    relative_name = full_name.lstrip(".")
    if package and relative_name.startswith(f"{package}."):
        relative_name = relative_name[len(package) + 1 :]
    return f"pb.msg.{relative_name}"


def is_error_proto(proto_file: ProtoFile) -> bool:
    """Return whether a descriptor file is the client error definition."""
    return proto_file.name.replace("\\", "/").endswith(ERROR_PROTO_PATH)


def field_type(
    proto_field: ProtoField,
    messages_by_name: dict[str, ProtoMessage],
    lua_names: dict[str, str],
) -> str:
    if proto_field.type in INTEGER_TYPES:
        result = "integer"
    elif proto_field.type in {TYPE_DOUBLE, TYPE_FLOAT}:
        result = "number"
    elif proto_field.type == TYPE_BOOL:
        result = "boolean"
    elif proto_field.type in {TYPE_STRING, TYPE_BYTES}:
        result = "string"
    elif proto_field.type in {TYPE_MESSAGE, TYPE_ENUM, TYPE_GROUP}:
        result = lua_names.get(
            proto_field.type_name, lua_type_name(proto_field.type_name)
        )
    else:
        raise ValueError(
            f"unsupported descriptor field type {proto_field.type}: "
            f"{proto_field.name}"
        )

    if proto_field.label != LABEL_REPEATED:
        return result

    map_message = messages_by_name.get(proto_field.type_name)
    if map_message is not None and map_message.map_entry:
        if len(map_message.fields) != 2:
            raise ValueError(f"invalid map entry descriptor: {map_message.full_name}")
        key_type = field_type(map_message.fields[0], messages_by_name, lua_names)
        value_type = field_type(map_message.fields[1], messages_by_name, lua_names)
        return f"table<{key_type}, {value_type}>"
    return f"{result}[]"


def render_cats(files: list[ProtoFile]) -> str:
    messages = [
        message
        for proto_file in files
        for message in walk_messages(proto_file.messages)
    ]
    messages_by_name = {message.full_name: message for message in messages}
    if len(messages_by_name) != len(messages):
        raise ValueError("duplicate protobuf message full name")
    lua_names: dict[str, str] = {}
    for proto_file in files:
        for message in walk_messages(proto_file.messages):
            lua_names[message.full_name] = lua_type_name(
                message.full_name, proto_file.package
            )
        for proto_enum in [*proto_file.enums, *walk_enums(proto_file.messages)]:
            lua_names[proto_enum.full_name] = lua_type_name(
                proto_enum.full_name, proto_file.package
            )

    lines = [
        "---@meta",
        "",
        "-- Code generated by proto/gen_lua.py. DO NOT EDIT.",
        "",
    ]
    for proto_file in files:
        if is_error_proto(proto_file):
            continue
        enums = [
            *proto_file.enums,
            *walk_enums(proto_file.messages),
        ]
        for proto_enum in enums:
            if proto_enum.name in {"Opcode", "OpcodeLock"}:
                continue
            lines.append(f"---@enum {lua_names[proto_enum.full_name]}")
            lines.append(f"local {proto_enum.name} = {{")
            for name, number in proto_enum.values:
                lines.append(f"    {name} = {number},")
            lines.extend(["}", ""])

        for message in walk_messages(proto_file.messages):
            if message.map_entry:
                continue
            lines.append(f"---@class {lua_names[message.full_name]}")
            for proto_field in message.fields:
                lines.append(
                    f"---@field {proto_field.name}? "
                    f"{field_type(proto_field, messages_by_name, lua_names)}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def find_error_enum(files: list[ProtoFile]) -> ProtoEnum:
    matches = [
        proto_enum
        for proto_file in files
        if is_error_proto(proto_file)
        for proto_enum in proto_file.enums
        if proto_enum.name == ERROR_ENUM_NAME
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {ERROR_ENUM_NAME} enum in "
            f"{ERROR_PROTO_PATH}, found {len(matches)}"
        )
    return matches[0]


def render_error(proto_enum: ProtoEnum) -> str:
    lines = [
        "-- Code generated by proto/gen_lua.py. DO NOT EDIT.",
        "",
        "---@enum pb.error",
        "local M = {",
    ]
    rendered_names: set[str] = set()
    for name, number in proto_enum.values:
        _, separator, rendered_name = name.partition("_")
        if not separator or not rendered_name:
            raise ValueError(
                f"error enum value must contain a non-empty prefix: {name}"
            )
        if rendered_name in rendered_names:
            raise ValueError(f"duplicate rendered error name: {rendered_name}")
        rendered_names.add(rendered_name)
        lines.append(f"    {rendered_name} = {number},")
    lines.extend(["}", "", "return M", ""])
    return "\n".join(lines)


def find_opcode_enum(files: list[ProtoFile]) -> ProtoEnum:
    matches = [
        proto_enum
        for proto_file in files
        for proto_enum in proto_file.enums
        if proto_enum.name == "Opcode"
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one Opcode enum, found {len(matches)}")
    return matches[0]


def opcode_entries(
    files: list[ProtoFile],
) -> list[tuple[str, int, str]]:
    messages: dict[str, str] = {}
    duplicate_names: set[str] = set()
    for proto_file in files:
        for message in walk_messages(proto_file.messages):
            if message.map_entry:
                continue
            if message.name in messages:
                duplicate_names.add(message.name)
            messages[message.name] = message.full_name.lstrip(".")
    entries: list[tuple[str, int, str]] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    for enum_name, number in find_opcode_enum(files).values:
        if enum_name in {"kOpcode_None", "None", "kOpcode_Begin"} or number == 0:
            continue
        _, separator, name = enum_name.partition("_")
        if not separator or not name:
            raise ValueError(
                f"opcode enum value must contain a non-empty prefix: {enum_name}"
            )
        if name in seen_names:
            raise ValueError(f"duplicate rendered opcode name: {name}")
        seen_names.add(name)
        if name in duplicate_names:
            raise ValueError(f"opcode message name is ambiguous: {name}")
        full_name = messages.get(name)
        if full_name is None:
            raise ValueError(f"opcode {enum_name} has no matching message {name}")
        if number in seen_ids:
            raise ValueError(f"duplicate opcode value {number}")
        seen_ids.add(number)
        entries.append((name, number, full_name))
    return entries


def render_name(entries: list[tuple[str, int, str]]) -> str:
    lines = [
        "-- Code generated by proto/gen_lua.py. DO NOT EDIT.",
        "",
        "local M = {",
    ]
    lines.extend(f'    [{number}] = "{full_name}",' for _, number, full_name in entries)
    lines.extend(["}", "", "return M", ""])
    return "\n".join(lines)


def render_opcode(entries: list[tuple[str, int, str]]) -> str:
    lines = [
        "-- Code generated by proto/gen_lua.py. DO NOT EDIT.",
        "",
        "---@enum pb.opcode",
        "local M = {",
    ]
    lines.extend(f"    {name} = {number}," for name, number, _ in entries)
    lines.extend(["}", "", "return M", ""])
    return "\n".join(lines)


def write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def generate(
    schema_path: Path = SCHEMA_PATH, output_dir: Path = OUTPUT_DIR
) -> None:
    files = load_schema(schema_path)
    entries = opcode_entries(files)
    write_atomically(output_dir / "msg.lua", render_cats(files))
    write_atomically(output_dir / "error.lua", render_error(find_error_enum(files)))
    write_atomically(output_dir / "name.lua", render_name(entries))
    write_atomically(output_dir / "opcode.lua", render_opcode(entries))


if __name__ == "__main__":
    generate()
