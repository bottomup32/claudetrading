"""CLI: python -m translator.cli "안녕하세요"  (or pipe text via stdin)."""

from __future__ import annotations

import sys

from translator.translate import translate


def main() -> int:
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    try:
        result = translate(text)
    except Exception as e:  # noqa: BLE001 - surface any failure to the terminal
        print(f"오류: {e}", file=sys.stderr)
        return 1
    print(f"[中文] {result.zh}")
    print(f"[English] {result.en}")
    print(f"[日本語] {result.ja}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
