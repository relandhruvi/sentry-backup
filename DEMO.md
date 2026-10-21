# Scam Shield live demo

## Start

```bash
cd ~/Sentry
make demo
```

`make demo` does the following:

1. Installs anything missing: the backend venv and the frontend `node_modules`.
2. Starts the backend and waits until speech-to-text has loaded **and run a warm-up inference**,
   and the classifier has loaded. The first run downloads the Whisper models.
3. Prints `/health` for every component.
4. Starts the frontend and opens **http://localhost:5173/demo**.

`Ctrl-C` stops both. Backend log: `backend/data/demo-backend.log`.

If port 8000 is busy: `kill $(lsof -tnP -iTCP:8000 -sTCP:LISTEN)`.

## Before the audience arrives (2 minutes)

1. **Check the health strip at the top of /demo.** Speech-to-text, Classifier and Lexicon must be
   green `ok`. Voice check and Verification show `unavailable`; that's true (not built yet), and
   the UI never fakes them.
2. **Allow the microphone.** Click **Start listening** once, then allow the mic when Chrome asks.
   On macOS the first time, also allow it under System Settings → Privacy & Security → Microphone →
   Google Chrome.
3. **Do one quick test.** Say the benign call below and confirm the transcript appears.
   Transcripts arrive about 1.5–2 s after each sentence.
4. **Keep the room quiet** and the laptop mic about 30 cm from the speaker. Pause about 1 second
   between sentences; that's where the system cuts utterances.

## Run

- **Live:** click **🎙 Start listening**, speak, then click **■ Stop**. Stopping finishes
  transcribing what was already said.
- **Recorded:** click **Upload WAV**. The file plays through the speakers and goes through
  exactly the same path as the mic. The `samples/synthetic_tts/*.wav` calls work; those are
  machine voices, so say so.

The page shows:
- the risk meter and level
- risk over time
- the scam-pattern chain (AUTHORITY → THREAT → SECRECY → PAYMENT)
- the live transcript with tactic words highlighted
- **Why**, the top reasons in English and Hindi with the words that triggered them
- the **family alert** panel, which appears once, on the first HIGH or CRITICAL

## "Check this caller" (/check)

Open **http://localhost:5173/check** (same `make demo`). The form takes:
- who the caller claimed to be
- the bank or office name
- the number they called from
- optionally, a UPI ID or link
- what they asked for

It returns:
- the risk level
- numbered next steps
- the official helpline found on the institution's own website, with a source link
- government advisories
- public fraud reports, where "no reports found is NOT proof it's safe"

**Requirements:**
- `TINYFISH_API_KEY` in `backend/.env`. Without it, verification shows "unavailable" and the
  advice uses only what was entered.
- **Before the demo:** `cd backend && make prewarm-verify`. This caches real, sourced helplines
  for about 12 institutions and the advisories. If Wi-Fi drops, `/check` serves them labelled
  "cached, fetched at <time>".

The organisation box is a searchable list (English or Hindi, abbreviations like PSB, SBI, PNB,
BoB). For anything not on it, pick **Other**: it isn't searched online, but the advice and 1930
still show. Typed text that matches several entries ("CBI", "Punjab") asks "Did you mean …?".

Try: **Bank → "HDFC Bank" → any 10-digit number → Tell an OTP + Send money → Check now**. You
should see a MISMATCH with HDFC's official number and the source page.

## Scripts

**A single sentence never reaches HIGH, by design.** A call needs pressure across several
utterances. Speak each line as its own sentence, with a pause after it.

### Scam call, English (reaches HIGH)

1. "Hello, I am Inspector Rajesh Sharma from the Mumbai Cyber Crime Branch."
2. "An arrest warrant has been issued in your name for money laundering. Do not disconnect this
   call and do not tell anyone in your family."
3. "To clear your name, transfer two lakh rupees to the RBI safe account right now."

### Scam call, Hinglish (reaches HIGH)

1. "Namaste, main CBI headquarters Delhi se senior officer bol raha hoon."
2. "Aapke naam pe drugs wala parcel pakda gaya hai, aapke khilaf FIR ho gayi hai. Ye baat kisi ko
   mat batana."
3. "Abhi turant apne account ke saare paise is safe account mein transfer kijiye, warna aaj hi
   arrest hoga."

### Benign call (stays LOW, no alert)

1. "Hello Nani, this is Priya from Pune. How are you feeling today?"
2. "We are coming to visit you next Sunday with the kids."
3. "Please take your medicines on time, and call me if you need anything."

**How these were checked (2026-10-19):** each script was rendered with macOS `say` (voice Rishi),
then played through the /demo upload path in Chrome, which is the same WebSocket and pipeline as
the mic.

| Script | Result |
|---|---|
| English scam | HIGH 0.72, alert fired, all 4 pattern stages lit |
| Hinglish scam | HIGH 0.70, alert fired |
| Benign | LOW 0.00, no alert |

The mic path was also tested end to end with a recorded call:
- **Covered:** the browser's resampling, the AudioWorklet, 20 ms frames, the WebSocket and the
  UI.
- **Not covered:** the physical microphone itself, because macOS doesn't give a script-launched
  Chrome mic access.
- **Result:** LOW → HIGH (30 s) → CRITICAL (41 s), with the alert at the first HIGH.

**Real voices will differ from these TTS results.** Rehearse once with your own speakers.

## Known limits to mention honestly

- **A first English sentence can be misheard.** Hindi is the default when language detection is
  unsure, and Whisper then garbles English greetings. If the first line comes out as Hindi
  gibberish, carry on; the next sentences recover.
- **Some scams climb more slowly with the classifier.** It sometimes misses Hinglish threats and
  STT-garbled phrases such as "मैद बताना" for "मत बताना" (don't tell). The lexicon sees them but
  only counts as a fallback. Some scams therefore climb more slowly than in lexicon-only mode
  (see PROGRESS.md).
- **Voice-clone detection (B5) and number verification (B6) aren't built**, and they show as
  unavailable.
- **The demo is a browser call, not a phone line.** Apps can't tap ordinary cellular calls; the
  product is a VoIP safety layer.
