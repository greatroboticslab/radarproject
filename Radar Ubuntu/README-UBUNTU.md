# Running the mmWave Soil Moisture Sensor on Ubuntu

This is a copy of the Windows project (`mmwave-soil-moisture`), set up to run on Linux.
The code itself is cross-platform (pathlib for paths, `pyserial` for the UART link,
Tkinter for the GUI, `serial.tools.list_ports` for port discovery) — nothing had to be
rewritten. This file just covers the Linux-specific setup steps that don't apply on Windows.

Read `NOTES.md` for the full project context (physics, calibration workflow, chirp config,
known issues). This file only covers what's different on Ubuntu.

## First-time setup

```bash
chmod +x setup.sh run.sh
./setup.sh
```

This is a one-shot installer: it installs `python3-tk` (Tkinter isn't bundled with the
system Python on Ubuntu the way it is on the Windows python.org installer), creates a
`.venv`, installs `requirements.txt`, adds your user to the `dialout` group (serial port
access), and then **launches the GUI automatically** — nothing else to run by hand. It's
also safe to re-run any time (e.g. after transferring an updated copy of the folder); it
skips steps that are already done.

You'll hit a `sudo` password prompt partway through (for the apt install and the group
change). If this is a genuinely fresh account that was *just* added to `dialout` by an
earlier run, you may still need to log out/in once before serial ports work in a plain
terminal — `setup.sh` works around this for its own launch via `sg dialout`, but a
separate terminal opened before that point won't have it yet.

## Running (after the first time)

```bash
./run.sh
```

This just activates the venv and starts the GUI — use it for every run after the initial
`./setup.sh`. Equivalent to:

```bash
source .venv/bin/activate
python gui.py
```

or headless/CLI mode:

```bash
python -m host.main --cli-port /dev/ttyACM0 --data-port /dev/ttyACM1 --mode uart --standoff 0.20
```

## Finding the serial ports

On Windows this board showed up as COM3/COM4 ("XDS110 Class Application/User UART" and
"XDS110 Class Auxiliary Data Port"). On Linux it will enumerate as `/dev/ttyACM0` and
`/dev/ttyACM1` (order not guaranteed to match the Windows CLI/data assignment). Find them with:

```bash
python -m serial.tools.list_ports -v
```

or

```bash
dmesg | grep -i tty
```

The GUI's port dropdowns auto-populate from `serial.tools.list_ports`, so you can just pick
the right ones there — but you'll need to identify which is CLI vs. data the first time
(plug in, run `dmesg | tail`, unplug, compare) since the descriptive names may differ from
Windows.

### Optional: stable port names via udev

If the `/dev/ttyACM0` / `/dev/ttyACM1` assignment isn't consistent across reboots/replugs
and that bothers you, you can pin them with a udev rule. Get the exact `idVendor`/`idProduct`
for your board first — don't guess:

```bash
udevadm info -a -n /dev/ttyACM0 | grep -E 'idVendor|idProduct|bInterfaceNumber' | head
```

Then create `/etc/udev/rules.d/99-xds110.rules` using those values, reload with
`sudo udevadm control --reload-rules && sudo udevadm trigger`, and update the GUI/CLI
invocation to use the fixed symlink names instead of `/dev/ttyACM*`.

## Things that don't carry over from Windows

- **`SoilMoistureSensor.exe`** was not copied — it's a Windows PyInstaller build. Run
  `python gui.py` instead. (If you want a standalone Linux binary later, PyInstaller
  supports Linux targets too — same `--onefile` approach as the Windows build command
  in `NOTES.md`, run on the Ubuntu machine itself.)
- **DCA1000 (Ethernet/UDP) path**: if you use this instead of UART, the static-IP NIC
  setup (`192.168.33.30/24`) is done through Ubuntu's Netplan or NetworkManager, not the
  Windows NIC settings dialog — the DCA1000 side (`192.168.33.180`) is unchanged.
- **`__pycache__` / `.pytest_cache`**: not copied, will regenerate automatically.

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

No hardware required — synthetic data.
