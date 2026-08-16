# Piper Voice Installation

HotPepperPodcast uses the official Piper voice manifest:

```text
https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json
```

Each catalog entry identifies a model, companion `.onnx.json`, and `MODEL_CARD`. The official manifest currently supplies MD5 digests for the model files; HotPepperPodcast also supports SHA-256 entries in local/curated catalogs. MD5 is the upstream integrity field, not an independent cryptographic security guarantee, so release-critical deployments should use a separately pinned SHA-256 catalog when available.

## Catalog cache

Remote catalog metadata is cached at:

```text
${XDG_CACHE_HOME:-$HOME/.cache}/hotpepperpodcast/voices.json
```

A fresh cache is used for 24 hours. If refresh fails, an older cache may be used and the command reports the resulting catalog data rather than pretending it is current. Force a refresh with:

```bash
./scripts/run.sh voices catalog --no-cache
```

The cache is separate from both the project repository and the private notes under `~/Logs/HotPepperPodcast`.

## List voices

```bash
./scripts/run.sh voices catalog --language en_US --limit 20
```

The catalog command does not download model files. It only reads metadata.

## Install a voice

```bash
./scripts/run.sh voices install en_US-amy-medium
```

The command:

1. Shows the voice, source, model-card URL, and license metadata.
2. Prompts for a destination, with Enter accepting the default user-owned path.
3. Requires explicit license acceptance.
4. Downloads model, config, and model card into `.part` files.
5. Resumes downloads when the server supports HTTP ranges.
6. Prints byte progress for each asset.
7. Verifies the manifest digest.
8. Validates Piper JSON metadata.
9. Activates the files only after validation.
10. Retains the model card beside the installed voice.

For scripts or CI, review the license separately and pass:

```bash
./scripts/run.sh voices install en_US-amy-medium --accept-license
```

Do not use `--accept-license` without reviewing the model card and its upstream terms. Piper voices do not share one universal license.

## Verify

```bash
./scripts/run.sh voices verify en_US-amy-medium
```

Verification checks the model digest, companion file, model card, Piper metadata, and interrupted-install marker.

## Destination and permissions

The default is:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/hotpepperpodcast/voices
```

The installer does not run `sudo`, ask for a password, or recursively change ownership. If a protected directory is selected, it prints a visible, narrow copyable command that stages the files and uses `sudo install` for the exact paths. A user-owned directory is preferred for ordinary installations.

## Failure behavior

- Failed downloads remain only as temporary files while the current operation runs; checksum failures clean up partial artifacts.
- Invalid JSON, missing Piper metadata, missing model cards, and digest mismatches prevent activation.
- An interrupted pair activation leaves a marker so verification refuses to treat the voice as healthy.
- A missing or unavailable catalog produces a readable error and a non-zero exit status.
