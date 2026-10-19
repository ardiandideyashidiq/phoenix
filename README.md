# phoenix

Transsion firmware client for [phoenixservicetool.com](https://phoenixservicetool.com).

`firmware_list` is free; `firmware_link` (mapping a version to a share) costs one credit.

## Install

```bash
cd phoenix
uv sync
```

## Usage

```bash
uv run phoenix login -e you@example.com        # prompts for the password
uv run phoenix firmware X6871                  # list versions (free)
uv run phoenix download X6871 X6871-H895DE-U-EU-250717V3825 -o ./firmware
uv run phoenix models
uv run phoenix status
uv run phoenix logout
```

Global options go before the command:

```bash
uv run phoenix --no-verify-ssl --session-file ./session.json firmware X6871
uv run phoenix -v firmware X6871
```

Credentials can also come from `PHOENIX_EMAIL` / `PHOENIX_PASSWORD`. The session token is
stored in `~/.phoenix_session.json` (mode 0600) and reused until the server rejects it.

### Legacy flags

`phoenix_api_client.py` at the repository root still accepts the old interface:

```bash
python3 phoenix_api_client.py --firmware X6871
python3 phoenix_api_client.py --download --project X6871 --version-id V1 --output-dir ./fw
```

## Library

```python
from phoenix import Downloader, FirmwareService, FrboxClient, PhoenixClient

client = PhoenixClient()
client.restore() or client.login("you@example.com", "password")

versions = FirmwareService(client).list("X6871")
share = FirmwareService(client).link("X6871", versions[0].version)
files = FrboxClient().resolve(share)
for path in (Downloader("./firmware").save(f) for f in files):
    print(path)
```

## Tests

```bash
uv run pytest
uv run ruff check .
```
