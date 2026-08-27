/**
 * Reading money and percentages out of a text field, as strings.
 *
 * **Nothing here converts to a number, and that is the whole point.** Spec §46's
 * amounts travel as decimal strings in both directions, and
 * `services/api/src/tcg_api/routers/economics.py` refuses a JSON number outright
 * — "a JSON number is a binary float in most clients, and money must stay
 * exact". A helper that parsed `"0.10"` into a `number` on the way past would
 * reintroduce exactly the float this contract exists to keep out, so the
 * percentage below is shifted by moving its decimal point rather than by
 * dividing by 100.
 *
 * The grammar is deliberately narrower than the server's. `Money` would accept
 * `"5"`, `" 5.00 "` and arbitrarily many decimal places (it quantises), but a
 * field that silently accepts `"5.005"` and stores `5.01` is a field that lied
 * about what it took. Anything outside the grammar is `invalid` and is answered
 * next to the input, so a mistyped amount never becomes FastAPI's
 * `{"detail": [...]}` — spec §66 has no code for a malformed request and #65
 * declined to invent a ninth.
 */

/**
 * What a field held.
 *
 * `blank` is a state of its own rather than an empty `value`, because for the
 * acquisition cost blank means **unknown** and must reach the wire as `null` —
 * never as `"0.00"`, which is a real acquisition cost (spec §45).
 */
export type ParsedAmount =
  | { readonly state: "blank" }
  | { readonly state: "amount"; readonly value: string }
  | { readonly state: "invalid" };

/** Digits, optionally a point and one or two more. No sign, no separators, no symbol. */
const AMOUNT = /^\d+(?:\.\d{1,2})?$/;

/** The same shape, capped below at 100 by {@link parsePercent}. */
const PERCENT = /^\d{1,3}(?:\.\d{1,2})?$/;

/** A proportion is stored `Numeric(6, 4)`, so a percent gets two decimal places. */
const PROPORTION_PLACES = 4;

/**
 * One money field.
 *
 * A negative is `invalid` rather than clamped: every cost line item and the
 * acquisition cost are non-negative server-side, and clamping would submit a
 * number the user did not type.
 */
export function parseAmount(raw: string): ParsedAmount {
  const trimmed = raw.trim();
  if (trimmed === "") return { state: "blank" };
  return AMOUNT.test(trimmed) ? { state: "amount", value: trimmed } : { state: "invalid" };
}

/**
 * A selling fee typed as a percentage, as the proportion the API wants.
 *
 * `10` becomes `"0.1000"`. The engine refuses `Decimal("10")` by name —
 * "Ten percent is Decimal('0.10'), not Decimal('10')" — so presenting the field
 * as a percentage and shifting it here is what keeps the user's mental model and
 * the wire's contract from disagreeing.
 *
 * The shift is done on the digits: `100 / 100` is exact in binary and
 * `7.35 / 100` is not, and there is no reason for either to be a question.
 */
export function parsePercent(raw: string): ParsedAmount {
  const trimmed = raw.trim();
  if (trimmed === "") return { state: "blank" };
  if (!PERCENT.test(trimmed)) return { state: "invalid" };

  const [whole, fraction = ""] = trimmed.split(".");
  // Two places of percent are four places of proportion, which is the column.
  const digits = `${whole}${fraction.padEnd(2, "0")}`;
  if (Number(digits) > 10_000) return { state: "invalid" };

  const padded = digits.padStart(PROPORTION_PLACES + 1, "0");
  const point = padded.length - PROPORTION_PLACES;
  return { state: "amount", value: `${padded.slice(0, point)}.${padded.slice(point)}` };
}

/**
 * The inverse, for reading a stored `rate` back to a person: `"0.1000"` is
 * `"10"`. Used only for display, and only on what the API just returned.
 */
export function proportionToPercent(proportion: string): string {
  const [whole = "0", fraction = ""] = proportion.trim().split(".");
  const digits = `${whole}${fraction.padEnd(PROPORTION_PLACES, "0").slice(0, PROPORTION_PLACES)}`;
  const padded = digits.padStart(3, "0");
  const point = padded.length - 2;
  const percent = `${padded.slice(0, point).replace(/^0+(?=\d)/, "")}.${padded.slice(point)}`;

  // Trailing zeros are noise on a screen: 10%, not 10.00%. The point is always
  // there to strip against, so `0+$` can never eat a whole number's last zero.
  return percent.replace(/\.?0+$/, "");
}
