# top-down-proto

本目录保存项目的 Proto 源文件及通用生成脚本。脚本不依赖主工程目录结构，调用方必须为 PB 和 Lua 产物显式传入路径。

## 生成 Opcode

`gen_opcode.py` 无参数运行。它扫描本目录中的顶层 `Request`、`Response`、`Notice`、`Notify` 和 `Broadcast` 消息，保留 `common/opcode.proto` 中已有的 Opcode，并为新消息顺序追加编号。

```shell
python gen_opcode.py
```

## 编译 descriptor set

`compile.py` 需要 Proto 根目录、输出目录和输出文件名。运行环境必须安装 `grpcio-tools`，脚本直接调用 `grpc_tools.protoc` 的 Python API，不依赖系统 `PATH` 中的独立 `protoc` 可执行文件。

```shell
python compile.py --proto-dir . --output-dir ../../../assets/pb --output-name msg.pb
```

编译使用 `--include_imports`，并通过同目录临时文件原子替换目标 PB，失败时不会覆盖原产物。主工程约定使用 Conda `main` 环境的 Python 运行构建入口。

## 生成 Lua

`gen_lua.py` 从 descriptor set 生成 `msg.lua`、`error.lua`、`name.lua` 和 `opcode.lua`。输入 PB 路径与 Lua 输出目录均为必填参数。

```shell
python gen_lua.py --schema ../../../assets/pb/msg.pb --output-dir ../../../src/modules/pb
```

主工程统一通过 `tools/pb/build.py` 按“生成 Opcode、编译 PB、生成 Lua”的顺序调用这些脚本：

```shell
D:\app\anaconda3\envs\main\python.exe tools\pb\build.py
```
