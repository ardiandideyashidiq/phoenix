# phoenix

Transsion firmware client for [phoenixservicetool.com](https://phoenixservicetool.com).
Listing is free; resolving a version to a share (`download`) costs one credit.

## Install

```bash
uv tool install git+https://github.com/ardiandideyashidiq/phoenix.git
# from a checkout: uv sync
```

## Usage

```bash
phoenix login -e you@example.com        # password prompt; or PHOENIX_EMAIL/PHOENIX_PASSWORD
phoenix firmware X6871                  # list versions (free), newest first
phoenix firmware "X6871(Android 16)"    # one variant only
phoenix download X6871 <version> -o ./firmware -w 4
```

Global flags go before the command (`--no-verify-ssl`, `--session-file PATH`, `-v`).
The token is saved to `~/.phoenix_session.json` (mode 0600).

Phoenix keeps a separate catalogue per variant (`X670-T`, `X6871(Android 15)`,
`X6871(Android 16)`). A bare code lists all of them; `download` resolves the right one.

## Downloads

The share URL includes `?pwd=...`. Files are ranged over `--workers` connections
(default 4, `1` disables parallelism), resume from `*.part`/`*.part.json`, and
refresh expired signed URLs automatically.

## Library

```python
from phoenix import Downloader, FirmwareService, FrboxClient, PhoenixClient

client = PhoenixClient()
client.restore() or client.login("you@example.com", "password")

versions = FirmwareService(client).list("X6871")
share = FirmwareService(client).link("X6871", versions[0].version)
for path in (Downloader("./firmware").save(f) for f in FrboxClient().resolve(share)):
    print(path)
```

## Development

```bash
uv run pytest
uv run ruff check .
python3 phoenix_api_client.py --firmware X6871   # legacy flat-flag interface
```
