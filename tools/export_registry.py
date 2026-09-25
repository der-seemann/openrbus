#!/usr/bin/env python3
"""Export the public JSON registry for constrained and embedded consumers.

This tool reads only the normalized public JSON shipped with OpenRBus.  It has
no importer for SQLite or proprietary formats.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

try:
    from openrbus.registry import Registry, export_c_header, export_compact_json
except ModuleNotFoundError:  # Support an uninstalled source checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from openrbus.registry import Registry, export_c_header, export_compact_json


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("compact-json", "c-header"),
        required=True,
        help="output format",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        help="normalized public registry JSON (defaults to the packaged dataset)",
    )
    parser.add_argument("--output", type=Path, help="output file (defaults to stdout)")
    parser.add_argument("--locale", choices=("de", "en"), default="en")
    parser.add_argument(
        "--symbol-prefix",
        default="OPENRBUS",
        help="uppercase C identifier prefix for c-header output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a deterministic export."""

    args = build_parser().parse_args(argv)
    registry = Registry.load(args.registry) if args.registry else Registry.load_default()
    if args.format == "compact-json":
        rendered = export_compact_json(registry, locale=args.locale)
    else:
        rendered = export_c_header(
            registry,
            locale=args.locale,
            symbol_prefix=args.symbol_prefix,
        )
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
