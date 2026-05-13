#!/usr/bin/env python3
"""Backward-compatible wrapper for the renamed watermark_out.py entrypoint."""

from watermark_out import main


if __name__ == "__main__":
    raise SystemExit(main())
