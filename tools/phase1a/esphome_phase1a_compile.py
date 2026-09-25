#!/usr/bin/env python3
"""Run ESPHome codegen, apply the pinned ATT gate, then compile with ESP-IDF."""

from __future__ import annotations

import sys
from pathlib import Path

import esphome.__main__ as esphome_main
from esphome.const import __version__ as esphome_version
from esphome.core import CORE
from esphome_phase1a_gate import SUPPORTED_ESPHOME_VERSION, apply_gate


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: esphome_phase1a_compile.py CONFIG.yaml")
    if esphome_version != SUPPORTED_ESPHOME_VERSION:
        raise SystemExit(
            f"unsupported ESPHome {esphome_version}; expected {SUPPORTED_ESPHOME_VERSION}"
        )

    config = str(Path(sys.argv[1]).resolve())
    original_write_cpp = esphome_main.write_cpp

    def write_cpp_then_gate(parsed_config: object) -> int:
        result = original_write_cpp(parsed_config)
        if result != 0:
            return result
        apply_gate(Path(CORE.build_path) / "src")
        print("PHASE1A_GATE_APPLIED_AND_VERIFIED")
        return 0

    esphome_main.write_cpp = write_cpp_then_gate
    return int(esphome_main.run_esphome(["esphome", "compile", config]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
