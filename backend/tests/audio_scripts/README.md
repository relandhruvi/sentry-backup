# Synthetic test audio scripts

**Everything rendered from these scripts is SYNTHETIC text-to-speech audio** (macOS `say`).
Every script id and every rendered file name starts with `synthetic_`.

Use them **only for pipeline testing**: ingest, VAD segmentation, STT plumbing and latency,
and end-to-end event flow.

**Never** use them to train, calibrate or evaluate the voice anti-spoof model. Every one of these
files is machine-generated speech, so they would teach the voice signal that all calls are
synthetic, and any accuracy number measured on them would be meaningless.

The STT accuracy numbers measured on them (WER) describe Whisper on clean macOS TTS. They are a
smoke test, not an estimate of accuracy on real phone or VoIP speech.

## Format

```yaml
id: synthetic_<category>_<name>     # also the output file name
category: scam | benign_urgent | normal
speakers:
  caller: {voice: <say voice>, rate: <words per minute>}
  elder:  {voice: <say voice>, rate: <words per minute>}
turns:
  - {speaker: caller, lang: en | hi | hinglish, text: "..."}
```

`lang` is only used to group the WER report. The pipeline never sees it.
Hinglish appears in two scripts: romanized (read by an en-IN voice) and Devanagari mixed with
English words (read by the hi-IN voice).

## Render

```bash
make test-audio      # -> ../samples/synthetic_tts/<id>.wav + <id>.json (turn timings)
```

The rendered files go under the gitignored `samples/` folder and are never committed.

## Voices used

Installed on the build machine: en-IN Rishi, Aman, Tara and hi-IN Lekha. There is only one Hindi
voice, so Hindi-only calls use Lekha for both speakers at different rates. To get a second Hindi
voice, add one in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → Hindi,
then set it in the script's `speakers`.
