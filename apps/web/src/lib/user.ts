// The signed-in user is a local preference for now: the chess.com / lichess username whose games were imported.
const KEY = 'chess-tutor:username';
export function getUsername(): string | null { try { return localStorage.getItem(KEY); } catch { return null; } }
export function setUsername(name: string): void { try { localStorage.setItem(KEY, name); } catch { /* ignore */ } }
