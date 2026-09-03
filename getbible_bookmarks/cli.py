"""Command line interface: validate, build, import bundles, publish, tokens, serve."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .api.auth import ROLES, AuthError, ContributorStore
from .build import catalog_checksum, render_api, stale_paths, write_tree
from .bundle import apply_bundle
from .model import CatalogError
from .release import ReleaseError, publish_release
from .sources import load_catalog, save_catalog


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if options.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        handler = options.handler
        result: int = handler(options)
        return result
    except (CatalogError, AuthError, ReleaseError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="getbible-bookmarks",
        description="Build and manage the getBible bookmarks catalogue and its static JSON API.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(os.environ.get("BOOKMARKS_REPO_DIR", ".")),
        help="repository checkout (default: $BOOKMARKS_REPO_DIR or the current directory)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate the canonical sources under data/")
    validate.set_defaults(handler=_validate)

    build = commands.add_parser("build", help="render the v1/ static API tree from data/")
    build.add_argument("--output", type=Path, help="output directory (default: <repo>/v1)")
    build.add_argument(
        "--check", action="store_true", help="verify the committed tree instead of writing"
    )
    build.set_defaults(handler=_build)

    bundle = commands.add_parser(
        "import-bundle", help="apply a robot contribution bundle to data/ and rebuild v1/"
    )
    bundle.add_argument("bundle", type=Path, help="path to the schema-version-1 bundle JSON")
    bundle.add_argument("--check", action="store_true", help="validate without writing")
    bundle.set_defaults(handler=_import_bundle)

    publish = commands.add_parser("publish", help="publish v1/ into the nginx release directory")
    publish.add_argument(
        "--target", type=Path, required=True, help="release root, e.g. /srv/getbible-bookmarks"
    )
    publish.add_argument("--source", type=Path, help="tree to publish (default: <repo>/v1)")
    publish.add_argument(
        "--keep", type=int, default=3, help="previous releases to keep (default 3)"
    )
    publish.set_defaults(handler=_publish)

    tokens = commands.add_parser("tokens", help="manage contributor tokens (never over HTTP)")
    tokens.add_argument(
        "--file",
        type=Path,
        default=Path(os.environ.get("BOOKMARKS_CONTRIBUTORS_FILE", "contributors.json")),
        help="registry path (default: $BOOKMARKS_CONTRIBUTORS_FILE or ./contributors.json)",
    )
    token_commands = tokens.add_subparsers(dest="token_command", required=True)
    create = token_commands.add_parser(
        "create", help="enrol a contributor and print the token once"
    )
    create.add_argument("--id", required=True, help="stable contributor id (lowercase slug)")
    create.add_argument("--name", required=True, help="git author name for their commits")
    create.add_argument("--email", required=True, help="git author email for their commits")
    create.add_argument("--role", choices=ROLES, default="contributor")
    create.set_defaults(handler=_tokens_create)
    listing = token_commands.add_parser("list", help="list contributors (no secrets)")
    listing.set_defaults(handler=_tokens_list)
    revoke = token_commands.add_parser("revoke", help="revoke a contributor token")
    revoke.add_argument("--id", required=True)
    revoke.set_defaults(handler=_tokens_revoke)
    rotate = token_commands.add_parser(
        "rotate", help="replace a token and reactivate the contributor"
    )
    rotate.add_argument("--id", required=True)
    rotate.set_defaults(handler=_tokens_rotate)
    role = token_commands.add_parser("set-role", help="change a contributor's role")
    role.add_argument("--id", required=True)
    role.add_argument("--role", choices=ROLES, required=True)
    role.set_defaults(handler=_tokens_set_role)

    serve = commands.add_parser("serve", help="run the management API (configured by environment)")
    serve.set_defaults(handler=_serve)
    return parser


def _validate(options: argparse.Namespace) -> int:
    catalog = load_catalog(options.repo / "data")
    print(
        f"OK: {len(catalog.topics)} topics, {catalog.link_count()} verse links, "
        f"{len(catalog.locales)} locales, {len(catalog.retired)} retired "
        f"(catalog version {catalog.catalog_version})."
    )
    return 0


def _build(options: argparse.Namespace) -> int:
    catalog = load_catalog(options.repo / "data")
    files = render_api(catalog)
    output = options.output or options.repo / "v1"
    if options.check:
        stale = stale_paths(output, files)
        if stale:
            shown = ", ".join(stale[:8]) + (" ..." if len(stale) > 8 else "")
            print(
                f"STALE: {len(stale)} file(s) differ from a fresh build: {shown}", file=sys.stderr
            )
            print("Run `getbible-bookmarks build` and commit the result.", file=sys.stderr)
            return 1
        print(
            f"Verified {len(files)} files (catalog version {catalog.catalog_version}, checksum {catalog_checksum(files)})."
        )
        return 0
    write_tree(output, files)
    print(
        f"Generated {len(files)} files into {output} (catalog version {catalog.catalog_version}, checksum {catalog_checksum(files)})."
    )
    return 0


def _import_bundle(options: argparse.Namespace) -> int:
    data = options.repo / "data"
    catalog = load_catalog(data)
    try:
        document = json.loads(options.bundle.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise CatalogError(f"{options.bundle} is not readable JSON: {error}") from error
    result = apply_bundle(catalog, document)
    if not result.changed():
        print("The bundle is already fully applied; nothing to do.")
        return 0
    catalog.catalog_version += 1
    files = render_api(catalog)
    if options.check:
        print(
            f"Valid: would create {len(result.topics_created)} topic(s), extend "
            f"{len(result.topics_extended)}, add {result.verses_added} and remove "
            f"{result.verses_removed} verse link(s) as catalog version {catalog.catalog_version}."
        )
        return 0
    save_catalog(data, catalog)
    write_tree(options.repo / "v1", files)
    print(
        f"Applied: created {len(result.topics_created)} topic(s), extended "
        f"{len(result.topics_extended)}, added {result.verses_added} and removed "
        f"{result.verses_removed} verse link(s); catalog version {catalog.catalog_version}, "
        f"checksum {catalog_checksum(files)}."
    )
    return 0


def _publish(options: argparse.Namespace) -> int:
    source = options.source or options.repo / "v1"
    release = publish_release(source, options.target, keep=options.keep)
    print(f"Published {release}; {options.target / 'current'} now points at it.")
    return 0


def _tokens_create(options: argparse.Namespace) -> int:
    store = ContributorStore(options.file)
    entry, token = store.create(
        contributor_id=options.id, name=options.name, email=options.email, role=options.role
    )
    print(f"Created {entry.id} ({entry.role}). Token, shown once:\n{token}")
    return 0


def _tokens_list(options: argparse.Namespace) -> int:
    store = ContributorStore(options.file)
    entries = store.load()
    if not entries:
        print("No contributors enrolled.")
        return 0
    for entry in entries:
        status = "revoked " + entry.revoked_at if entry.revoked_at else "active"
        print(f"{entry.id:24} {entry.role:12} {status:32} {entry.name} <{entry.email}>")
    return 0


def _tokens_revoke(options: argparse.Namespace) -> int:
    entry = ContributorStore(options.file).revoke(options.id)
    print(f"Revoked {entry.id} at {entry.revoked_at}.")
    return 0


def _tokens_rotate(options: argparse.Namespace) -> int:
    entry, token = ContributorStore(options.file).rotate(options.id)
    print(f"Rotated {entry.id}. New token, shown once:\n{token}")
    return 0


def _tokens_set_role(options: argparse.Namespace) -> int:
    entry = ContributorStore(options.file).set_role(options.id, options.role)
    print(f"{entry.id} is now a {entry.role}.")
    return 0


def _serve(options: argparse.Namespace) -> int:
    from .api.app import serve_from_environment  # noqa: PLC0415 - tornado only when serving

    return serve_from_environment(repo_dir=options.repo)


__all__ = ["build_parser", "main"]
