/*
 * AEDI - IONITY GLOBAL | @ionity/portal | scrub.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Defence in depth for "secrets never appear on screen or in a saved log".
 *
 * The first two lines of defence are elsewhere: the firmware contract forbids
 * echoing secrets (boards report has_pass / has_mqtt_pass instead) and the
 * provisioning client redacts what it sends before logging it. This scrubber
 * covers what the portal does not control: an older or debug firmware that
 * prints a password, a library log line, text a person types into the console.
 *
 * It masks the exact values typed into the form in THIS session. It is not a
 * DLP engine - it cannot recognise a secret it was never given - and it keeps
 * only references to strings the form state already holds (no extra copies are
 * persisted anywhere).
 */

export const MASK = '•••';

/**
 * Shorter values are not masked: masking "admin" or "1234" would garble normal
 * log text while protecting nothing (WPA2 passwords are 8+ characters anyway).
 */
export const MIN_SECRET_LENGTH = 6;

export class SecretScrubber {
  #secrets: readonly string[] = [];
  #carry = '';

  /** Replace the set of values to mask. */
  set(values: Iterable<string>): void {
    const uniq = new Set<string>();
    for (const v of values) if (v.length >= MIN_SECRET_LENGTH) uniq.add(v);
    // Longest first, so a secret that contains another one is masked whole.
    this.#secrets = [...uniq].sort((a, b) => b.length - a.length);
  }

  get active(): boolean {
    return this.#secrets.length > 0;
  }

  /** Mask secrets in self-contained text (a complete line). */
  line(text: string): string {
    let out = text;
    for (const s of this.#secrets) if (out.includes(s)) out = out.split(s).join(MASK);
    return out;
  }

  /**
   * Streaming use (the terminal receives arbitrary chunks): returns the text that
   * is safe to show now. A tail that could be the start of a secret is held back
   * until the next push() or flush(), so a secret split across two USB reads is
   * still masked.
   */
  push(text: string): string {
    const s = this.line(this.#carry + text);
    const hold = this.#heldTail(s);
    this.#carry = hold > 0 ? s.slice(s.length - hold) : '';
    return hold > 0 ? s.slice(0, s.length - hold) : s;
  }

  /** Text held back by push(); call when the stream goes quiet. */
  flush(): string {
    const t = this.#carry;
    this.#carry = '';
    return t;
  }

  /** Length of the longest suffix of `s` that is a proper prefix of a secret. */
  #heldTail(s: string): number {
    let best = 0;
    for (const secret of this.#secrets) {
      for (let k = Math.min(secret.length - 1, s.length); k > best; k--) {
        if (secret.startsWith(s.slice(s.length - k))) {
          best = k;
          break;
        }
      }
    }
    return best;
  }
}
