import type { FormEvent } from "react";

/**
 * Local-only waitlist confirmation. Connect a real backend or email service here later.
 * This prototype validates the address in the browser and does not send or store it.
 */
export function submitWaitlistEmail(event: FormEvent<HTMLFormElement>, onAccepted: () => void) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  onAccepted();
}
