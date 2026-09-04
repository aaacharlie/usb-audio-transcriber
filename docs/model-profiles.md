# Whisper model profiles

## Recommendation

Use `fast` for routine English recordings. Use `accurate` only when difficult speech justifies a much longer run. Use `both` for an explicit comparison, not as the everyday default.

| Profile | faster-whisper model | Typical reason to use it | Trade-off |
| --- | --- | --- | --- |
| `fast` | `distil-large-v3` | Routine transcription with strong English accuracy | May be less robust on distant, overlapping, or difficult speech |
| `accurate` | `large-v3` | A difficult recording where maximum supported accuracy matters | Substantially slower and uses a larger disk cache |
| `both` | both, sequentially | Compare outputs from exactly the same recording | Combined runtime and storage |

## Measured example

A real 57 minute 45 second recording on the original CPU-based deployment — a GEEKOM X16 laptop (NX16AM) with an Intel Core Ultra 9 185H (16 cores / 22 threads), 32 GB RAM, and integrated graphics, running Ubuntu 26.04 LTS with GNOME — produced:

| Model | Elapsed time | Throughput |
| --- | ---: | ---: |
| `distil-large-v3` | 16m 56s | 3.41x real time |
| `large-v3` | 89m 57s | 0.64x real time |

On that machine and recording, `large-v3` was 5.31 times slower. The transcripts had 74.2% word-sequence similarity, but similarity is not an accuracy score. A human-reviewed reference transcript is required to establish which wording is correct. Performance varies by hardware, audio, language, and compute configuration.

## Disk and RAM behavior

Observed Hugging Face cache sizes were approximately:

- `distil-large-v3`: 1.41 GiB
- `large-v3`: 2.88 GiB

Downloaded weights occupy disk so they can be reused and used offline. They are not permanently loaded in RAM. The pipeline loads one profile at a time and releases it before loading the next.

## Select a profile

Edit the installed `config.env`:

```ini
WHISPER_MODEL_PROFILE="fast"
```

Accepted values are `fast`, `accurate`, and `both`.

To preserve a custom legacy model ID, leave the profile empty:

```ini
WHISPER_MODEL_PROFILE=""
WHISPER_MODEL="medium.en"
```

Custom models do not have the curated performance and cache metadata of the built-in profiles.

## Benchmark responsibly

A comparison should use the same source audio, device, compute type, language, VAD settings, and beam size. Compare known difficult passages against human listening rather than choosing whichever transcript is longer or more fluent.
