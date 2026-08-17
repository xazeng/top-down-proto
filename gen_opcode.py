#!/usr/bin/env python3
"""Generate stable client protocol opcodes."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_DIR
OPCODE_PATH = SOURCE_ROOT / "common" / "opcode.proto"

OPCODE_RE = re.compile(
    r"^\s*kOpcode_([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)\s*;",
    re.MULTILINE,
)
MESSAGE_RE = re.compile(r"\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
PROTOCOL_NAME_RE = re.compile(r".*(?:Request|Response|Notice|Notify|Broadcast)$")


def strip_comments_and_strings(source: str) -> str:
    """Replace comments and quoted strings with whitespace, preserving newlines."""
    result: list[str] = []
    index = 0
    size = len(source)

    while index < size:
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            if end == -1:
                result.extend(" " * (size - index))
                break
            result.extend(" " * (end - index))
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise ValueError("unterminated block comment")
            segment = source[index : end + 2]
            result.extend("\n" if char == "\n" else " " for char in segment)
            index = end + 2
        elif source[index] in {'"', "'"}:
            quote = source[index]
            result.append(" ")
            index += 1
            while index < size:
                char = source[index]
                if char == "\\":
                    result.append(" ")
                    index += 1
                    if index < size:
                        result.append("\n" if source[index] == "\n" else " ")
                        index += 1
                elif char == quote:
                    result.append(" ")
                    index += 1
                    break
                else:
                    result.append("\n" if char == "\n" else " ")
                    index += 1
            else:
                raise ValueError("unterminated quoted string")
        else:
            result.append(source[index])
            index += 1

    return "".join(result)


def find_top_level_protocol_messages(path: Path) -> list[str]:
    """Return Request/Response messages declared at file scope, in source order."""
    source = strip_comments_and_strings(path.read_text(encoding="utf-8"))
    messages: list[str] = []
    depth = 0
    cursor = 0

    for match in MESSAGE_RE.finditer(source):
        for char in source[cursor : match.start()]:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth < 0:
                    raise ValueError(f"unbalanced braces in {path}")

        if depth == 0 and PROTOCOL_NAME_RE.fullmatch(match.group(1)):
            messages.append(match.group(1))

        depth += 1
        cursor = match.end()

    for char in source[cursor:]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced braces in {path}")
    if depth != 0:
        raise ValueError(f"unbalanced braces in {path}")

    return messages


def load_opcode_file(path: Path) -> tuple[list[tuple[str, int]], int]:
    """Load historical opcode mappings and their maximum id."""
    source = path.read_text(encoding="utf-8")
    entries = [
        (name, int(value))
        for name, value in OPCODE_RE.findall(source)
        if name != "None"
    ]
    names = [name for name, _ in entries]
    ids = [opcode for _, opcode in entries]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate opcode name in {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate opcode id in {path}")

    return entries, max(ids, default=0)


def scan_messages(source_root: Path, opcode_path: Path) -> list[str]:
    """Scan client proto files in stable path and declaration order."""
    messages: list[str] = []
    owners: dict[str, Path] = {}

    for path in sorted(source_root.rglob("*.proto")):
        if path.resolve() == opcode_path.resolve():
            continue
        for name in find_top_level_protocol_messages(path):
            previous = owners.get(name)
            if previous is not None:
                raise ValueError(
                    f"duplicate message {name}: "
                    f"{previous.relative_to(source_root)} and "
                    f"{path.relative_to(source_root)}"
                )
            owners[name] = path
            messages.append(name)

    return messages


def render_opcode(entries: list[tuple[str, int]]) -> str:
    """Render the complete generated opcode proto."""
    opcode_lines = "\n".join(
        f"  kOpcode_{name} = {opcode};" for name, opcode in entries
    )
    return f"""// Code generated by gen_opcode.py. DO NOT EDIT.

syntax = "proto3";

package msg;

enum Opcode {{
  kOpcode_None = 0;
{opcode_lines}
}}
"""


def write_atomically(path: Path, content: str) -> None:
    """Write a temporary sibling file, then atomically replace the target."""
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
        os.chmod(temp_path, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def generate(source_root: Path = SOURCE_ROOT, opcode_path: Path = OPCODE_PATH) -> None:
    """Append new message opcodes and replace opcode.proto via a temp file."""
    entries, max_id = load_opcode_file(opcode_path)
    existing_names = {name for name, _ in entries}

    for name in scan_messages(source_root, opcode_path):
        if name in existing_names:
            continue
        max_id += 1
        entries.append((name, max_id))
        existing_names.add(name)

    write_atomically(opcode_path, render_opcode(entries))


if __name__ == "__main__":
    generate()
