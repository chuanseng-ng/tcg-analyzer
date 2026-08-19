/**
 * How a card's facts are worded, shared by the results list and the detail view.
 *
 * The two must agree. There is no card image — `docs/adr/0004-the-canonical-card-catalog-source.md`
 * imports none, because MIT covers TCGdex's compilation and not The Pokémon
 * Company's artwork — so set, number, variant and rarity are carrying the
 * disambiguation a picture would otherwise do. A user who reads `reverse-holo`
 * in a list and something else on the card's own page cannot tell whether they
 * picked the right row, and picking the wrong row is a wrong valuation.
 */

/**
 * Printing variants are economically different cards: holo, reverse holo and
 * 1st edition do not sell for the same money.
 *
 * A null variant is said out loud rather than left blank. The catalog holds
 * several — every Japanese card seeded so far, and Base Set's Energy Retrieval
 * — and a blank cell next to `reverse-holo` reads as a rendering fault. It is
 * deliberately not called "standard": the catalog does not record that, and
 * inventing it would assert something about the printing that nobody checked.
 */
export function variantLabel(variant: string | null): string {
  return variant ?? "variant not recorded";
}

/** Same reasoning as {@link variantLabel} — absence is stated, never blank. */
export function rarityLabel(rarity: string | null): string {
  return rarity ?? "rarity not recorded";
}

/**
 * V1 ships English and Japanese, but `language` is a validated field rather
 * than an enum — `packages/domain` accepts a code neither of them names — so an
 * unrecognised code is shown as itself instead of being hidden or guessed at.
 */
const LANGUAGE_NAMES: Readonly<Record<string, string>> = {
  en: "English",
  ja: "Japanese",
};

export function languageLabel(language: string): string {
  return LANGUAGE_NAMES[language] ?? language;
}

/** The catalog's own release date, as printed by the set. */
export function releaseDateLabel(releaseDate: string | null): string {
  return releaseDate ?? "release date not recorded";
}

/** The canonical detail route for one card. */
export function cardHref(cardId: string): string {
  return `/cards/${encodeURIComponent(cardId)}`;
}

/**
 * A metadata value as text.
 *
 * `metadata` is an open bag on both the card and its set, so a value may be
 * anything JSON allows. Strings and numbers are shown plainly; anything
 * structured is serialized rather than rendered as `[object Object]`.
 */
export function metadataValueLabel(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}
