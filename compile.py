#!/usr/bin/env python3
"""将 Proto 源目录编译为 protobuf descriptor set。"""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path

from grpc_tools import protoc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将目录中的 Proto 文件编译为一个 protobuf descriptor set。"
    )
    parser.add_argument(
        "--proto-dir",
        required=True,
        type=Path,
        help="Proto 源文件根目录。",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="descriptor set 输出目录。",
    )
    parser.add_argument(
        "--output-name",
        required=True,
        help="descriptor set 输出文件名，例如 msg.pb。",
    )
    return parser.parse_args()


def validate_output_name(output_name: str) -> None:
    if not output_name or Path(output_name).name != output_name:
        raise ValueError("output name must be a file name without a directory")


def compile_proto(proto_dir: Path, output_dir: Path, output_name: str) -> Path:
    validate_output_name(output_name)
    proto_dir = proto_dir.resolve()
    output_dir = output_dir.resolve()

    if not proto_dir.is_dir():
        raise FileNotFoundError(f"proto directory does not exist: {proto_dir}")

    proto_files = sorted(
        path.relative_to(proto_dir) for path in proto_dir.rglob("*.proto")
    )
    if not proto_files:
        raise ValueError(f"proto directory contains no .proto files: {proto_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name
    mode = stat.S_IMODE(output_path.stat().st_mode) if output_path.exists() else 0o644
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{output_name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        command = [
            "grpc_tools.protoc",
            f"--proto_path={proto_dir}",
            f"--descriptor_set_out={temp_path}",
            "--include_imports",
            *(str(proto_dir / path) for path in proto_files),
        ]
        result = protoc.main(command)
        if result != 0:
            raise RuntimeError(f"grpc_tools.protoc failed with exit code {result}")
        os.chmod(temp_path, mode)
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

    return output_path


if __name__ == "__main__":
    arguments = parse_args()
    compile_proto(
        arguments.proto_dir,
        arguments.output_dir,
        arguments.output_name,
    )
