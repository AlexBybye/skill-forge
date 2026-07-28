---
name: canonical-json
description: Canonicalize JSON deterministically and reject duplicate object keys. Use when stable byte-for-byte JSON or duplicate-key rejection is required.
---

# Canonical JSON

Reject duplicate object keys before conversion. Sort object keys by Unicode code point, use compact separators, preserve JSON value types, encode as UTF-8, and end the output with exactly one newline.
