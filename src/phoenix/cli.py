"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

import rich_click as click
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from .client import PhoenixClient
from .config import EP_REGISTER, Settings
from .downloader import Downloader
from .errors import PhoenixError
from .firmware import FirmwareService
from .frbox import FrboxClient
from .models import RemoteFile

console = Console()


def _configure_logging(verbose: int) -> None:
    logger.remove()
    level = "DEBUG" if verbose > 1 else "INFO" if verbose else "WARNING"
    logger.add(sys.stderr, level=level, format="<level>{level: <7}</level> {message}")


def _credentials(
    email: str | None = None,
    password: str | None = None,
    *,
    force_prompt: bool = False,
) -> tuple[str, str]:
    email = email or os.environ.get("PHOENIX_EMAIL")
    password = password or os.environ.get("PHOENIX_PASSWORD")
    interactive = force_prompt or sys.stdin.isatty()
    if not email:
        if not interactive:
            raise click.ClickException("No saved session. Pass --email or set PHOENIX_EMAIL.")
        email = click.prompt("Email")
    if not password:
        if not interactive:
            raise click.ClickException("Password required. Pass --password or set PHOENIX_PASSWORD.")
        password = click.prompt("Password", hide_input=True)
    return email.strip(), password


class App:
    def __init__(self, settings: Settings, *, no_session: bool = False) -> None:
        self.settings = settings
        self.no_session = no_session
        self._client: PhoenixClient | None = None

    @property
    def client(self) -> PhoenixClient:
        if self._client is None:
            self._client = PhoenixClient(self.settings)
        return self._client

    def authenticated(self) -> PhoenixClient:
        client = self.client
        if not self.no_session and client.restore():
            return client
        email, password = _credentials()
        client.login(email, password)
        return client


@click.group(
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100},
)
@click.option("--base-url", default=None, help="API base URL.")
@click.option("--no-verify-ssl", is_flag=True, help="Disable TLS verification.")
@click.option(
    "--session-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Session file path.",
)
@click.option("--no-session", is_flag=True, help="Ignore the saved session.")
@click.option("-v", "--verbose", count=True, help="Log verbosity (-v info, -vv debug).")
@click.version_option(package_name="phoenix")
@click.pass_context
def cli(
    ctx: click.Context,
    base_url: str | None,
    no_verify_ssl: bool,
    session_file: Path | None,
    no_session: bool,
    verbose: int,
) -> None:
    """Transsion firmware client for phoenixservicetool.com."""
    _configure_logging(verbose)
    overrides: dict[str, object] = {}
    if base_url:
        overrides["base_url"] = base_url
    if no_verify_ssl:
        overrides["verify_ssl"] = False
    if session_file:
        overrides["session_file"] = session_file
    ctx.obj = App(Settings.from_env(**overrides), no_session=no_session)


@cli.command()
@click.option("-e", "--email")
@click.option("-p", "--password")
@click.option("--no-save", is_flag=True, help="Do not persist the session token.")
@click.pass_obj
def login(app: App, email: str | None, password: str | None, no_save: bool) -> None:
    """Log in and save the session token."""
    email, password = _credentials(email, password, force_prompt=True)
    session = app.client.login(email, password, remember=not no_save)
    console.print(f"[green]Logged in[/green] as {session.name or email} (credit: {session.credit})")
    if no_save:
        console.print("[dim]Session not saved[/dim]")


@cli.command()
@click.pass_obj
def logout(app: App) -> None:
    """Clear the saved session."""
    console.print("Session cleared" if app.client.logout() else "No saved session")


@cli.command()
@click.pass_obj
def status(app: App) -> None:
    """Check server status (public)."""
    data = app.client.server_status()
    console.print(f"[bold]Status:[/bold] {data.get('status', '?')}")
    if data.get("message"):
        console.print(str(data["message"]))


@cli.command()
@click.pass_obj
def info(app: App) -> None:
    """Show server information."""
    console.print_json(data=app.authenticated().server_info())


@cli.command()
@click.pass_obj
def models(app: App) -> None:
    """List device models."""
    items = app.authenticated().models()
    table = Table("Brand", "Model")
    for item in items:
        table.add_row(item.brand, item.model)
    console.print(f"[bold]{len(items)}[/bold] model(s)")
    console.print(table)


@cli.command()
@click.argument("project")
@click.pass_obj
def firmware(app: App, project: str) -> None:
    """List firmware versions for PROJECT (free).

    A bare code also searches its variants, so X670 includes X670-T and
    X6871 includes X6871(Android 15) and X6871(Android 16).
    """
    versions = FirmwareService(app.authenticated()).list(project)
    if not versions:
        console.print("[yellow]No firmware found[/yellow]")
        return
    table = Table("Version", "Project", "Market", "Date")
    for version in versions:
        table.add_row(
            version.version,
            version.project or project.upper(),
            version.market or "-",
            version.date or "-",
        )
    console.print(f"[bold]{len(versions)}[/bold] firmware version(s) for {project.upper()}")
    console.print(table)


