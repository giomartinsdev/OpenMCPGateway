"""CLI: run the server, or apply a YAML file without the UI.

    omg serve
    omg apply gateway.yaml          # one-shot reconciliation
    omg export                      # print current state as YAML
    omg operator add someone@x.dev  # create an operator (prompts for password)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

from gateway.config import ConfigError, Settings


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        print(
            "\nRequired environment:\n"
            "  OMG_DATABASE_URL   postgresql://user:pass@host:5432/db\n"
            "  OMG_SESSION_SECRET at least 32 bytes (openssl rand -hex 32)\n"
            "  OMG_BASE_URL       public URL, e.g. https://gateway.example.com\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


async def _container(settings: Settings):
    from gateway.application.container import Container

    return await Container.create(settings)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from gateway.asgi import build_asgi

    settings = _settings()
    host = args.host or settings.host
    port = args.port or settings.port
    uvicorn.run(
        build_asgi(settings),
        host=host,
        port=port,
        log_level=os.environ.get("OMG_LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from gateway.configfile import apply_document, parse_document

    settings = _settings()
    path = Path(args.file)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    document = parse_document(path.read_text())

    async def run() -> int:
        container = await _container(settings)
        try:
            result = await apply_document(
                document, container.commands, container.read_models, actor="cli"
            )
            for key in ("created", "updated", "unchanged", "pruned"):
                if result[key]:
                    print(f"{key}: {', '.join(result[key])}")
            # Newly issued keys are shown once — capture them now or never.
            for entry in result["api_keys"]:
                print(f"api key [{entry['gateway']}/{entry['label']}]: {entry['key']}")
            for error in result["errors"]:
                print(f"error: {error}", file=sys.stderr)
            return 1 if result["errors"] else 0
        finally:
            await container.close()

    return asyncio.run(run())


def cmd_export(args: argparse.Namespace) -> int:
    del args
    settings = _settings()

    async def run() -> int:
        container = await _container(settings)
        try:
            from gateway.interface.api import _gateway_yaml

            lines: list[str] = []
            for gateway in await container.read_models.list_gateways():
                principals = await container.read_models.principals(gateway["id"])
                keys = await container.read_models.api_keys(gateway["id"])
                lines.append(_gateway_yaml(gateway, principals, keys))
            print("gateways:\n" + "\n".join(lines) if lines else "gateways: []")
            return 0
        finally:
            await container.close()

    return asyncio.run(run())


def cmd_operator_add(args: argparse.Namespace) -> int:
    settings = _settings()
    password = args.password or getpass.getpass("password: ")
    if len(password) < 12:
        print("password must be at least 12 characters", file=sys.stderr)
        return 2

    async def run() -> int:
        container = await _container(settings)
        try:
            operator_id = await container.commands.register_operator(
                email=args.email, password=password, is_admin=args.admin
            )
            print(f"created {args.email} ({operator_id})")
            return 0
        finally:
            await container.close()

    return asyncio.run(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omg", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API and the proxies")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    apply_cmd = sub.add_parser("apply", help="reconcile gateways from a YAML file")
    apply_cmd.add_argument("file")
    apply_cmd.set_defaults(func=cmd_apply)

    export_cmd = sub.add_parser("export", help="print current gateways as YAML")
    export_cmd.set_defaults(func=cmd_export)

    operator = sub.add_parser("operator", help="manage operator accounts")
    operator_sub = operator.add_subparsers(dest="operator_command", required=True)
    add = operator_sub.add_parser("add", help="create an operator")
    add.add_argument("email")
    add.add_argument("--password", help="skip the prompt (CI only)")
    add.add_argument("--admin", action="store_true")
    add.set_defaults(func=cmd_operator_add)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
