/*
 * AEDI - IONITY GLOBAL | @ionity/portal | store.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Per-browser conveniences only. SECURITY: secrets (Wi-Fi password, MQTT
 * password, tokens, CA keys) are NEVER written here - the key list below is
 * closed on purpose, so a new preference has to be added deliberately.
 * localStorage can be missing or throw (private mode, blocked storage): every
 * access is guarded and the portal works without it.
 */

const PREFIX = 'ionity-portal:';

export type PrefKey = 'hostApi' | 'site' | 'group' | 'role' | 'screenReader' | 'firmwareUrl';

export const prefs = {
  get(key: PrefKey): string | null {
    try {
      return window.localStorage.getItem(PREFIX + key);
    } catch {
      return null;
    }
  },
  set(key: PrefKey, value: string): void {
    try {
      if (value === '') window.localStorage.removeItem(PREFIX + key);
      else window.localStorage.setItem(PREFIX + key, value);
    } catch {
      // storage unavailable: preference not kept, nothing else changes
    }
  },
};
