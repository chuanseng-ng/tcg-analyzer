/**
 * Which analysis this browser tab is working on.
 *
 * The identifier has to survive `/analyze → /cards → /cards/[cardId] →
 * /identify`, and `sessionStorage` is the whole mechanism: carrying it as a
 * query parameter would mean re-emitting it from the search form, the pager,
 * every result row and the detail page's link, and it would buy nothing. An
 * analysis id is worthless without the HTTP-only `tcg_session` cookie that
 * `POST /analyses` issued — a link pasted into another browser answers 404 —
 * so there is no shareable URL to preserve.
 *
 * It is **not** authorisation and it is not trusted. The API scopes every
 * analysis to the session cookie, so an id typed in by hand is a 404 rather
 * than somebody else's photographs. What this holds is a convenience, and
 * losing it is a state every screen already handles.
 */

/** Per tab, not per browser: two tabs are two analyses, which is what a user means. */
const KEY = "tcg.analysis";

/**
 * `sessionStorage` throws rather than returning null when a browser refuses it
 * — Safari's private mode is the usual one, and a blocked-cookies setting is
 * another. A refusal reads as "no analysis in this tab", which is a state the
 * screens are written for anyway.
 */
function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function rememberAnalysis(analysisId: string): void {
  try {
    storage()?.setItem(KEY, analysisId);
  } catch {
    // A full or refused store is not worth failing an upload over.
  }
}

export function currentAnalysis(): string | null {
  try {
    const stored = storage()?.getItem(KEY) ?? "";
    return stored.trim() === "" ? null : stored;
  } catch {
    return null;
  }
}

export function forgetAnalysis(): void {
  try {
    storage()?.removeItem(KEY);
  } catch {
    // Same reasoning as `rememberAnalysis`.
  }
}
