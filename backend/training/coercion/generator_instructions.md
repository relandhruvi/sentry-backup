# Coercion classifier: data generation instructions (given verbatim to the generator)

You are writing training data for a classifier that reads ONE utterance spoken by a phone caller
(transcribed speech) to an older adult in India and labels the persuasion tactics the caller is
using. You have not seen and must not look at any existing detector, word list, test set or code.
Write from your own knowledge of how real Indian phone scams, real institutions and real families
talk.

## Labels (multi-label)

- **AUTHORITY**: the caller asserts or leans on official, institutional or expert standing to get
  compliance: police, CBI, ED, customs, narcotics, cyber cell, court, judge, RBI, TRAI, income tax,
  a bank's "head office" or "fraud department", a telecom or electricity department, a courier's
  "legal team", Microsoft or "technical support". Also badge or employee numbers, "senior officer",
  "this is a government matter", or citing a case or warrant number as proof of who they are.
- **THREAT**: the caller states or implies harm if the listener doesn't comply, or accuses them so
  that they feel in danger: arrest, warrant, FIR, jail, court case, money-laundering or drugs
  accusation, account frozen or blocked, SIM or electricity cut, pension stopped, penalty or fine,
  a relative hurt or arrested, a private video leaked, "your name is in a case".
- **URGENCY**: artificial time pressure: now, immediately, within 2 hours, before 5 pm, today
  only, last warning, no time to think, "do it while I'm on the line".
- **SECRECY**: the caller tells the listener to hide the matter: don't tell family, children,
  neighbours, bank staff or police; "this is confidential" or "a national secret"; "if the bank
  asks, say it's for a relative"; delete the messages.
- **ISOLATION**: the caller keeps the listener cut off and under their control: stay on the call,
  don't disconnect, keep the camera on, go into a room alone and close the door, don't take other
  calls, switch off the other phone, don't go to the branch or police station, "we are monitoring
  you".
- **PAYMENT**: the caller asks the listener to move money or value: transfer, UPI, scan a QR
  code, deposit into a "safe", "RBI" or "verification" account, pay a fee, fine, tax, customs duty
  or "refundable security", buy gift cards or crypto, withdraw cash and hand it over, approve a
  collect request.
- **CREDENTIAL**: the caller asks for secret access data or something that lets them take over an
  account or device: OTP, UPI PIN, ATM PIN, CVV, full card number and expiry, net-banking
  password, Aadhaar or PAN together with an OTP, installing AnyDesk, TeamViewer or a "support" app,
  screen sharing, clicking a link and entering bank details.
- **BENIGN**: none of the tactics above is being used on the listener. This covers genuine calls
  (banks, hospitals, police, schools, delivery), real family news, protective advice ("never share
  your OTP with anyone") and ordinary chat. **BENIGN is never combined with another label.**

## Labeling rules

1. Label only what the caller is doing in the target `text`. Context lines are earlier turns and
   only help disambiguate.
2. A tactic counts only when it is used as pressure on the listener. Scary or official words used
   innocently are BENIGN: a real hospital saying "please come immediately, your father is in the
   ICU", a real bank saying "we blocked a suspicious transaction, please don't share your OTP", a
   real policeman doing passport verification.
3. **Being asked to join or stay on a video call is NOT CREDENTIAL.** Label it SECRECY or
   ISOLATION depending on the wording: "stay on video, don't leave the room, keep the camera on"
   is ISOLATION; "come on video call so that nobody else hears, this is confidential" is SECRECY.
   Both can apply.
4. A delivery agent at the door asking for the delivery OTP for a parcel the listener ordered is
   BENIGN. A caller asking for an OTP "to cancel", "to verify", "to refund" or "to stop a parcel"
   is CREDENTIAL.
5. A scam utterance with no tactic in it (for example the opening "Hello, am I speaking to Mrs
   Iyer?") is labeled BENIGN even though the call is a scam; its `category` stays `scam`.
6. Multi-label is normal: "Transfer it now or you'll be arrested" is PAYMENT + URGENCY + THREAT.

## Output format

JSON Lines, one object per line, UTF-8, no comments, no trailing commas:

```
{"lang": "en|hi|hinglish", "category": "scam|hard_negative|chat", "scenario": "<short tag>", "context": ["caller: ...", "elder: ..."], "text": "<the caller utterance>", "labels": ["THREAT", "URGENCY"], "spoken_noise": false}
```

- `context`: 0 to 3 earlier lines, each prefixed `caller: ` or `elder: `, oldest first, in the same
  language style as `text`. Vary the count; about a third of rows have no context.
- `text`: one caller utterance, 4–40 words, as it would be spoken on a phone.
- `scenario`: a short tag such as `digital_arrest_cbi`, `bank_kyc_block`, `relative_accident`,
  `real_bank_fraud_alert`, `hospital_emergency`, `delivery_otp`, `school_call`, `chat_weather`.
- `spoken_noise`: true for roughly one row in three, where the text has spoken-style noise:
  fillers (umm, uh, haan, matlab, dekhiye, अरे, मतलब), repeated words, typos, half sentences,
  self-corrections, run-ons. The other rows are clean.

## Language rules

- `en`: Indian English.
- `hi`: Hindi written **only in Devanagari**. Write English loanwords in Devanagari too (ओटीपी,
  अकाउंट, वारंट, पुलिस, वीडियो कॉल). Digits may be numerals. No Latin letters at all.
- `hinglish`: Hindi–English code-mixing written **only in Latin script** (romanized), the way
  people type on WhatsApp: "aapka account block ho jayega, abhi OTP batao". Romanized Hindi is
  always `hinglish`, never `hi`.

## Variety

Vary the persona and region of the caller and listener (names, cities, accents shown through
word choice), the listener's age (60–90), the scam type and the **stage** of the scam (opening,
building trust, accusation, pressure, isolation, the money or credential ask, follow-up after
payment). Include subtle, polite and indirect scams ("sir, just for your safety, kindly...") as well
as blunt ones. Don't reuse a sentence frame within a batch; a batch with 100 near-identical
sentences is useless. Use realistic but invented names, numbers, UPI IDs and case numbers.
