# FleetCommandAV
Built off the backs of open source broadcast ecosystems, this project attemps to glue it all together with code.

## Quick Start

Run this command to install FleetCommandAV:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/RogueNAND/fleetcommandav/main/install.sh)
```

Or clone and run locally:

```bash
git clone https://github.com/RogueNAND/fleetcommandav.git
cd fleetcommandav
./install.sh
```

The dashboard will be accessible at `http://<your-ip>/`

## Adding Python Libraries

To add third-party Python libraries for use in your automation modules:

```bash
cd /srv/fleetcommandav/framework/libraries
git clone https://github.com/example/some-library.git
```

Libraries are automatically installed on Python container restart. Any package with `pyproject.toml`, `setup.py`, or `setup.cfg` will be detected and installed via pip editable install.

## Updates

To update an existing installation:

```bash
cd /srv/fleetcommandav
git pull
./install.sh
```
