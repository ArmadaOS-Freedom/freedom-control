# Freedom Control

English | [简体中文](README.zh-CN.md)

Freedom Control is a tablet-focused continuation of [Armada Control](https://github.com/armada-os/armada/tree/main/decky/armada-control), built for [ArmadaOS Freedom](https://github.com/mydanyi/armadaos-freedom). It is a Decky Loader plugin for controlling per-game and system-wide compatibility and performance settings on handheld and tablet devices.

This repository is the independent home of the project: <https://github.com/mydanyi/freedom-control>.

Freedom Control reuses the Armada Control plugin architecture and host-service interfaces, retains the original plugin directory and Python package names for compatibility, and adds an initial SoC-level profile layer for the SM8850 platform.

## Features

The current snapshot provides:

- **Game Mode display orientation**: override the native direction, rotate left or right by 90 degrees, or rotate by 180 degrees, with touch coordinates following the display. The control appears in System above Steam UI power saving. Save the choice, then select Apply Orientation and confirm to restart the entire Game Mode session. Turning the override off follows the device default. Matching ArmadaOS Freedom system components are required.
- **Per-game and global controls** for compatibility and performance settings, so a title can inherit the global configuration or override it individually.
- **Layered configuration** for shared, SoC-specific, and user profiles. Profile values can be defined once for all devices, refined per SoC, and overridden by the user.
- **Initial Lenovo Legion Tab Y700 Gen 5 (TB323FU, SM8850) support**, including an initial device/SoC profile with CPU frequency caps and core metadata.
- **CPU governor labels** in the interface, so the selected governor is shown with a readable name rather than only its kernel identifier.
- **Touch gamepad page**, one page on the built-in touchscreen with Xbox/PS/Switch presets, per-preset 4/8-way D-pad, one global white/black button color, deleting custom presets, deleting and restoring template components, moving/resizing the four face buttons as a group, controller navigation, show/hide, and haptics. The fullscreen layout editor can dock its tools on either side; tablet presets are symmetric and sticks stay circular when resized. Optional full-screen right-stick control is off by default: it hides the fixed right stick, converts swipes in empty areas into right-stick input, and returns to center when motion stops or the finger lifts.
- **TF card game library tab**, which turns the Linux reserve partition of a prepared TF card into a Steam game library without touching the card's Android storage.

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

The Touch page additionally needs matching components from the system image: Gamescope touch overlay and gesture support, InputPlumber touch D-Bus and virtual-device configuration, and a system Python with `evdev`, GI, Cairo, GTK 4 and X11. The plugin does not include these system programs.

Installing only the plugin does not install the companion components under `system_files` or the system programs above; those are part of the operating system image and must be integrated when the OS is built. This repository does not provide an automatic privileged deployment step.

## Touch gamepad

The Touch page draws an on-screen virtual gamepad on the built-in touchscreen. It is one page, with presets, layout editing, show/hide, and haptics.

- Off by default. Turning the touch switch on starts the plugin's own daemon; turning it off stops that daemon and releases the touch device.
- Edge swipes use the existing Controller swipe-gesture switches: the overall switch and the in-game switch.
- Touch settings are kept in a separate `touch-controller.json` settings file, apart from the other Controller settings.

## TF card game library

The **TF** icon tab turns the Linux reserve partition of a TF card into an Armada/Steam game library while keeping the card's Android storage. It sits between the optional fan/RGB tabs and the Advanced tab.

1. **On Android first.** Allocate the card with [Smart TF Card Allocator (智能TF卡分配器)](https://github.com/mydanyi/smart-tf-allocator) ([APK download](https://github.com/mydanyi/smart-tf-allocator/releases/latest)). The original Armada Installer plugin is not required.
2. **Insert the card and open the TF tab.** Rescan, select the card, then review.
3. **Confirm.** The dialog restates the card, the Linux reserve partition and both sizes; cancel changes nothing.

- Only the Linux reserve partition (p1) is formatted to ext4 and mounted. The Android metadata/private partitions (p2/p3) and their data are kept.
- A card that already holds an Armada library is mounted and reconnected to Steam without formatting; an existing ext4 card is never reformatted automatically.
- The page shows the real stage with an indeterminate progress bar, a details toggle, both success outcomes (connected to Steam vs. mounted and waiting for Steam), the retry-connect action that never formats, and failure/retry plus an explicit back-to-cards action.
- Card probing and status polling run only while the TF tab is open. Switching tabs or closing the panel unmounts the page and stops polling; the systemd conversion job keeps running, and reopening the panel resumes the running/succeeded/failed state.

Runtime requirements on the device: `systemd`; `util-linux` (`sfdisk`, `lsblk`, `findmnt`); `gdisk`/`sgdisk`; `e2fsprogs` (`mkfs.ext4`); `udev`; and `/usr/lib/hwsupport/steamos-automount.sh`.

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

The TF card game library page is ported from [Armada Installer](https://github.com/mydanyi/armada-installer) (`1ac40b959085e6b55c2aef1467080b07560a2431`); the original author and source are retained. Its system-install page, language selector and author panel were not migrated, and the ported TF code is distributed as part of this project under the GNU General Public License v2.0 with the original author's approval.

The touch controller is ported from [decky-freedom-touch-controller](https://github.com/mydanyi/decky-freedom-touch-controller) (`9357d975711d17d3ee70fc97090b01ed47365672`) by 单翼 (danyi), and also integrates the author's retained 0.1.6 revisions; the original author, source and third-party rights are retained. The ported touch code stays under the GNU General Public License v2.0 with the author's permission. Third-party attributions are in [decky/armada-control/TOUCH_THIRD_PARTY_NOTICES.md](decky/armada-control/TOUCH_THIRD_PARTY_NOTICES.md).

This project is distributed under the GNU General Public License v2.0. See [LICENSE](LICENSE).
