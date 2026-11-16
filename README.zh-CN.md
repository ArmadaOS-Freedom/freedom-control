# Freedom Control

[English](README.md) | 简体中文

Freedom Control 是 [Armada Control](https://github.com/armada-os/armada/tree/main/decky/armada-control) 面向平板设备的延续项目，专为 [ArmadaOS Freedom](https://github.com/mydanyi/armadaos-freedom) 构建。它是一个 Decky Loader 插件，用于在掌机和平板设备上管理单游戏与全局的兼容性和性能设置。

本仓库是该项目的独立主页：<https://github.com/mydanyi/freedom-control>。

Freedom Control 沿用 Armada Control 的插件架构与宿主服务接口，保留原有插件目录和 Python 包名以维持兼容性，并新增了面向 SM8850 平台的初始 SoC 级配置层。

## 功能

当前快照提供：

- **游戏模式显示方向**：在“系统”中覆盖显示方向，支持原始方向、向左或向右旋转 90°、旋转 180°，触控跟随画面方向。入口位于“Steam 主界面节能模式”上方；保存后点击“应用方向”并确认，重启整个游戏模式后生效。关闭覆盖后使用设备默认方向。此功能需要配套的 ArmadaOS Freedom 系统组件。
- **单游戏与全局控制**：兼容性和性能设置既可以全局生效，也可以按游戏单独覆盖。
- **分层配置**：支持共享、SoC 专用和用户配置三个层次。同一配置项可以面向所有设备定义一次，再按 SoC 细化，并由用户覆盖。
- **初始支持 Lenovo Legion Tab Y700 Gen 5（TB323FU，SM8850）**：包含初始设备/SoC 配置，提供 CPU 频率上限和核心元数据。
- **CPU governor 显示名称**：界面以可读名称显示所选 governor，而不只是内核标识符。
- **触屏手柄单页**：只有一页，在内置触摸屏上绘制屏幕虚拟手柄，支持预设、布局编辑、显示隐藏与震动。
- **TF 卡游戏库标签页**：把已分配 TF 卡的 Linux 保留分区转换为 Steam 游戏库，同时保留该卡的 Android 存储。

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

触屏页面还需要系统镜像提供配套组件：Gamescope 触屏覆盖层与手势支持、InputPlumber 触屏 D-Bus 与虚拟设备配置，以及带 `evdev`、GI、Cairo、GTK 4 和 X11 的系统 Python。插件不包含这些系统程序。

仅安装插件不会安装 `system_files` 下的配套组件或上述系统程序；这些组件属于操作系统镜像的一部分，需要在构建系统时集成。本仓库不提供自动化的特权部署步骤。

## 触屏手柄

触屏页面在内置触摸屏上绘制屏幕虚拟手柄，只有一页，提供预设、布局编辑、显示隐藏与震动。

- 默认关闭。开启触屏开关会启动插件自身的守护进程；关闭该开关会停止该守护进程并释放触摸设备。
- 边缘滑动沿用 Controller 已有的滑动手势开关：总开关与游戏内开关。
- 触屏设置保存在独立的 `touch-controller.json` 设置文件中，与其它 Controller 设置分开。

## TF 卡游戏库

**TF** 图标标签页会把 TF 卡的 Linux 保留分区转换为 Armada/Steam 游戏库，同时保留该卡的 Android 存储。标签页位于可选的散热/RGB 标签页与高级标签页之间。

1. **先在 Android 上分配。** 使用 [智能TF卡分配器](https://github.com/mydanyi/smart-tf-allocator)（[APK 下载](https://github.com/mydanyi/smart-tf-allocator/releases/latest)）完成分配。无需安装原 Armada Installer 插件。
2. **插入 TF 卡并打开 TF 标签页。** 重新扫描、选择该卡，然后检查。
3. **确认。** 对话框会重述 TF 卡、Linux 保留分区与两侧容量；取消不会产生任何更改。

- 仅格式化 Linux 保留分区（p1）为 ext4 并挂载；Android 元数据与私有分区（p2/p3）及其数据都会保留。
- 已包含 Armada 游戏库的卡会直接挂载并重新接入 Steam，不会格式化；已有 ext4 卡不会被自动重新格式化。
- 页面显示真实阶段与不确定进度条、详情开关、两种成功结果（已接入 Steam / 已挂载等待 Steam）、绝不格式化的重试接入操作，以及失败重试和显式返回卡列表操作。
- 仅在 TF 标签页打开时进行卡探测与状态轮询。切换标签页或关闭面板会卸载页面并停止轮询；systemd 转换任务会继续运行，重新打开面板会恢复运行中/成功/失败状态。

设备端运行要求：`systemd`；`util-linux`（`sfdisk`、`lsblk`、`findmnt`）；`gdisk`/`sgdisk`；`e2fsprogs`（`mkfs.ext4`）；`udev`；以及 `/usr/lib/hwsupport/steamos-automount.sh`。

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

TF 卡游戏库页面移植自 [Armada Installer](https://github.com/mydanyi/armada-installer)（`1ac40b959085e6b55c2aef1467080b07560a2431`），保留原作者与来源。其系统安装页面、语言选择与作者面板未迁移；移植后的 TF 代码经原作者同意，作为本项目的一部分依据 GNU 通用公共许可证第 2 版分发。

触摸控制器移植自 [decky-freedom-touch-controller](https://github.com/mydanyi/decky-freedom-touch-controller)（`9357d975711d17d3ee70fc97090b01ed47365672`），原作者为单翼（danyi），保留原作者、来源与第三方权利。经作者许可，移植后的触屏代码继续依据 GNU 通用公共许可证第 2 版分发。第三方声明见 [decky/armada-control/TOUCH_THIRD_PARTY_NOTICES.md](decky/armada-control/TOUCH_THIRD_PARTY_NOTICES.md)。

本项目依据 GNU 通用公共许可证第 2 版分发。参见 [LICENSE](LICENSE)。
