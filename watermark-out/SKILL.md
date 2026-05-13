---
name: watermark-out
description: Remove AI-generation notices from reviewed/finalized PDF or DOCX files, including Chinese lines like "由AI生成" or "内容由 AI 生成，请谨慎参考".
---

# Watermark Out

## Overview

Use this skill to remove AI-generation notice watermarks from reviewed PDF or DOCX files. Keep the operation narrow and verify the output.

## Workflow

1. Confirm the user has authority to remove the notice. If already stated, proceed.
2. Run `scripts/watermark_out.py` with `--dry-run` first to inspect matches.
3. Write the cleaned file. The script validates the output and rescans for remaining matches.
4. For PDFs, adjust `--footer-ratio` if needed. For either format, add a specific `--pattern` if needed.

## Quick Start

```bash
python3 scripts/watermark_out.py input.pdf
python3 scripts/watermark_out.py input.docx
```

Useful options:

```bash
python3 scripts/watermark_out.py input.pdf --output cleaned.pdf
python3 scripts/watermark_out.py input.docx --output cleaned.docx
python3 scripts/watermark_out.py input.pdf --dry-run
python3 scripts/watermark_out.py input.pdf --footer-ratio 0.18
python3 scripts/watermark_out.py input.docx --pattern "由\\s*AI\\s*生成"
```

When running from outside the skill directory, set the skill location once and call the script by relative path:

```bash
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/watermark-out"
python3 "$SKILL_DIR/scripts/watermark_out.py" input.pdf
```

DOCX support uses only Python standard library modules. PDF support requires `pypdf`.

## Matching Rules

For PDFs, the script only removes a text object when all are true:

- It is in the footer region by page coordinates.
- Its normalized text matches notice patterns such as `由AI生成`, `AI生成`, `内容由AI生成`, `谨慎参考`, or `仅供参考`.
- The whole matched text object stays inside the footer band and is short enough to be a notice.

Use `--pattern` to add a custom regular expression for a specific watermark. Keep custom patterns specific enough to avoid deleting legitimate content.

For DOCX files, the script removes matching short paragraphs from `word/document.xml` and header/footer XML parts. It is intended for exported AI notice paragraphs, not image or shape watermarks.

## Validation

After writing the output, the script validates automatically:

- Output path is not the input path.
- For PDFs, page count is unchanged.
- For DOCX files, package entries are unchanged.
- No matching notice remains in the output.

## Safety Notes

- Do not remove legal notices, copyright notices, provenance statements, or required disclosures unless the user explicitly confirms that removal is appropriate.
- This script removes PDF text objects or DOCX text paragraphs. It is not intended for image/baked-in watermarks.
- Browser-generated PDFs may split Chinese text into glyph-specific font encodings; the script uses extracted text positions to find and edit the containing text object.
