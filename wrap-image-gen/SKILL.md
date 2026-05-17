---
name: wrap-image-gen
description: Run a portable wrapper around the bundled imagegen fallback CLI with user-filled OPENAI_API_KEY and OPENAI_BASE_URL placeholders. Use when the user wants to generate or edit images from the CLI through gpt-image-2 or other GPT Image models without rewriting dependency setup commands.
---

# Wrap Image Gen

Use this skill when image generation should go through the bundled `imagegen` fallback CLI with a wrapper that sets API configuration from user-filled placeholders and runs it through `uv`.

## Workflow

1. Run `scripts/wrap-image-gen.sh` with the needed image generation or edit arguments.
2. If the wrapper reports that credentials are missing, tell the user to edit the placeholder values at the top of that script. Do not print or expose real credentials.
3. If `uv` is missing, offer to help install it after user approval, or ask the user to set `UV_BIN` to an existing `uv` executable. Do not make the wrapper install dependencies automatically.
4. Prefer `gpt-image-2` unless the user explicitly asks for another GPT Image model or needs a model-specific feature such as native transparent output.
5. Keep final project outputs under `output/imagegen/` unless the user requests another path.
6. Do not print or expose real API keys. If debugging, verify only whether `OPENAI_API_KEY` and `OPENAI_BASE_URL` are set.

## Command

Run the wrapper directly from the skill directory:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/wrap-image-gen/scripts/wrap-image-gen.sh" generate \
  --model gpt-image-2 \
  --prompt "A cozy alpine cabin at dawn" \
  --size 1024x1024 \
  --quality medium \
  --out output/imagegen/test.png
```

The wrapper forwards all arguments to:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/image_gen.py"
```

If the bundled CLI is installed somewhere else, set `IMAGEGEN_CLI` to that script path. If `uv` is not on `PATH`, set `UV_BIN` to the `uv` executable path.

## Notes

- The wrapped CLI uses the Python OpenAI SDK, so `OPENAI_API_KEY` is required for real API calls.
- `OPENAI_BASE_URL` is optional when using the default OpenAI endpoint, but useful for custom compatible gateways.
- The wrapper should detect a missing `uv` and report it; Codex can help install `uv` only after asking the user.
- `--dry-run` can be used to inspect payload and paths without making a network call.