def _download_one(
    downloader: Downloader,
    item: RemoteFile,
    refresh_url: Callable[[], str],
) -> Path:
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(item.name, total=item.size or None)
        known_total = item.size or None

        def on_progress(downloaded: int, total: int | None) -> None:
            nonlocal known_total
            if total and known_total is None:
                known_total = total
                progress.update(task, total=total)
            progress.update(task, completed=downloaded)

        path = downloader.save(item, on_progress=on_progress, refresh_url=refresh_url)
    console.print(f"[green]Saved[/green] {path}")
    return path


@cli.command()
@click.argument("project")
@click.argument("version")
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("."),
    show_default=True,
    help="Directory for downloaded files.",
)
@click.option("--overwrite", is_flag=True, help="Replace existing files.")
@click.option("-y", "--yes", is_flag=True, help="Skip the credit confirmation.")
@click.option(
    "-w",
    "--workers",
    type=click.IntRange(1, 32),
    default=4,
    show_default=True,
    help="Parallel connections (1 disables parallelism).",
)
@click.pass_obj
def download(
    app: App,
    project: str,
    version: str,
    output_dir: Path,
    overwrite: bool,
    yes: bool,
    workers: int,
) -> None:
    """Download VERSION for PROJECT (costs one credit)."""
    if not yes:
        click.confirm(
            f"Resolve {project.upper()} {version} and download? This costs 1 credit",
            abort=True,
        )
    share = FirmwareService(app.authenticated()).link(project, version)
    console.print(f"Share: [cyan]{share.public_url}[/cyan]")
    frbox = FrboxClient(app.settings)
    files = frbox.resolve(share)
    downloader = Downloader(output_dir, overwrite=overwrite, workers=workers)
    paths = [
        _download_one(downloader, item, lambda item=item: frbox.download_url(share, item))
        for item in files
    ]
    console.print(f"[green]Downloaded {len(paths)} file(s)[/green]")


@cli.command()
@click.pass_obj
def register(app: App) -> None:
    """Open the registration page."""
    url = app.settings.base_url.rstrip("/") + EP_REGISTER
    console.print(url)
    click.launch(url)


def run(argv: list[str]) -> int:
    """Run the CLI and turn expected failures into exit codes."""
    try:
        cli.main(args=argv, prog_name="phoenix", standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code
    except click.exceptions.Abort:
        console.print("[yellow]Aborted[/yellow]")
        return 1
    except PhoenixError as exc:
        console.print(f"[red]error:[/red] {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


# --- legacy flag interface (kept for phoenix_api_client.py) ------------------


def _legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, prog="phoenix_api_client.py")
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-e", "--email")
    parser.add_argument("-p", "--password")
    parser.add_argument("--base-url")
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--no-session", action="store_true")
    parser.add_argument("--login", action="store_true")
    parser.add_argument("--logout", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--models", action="store_true")
    parser.add_argument("--list-firmware", action="store_true")
    parser.add_argument("--firmware", metavar="PROJECT")
    parser.add_argument("--project")
    parser.add_argument("--version-id")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--register", action="store_true")
    return parser


def _legacy_command(args: argparse.Namespace) -> list[str]:
    if args.logout:
        return ["logout"]
    if args.register:
        return ["register"]
    if args.status:
        return ["status"]
    if args.info:
        return ["info"]
    if args.models:
        return ["models"]
    project = args.firmware or args.project
    if args.firmware or args.list_firmware:
        if not project:
            raise SystemExit("--firmware requires a project name (e.g., --firmware X6871)")
        return ["firmware", project]
    if args.download:
        if not args.project or not args.version_id:
            raise SystemExit("--download requires --project and --version-id")
        # The legacy CLI downloaded without an extra confirmation prompt.
        return ["download", args.project, args.version_id, "--yes", "-o", args.output_dir]
    return []


def legacy_main(argv: list[str] | None = None) -> int:
    """Translate the old flat flags into subcommands."""
    args = _legacy_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.email:
        os.environ["PHOENIX_EMAIL"] = args.email
    if args.password:
        os.environ["PHOENIX_PASSWORD"] = args.password

    global_args: list[str] = []
    if args.base_url:
        global_args += ["--base-url", args.base_url]
    if args.no_verify_ssl:
        global_args.append("--no-verify-ssl")
    if args.no_session:
        global_args.append("--no-session")

    if args.help:
        return run([*global_args, "--help"])
    if args.login:
        login_args = ["login"]
        if args.email:
            login_args += ["-e", args.email]
        if args.password:
            login_args += ["-p", args.password]
        code = run([*global_args, *login_args])
        if code:
            return code
        command = _legacy_command(args)
        return run([*global_args, *command]) if command else 0

    command = _legacy_command(args)
    return run([*global_args, *command]) if command else run([*global_args, "--help"])
