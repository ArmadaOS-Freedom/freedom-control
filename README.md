# Freedom Control

English | [简体中文](README.zh-CN.md)

Freedom Control is a tablet-focused continuation of [Armada Control](https://github.com/armada-os/armada/tree/main/decky/armada-control), built for [ArmadaOS Freedom](https://github.com/mydanyi/armadaos-freedom). It is a Decky Loader plugin for controlling per-game and system-wide compatibility and performance settings on handheld and tablet devices.

This repository is the independent home of the project: <https://github.com/mydanyi/freedom-control>.

Freedom Control reuses the Armada Control plugin architecture and host-service interfaces, retains the original plugin directory and Python package names for compatibility, and adds an initial SoC-level profile layer for the SM8850 platform.

## Features

The current snapshot provides:

- **Per-game and global controls** for compatibility and performance settings, so a title can inherit the global configuration or override it individually.
- **Layered configuration** for shared, SoC-specific, and user profiles. Profile values can be defined once for all devices, refined per SoC, and overridden by the user.
- **Initial Lenovo Legion Tab Y700 Gen 5 (TB323FU, SM8850) support**, including an initial device/SoC profile with CPU frequency caps and core metadata.
- **CPU governor labels** in the interface, so the selected governor is shown with a readable name rather than only its kernel identifier.

SM8850 support is at the initial integration stage; performance tuning is ongoing.

## Repository layout

```
decky/armada-control/   Decky plugin: React/TypeScript frontend and Python backend
system_files/           Selected companion host-service and configuration sources
                        (an integration subset, retained in the upstream install layout)
tests/                  Focused SM8850 power-profile regression tests
```

- `decky/armada-control` is the plugin itself. It contains `main.py`, the `py_modules/armada_control` backend, the `src` frontend, and `plugin.json`.
- `system_files` mirrors the upstream filesystem layout (`usr/lib/armada`, `usr/libexec/armada`, `usr/share/armada`). It provides the companion host services and configuration that the plugin talks to. It is a source subset for OS integration, not an automatic installer and not a complete distribution.
- `tests/sm8850-power-test.py` covers the SM8850 power-profile layering and caps against the configuration sources in this repository.

The paths `decky/armada-control` and the Python package `armada_control` keep those upstream names for compatibility with existing Armada integration.

## Power files in `system_files`

`system_files` holds two kinds of power files.

Canonical sources owned here and installed into the OS image from this repository:

- `system_files/usr/libexec/armada/armada-powerd`
- `system_files/usr/lib/armada/devices/lenovo-tb323fu.conf`
- `system_files/usr/share/armada/power-profiles-SM8850.conf`

Standalone-test fixtures used by this repository's test suites and never installed by the OS build:

- `system_files/usr/lib/armada/armada_perf.py`
- `system_files/usr/share/armada/power-profiles.conf`
- `system_files/usr/share/armada/game-tweaks.json`

The system repository owns the authoritative copies of the three fixture paths. The copies here exist so the standalone tests can run; treat them as test inputs only and do not treat them as a second distribution of shared runtime files.

## Runtime requirements

Freedom Control is a plugin for an existing ArmadaOS or ArmadaOS Freedom installation. It requires:

- ArmadaOS or ArmadaOS Freedom, including its host services and power/device configuration, and
- [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader).

Installing only the plugin does not install the companion components under `system_files`; those are part of the operating system image and must be integrated when the OS is built. This repository does not provide an automatic privileged deployment step.

## Development

Use Node.js 22.23.3 or Node.js 24 for development. From the repository root:

```sh
cd decky/armada-control
npm ci
npm test
npm run build
```

The build writes the frontend bundle to `dist/index.js` inside the plugin directory. A packaged plugin consists of `main.py`, `py_modules`, `plugin.json`, and `dist/index.js`.

The SM8850 regression suite uses only the Python 3 standard library. From the repository root:

```sh
python3 tests/sm8850-power-test.py
```

## Steam preset identifiers

Native Steam performance preset identifiers remain `Eco`, `Balanced`, and `Performance`. They are kept as-is for compatibility with Steam and with the host services. A display-only translation layer for these identifiers is not included in this snapshot.

## Source and license

Freedom Control is derived from Armada Control by the Armada project. Credit for the original plugin, host services, and configuration belongs to the Armada contributors. Freedom-specific modifications, including the SM8850 configuration layering and CPU frequency caps plus the rename and governor labels, come from [mydanyi/armadaos-freedom](https://github.com/mydanyi/armadaos-freedom).

The source snapshot is based on [commit `5c560b965d040c07cc76e4a3dd78afaba3fcc796`](https://github.com/mydanyi/armadaos-freedom/tree/5c560b965d040c07cc76e4a3dd78afaba3fcc796/decky/armada-control), together with the existing Freedom Control modifications noted above.

This project is distributed under the GNU General Public License v2.0. See [LICENSE](LICENSE).
