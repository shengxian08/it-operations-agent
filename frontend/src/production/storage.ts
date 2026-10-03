const PREFIX = "it-workspace:v1:";
export function readStored<T>(key: string): T | null { try { const value = sessionStorage.getItem(PREFIX + key); return value ? JSON.parse(value) as T : null; } catch { return null; } }
export function storeValue(key: string, value: unknown) { try { sessionStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* The current page still retains retry state when browser storage is disabled. */ } }
export function removeStored(key: string) { try { sessionStorage.removeItem(PREFIX + key); } catch { /* Storage may be disabled. */ } }
export function clearPrivateStorage() { try { for (const key of Object.keys(sessionStorage)) if (key.startsWith(PREFIX)) sessionStorage.removeItem(key); } catch { /* Storage may be disabled. */ } }
export function confirmationKey(userId: string, draftId: string, version: number): string { const storageKey = `${userId}:confirm:${draftId}:${version}`; const previous = readStored<string>(storageKey); if (previous) return previous; const key = crypto.randomUUID(); storeValue(storageKey, key); return key; }
