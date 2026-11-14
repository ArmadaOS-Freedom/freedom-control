# Freedom Control

[English](README.md) | 简体中文

Freedom Control 是 [Armada Control](https://github.com/armada-os/armada/tree/main/decky/armada-control) 面向平板设备的延续项目，专为 [ArmadaOS Freedom](https://github.com/mydanyi/armadaos-freedom) 构建。它是一个 Decky Loader 插件，用于在掌机和平板设备上管理单游戏与全局的兼容性和性能设置。

本仓库是该项目的独立主页：<https://github.com/mydanyi/freedom-control>。

Freedom Control 沿用 Armada Control 的插件架构与宿主服务接口，保留原有插件目录和 Python 包名以维持兼容性，并新增了面向 SM8850 平台的初始 SoC 级配置层。

## 功能

当前快照提供：

- **单游戏与全局控制**：兼容性和性能设置既可以全局生效，也可以按游戏单独覆盖。
- **分层配置**：支持共享、SoC 专用和用户配置三个层次。同一配置项可以面向所有设备定义一次，再按 SoC 细化，并由用户覆盖。
- **初始支持 Lenovo Legion Tab Y700 Gen 5（TB323FU，SM8850）**：包含初始设备/SoC 配置，提供 CPU 频率上限和核心元数据。
- **CPU governor 显示名称**：界面以可读名称显示所选 governor，而不只是内核标识符。

SM8850 目前处于基础集成阶段，性能调优仍在进行。

## 仓库结构

```
decky/armada-control/   Decky 插件：React/TypeScript 前端与 Python 后端
system_files/           精选的配套宿主服务与配置源码
                        （集成子集，保留上游安装布局）
tests/                  针对 SM8850 电源配置的回归测试
```

- `decky/armada-control` 即插件本体，包含 `main.py`、`py_modules/armada_control` 后端、`src` 前端和 `plugin.json`。
- `system_files` 对应上游文件系统布局（`usr/lib/armada`、`usr/libexec/armada`、`usr/share/armada`），提供插件所调用的配套宿主服务与配置。它是用于操作系统集成的源码子集，不是自动安装程序，也不是完整发行版。
- `tests/sm8850-power-test.py` 基于本仓库的配置源码，覆盖 SM8850 电源配置的分层与 CPU 频率上限。

`decky/armada-control` 路径与 Python 包名 `armada_control` 沿用上游名称，以兼容既有的 Armada 集成。

## `system_files` 中的电源文件

`system_files` 包含两类电源文件。

由本仓库负责、并随本仓库安装进操作系统镜像的正式源码：

- `system_files/usr/libexec/armada/armada-powerd`
- `system_files/usr/lib/armada/devices/lenovo-tb323fu.conf`
- `system_files/usr/share/armada/power-profiles-SM8850.conf`

仅供独立测试使用、不会随操作系统构建安装的测试固定文件：

- `system_files/usr/lib/armada/armada_perf.py`
- `system_files/usr/share/armada/power-profiles.conf`
- `system_files/usr/share/armada/game-tweaks.json`

上述三个固定文件路径的权威副本由系统仓库所有。本仓库中的副本只为让独立测试可运行，请仅将其视为测试输入，不要当作共享运行时文件的第二份分发来源。

## 运行要求

Freedom Control 是面向既有 ArmadaOS 或 ArmadaOS Freedom 安装的插件，需要：

- ArmadaOS 或 ArmadaOS Freedom，包括其宿主服务与电源/设备配置；
- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)。

仅安装插件不会安装 `system_files` 下的配套组件；这些组件属于操作系统镜像的一部分，需要在构建系统时集成。本仓库不提供自动化的特权部署步骤。

## 开发

开发环境使用 Node.js 22.23.3 或 Node.js 24。在仓库根目录执行：

```sh
cd decky/armada-control
npm ci
npm test
npm run build
```

构建会把前端产物输出到插件目录内的 `dist/index.js`。可打包的插件由 `main.py`、`py_modules`、`plugin.json` 和 `dist/index.js` 组成。

SM8850 回归测试仅使用 Python 3 标准库。在仓库根目录执行：

```sh
python3 tests/sm8850-power-test.py
```

## Steam 预设标识符

原生 Steam 性能预设标识符仍为 `Eco`、`Balanced` 和 `Performance`，以保持与 Steam 及宿主服务的兼容。本快照不包含针对这些标识符的纯显示层翻译。

## 来源与许可

Freedom Control 源自 Armada 项目的 Armada Control。原始插件、宿主服务与配置的版权归 Armada 贡献者所有。Freedom 相关修改（包括 SM8850 配置分层与 CPU 频率上限，以及重命名和 governor 显示名称）来自 [mydanyi/armadaos-freedom](https://github.com/mydanyi/armadaos-freedom)。

源码快照基于 [提交 `5c560b965d040c07cc76e4a3dd78afaba3fcc796`](https://github.com/mydanyi/armadaos-freedom/tree/5c560b965d040c07cc76e4a3dd78afaba3fcc796/decky/armada-control)，并包含上述既有的 Freedom Control 修改。

本项目依据 GNU 通用公共许可证第 2 版分发。参见 [LICENSE](LICENSE)。
