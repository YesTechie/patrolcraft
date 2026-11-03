# PatrolCraft

Edit your UniFi Protect PTZ patrols from one page. Set the dwell time and the move speed for each preset in a patrol, turn camera auto-tracking on or off, and start or stop a patrol.

PatrolCraft runs on a Mac, a Linux computer or a Raspberry Pi on your network and talks to your UniFi console.

**Community:** questions, ideas and release updates live in [r/YesTechie_Labs](https://www.reddit.com/r/YesTechie_Labs/).

## Features

- **Per-preset dwell and speed.** Set how long the camera stays at each preset and how fast it moves there.
- **Auto-tracking.** Turn Person, Vehicle and Zoom-while-track on or off for each PTZ camera.
- **Start and stop patrols** from the camera's patrol list.
- **Preset snapshots.** See a picture of each preset, and refresh them from the camera when you need to.
- **Demo mode.** Try the editor with sample cameras before you connect a console.
- **Private by default.** The installer makes a unique login. On a Mac, only that Mac can open PatrolCraft. On Linux or a Raspberry Pi, other computers on your network can open it after signing in.
- **One-command install, upgrade and uninstall.** Upgrades keep your settings.

PatrolCraft edits the patrols you already made in UniFi Protect. Create, reorder and rename patrols in Protect.

## Requirements

- A Mac with macOS 12 or newer and Apple's Command Line Tools (Python 3.9 or newer)
- Or a Linux computer or Raspberry Pi with systemd, Python 3.9 or newer and OpenSSH 8.4 or newer (Raspberry Pi OS Bookworm, Ubuntu 22.04, Debian 11 or newer). On Raspberry Pi OS, Debian or Ubuntu, run `sudo apt install python3-venv` first.
- UniFi Protect with one or more PTZ cameras
- A local UniFi OS account on the console

## Install

Run this in a terminal on the computer that will run PatrolCraft:

    curl -fsSL https://labs.yestechie.com/install/patrolcraft.sh | bash

The installer shows your PatrolCraft username and password. On a Mac it opens http://127.0.0.1:8080/. On Linux or a Raspberry Pi it shows the network address to open from another computer. Sign in, open **Config** and connect your UniFi console.

To uninstall:

    curl -fsSL https://labs.yestechie.com/install/patrolcraft.sh | bash -s -- --uninstall

## License

Free for personal use under the [PolyForm Strict License](./LICENSE.md). For business use, or for an integration built for your site, contact us at [yestechie.com/contact](https://yestechie.com/contact).

## More

Details and a demo are on the [PatrolCraft page](https://labs.yestechie.com/integrations/patrolcraft.html).
