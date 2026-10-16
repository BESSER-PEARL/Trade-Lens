// Codes or plain names (contract 1.4): a reader whose profile hides codes (the citizen, the journalist:
// answer.show_codes false, "codes" in hide) reads "Electrical machinery", not "85 Electrical machinery",
// and "Lines inside" rather than "CN8 lines of 851713". The persona comes from spec.context.persona and
// its profile from contract/personas (the copy in ./personas). Display only: no figure changes.

import { profileOf } from "./personas";
import { usePageContext } from "./ViewStore";

/** Whether this reader sees HS/CN8 codes next to the names (every reader without a profile does). */
export function codesShown(persona: string | undefined | null): boolean {
  const p = profileOf(persona);
  if (!p) return true;
  const hidden = (p.hide || []).includes("codes");
  return (p.answer as any)?.show_codes !== false && !hidden;
}

/** The page's reader sees codes: read from the page context's persona. */
export function useShowCodes(): boolean {
  return codesShown(usePageContext().persona);
}

/**
 * A label without codes: "CN8 lines of 851713" -> "Lines inside", "HS4 headings of 85" -> "Headings inside",
 * "Smartphones (CN8 85171300)" -> "Smartphones", "85 Electrical machinery" -> "Electrical machinery".
 * Years (1999, 2025) and figures stay.
 */
export function withoutCodes(text: string | undefined | null): string {
  if (!text) return "";
  const out = String(text)
    .replace(/\s*\((?:HS\s?\d?|CN\s?8?|chapter)?\s*\d{2,8}(?:\s\d{2}){0,3}\)/gi, "")
    .replace(/\b(?:of|in|inside)\s+(?:HS\s?\d?\s*|CN\s?8?\s*|chapter\s+)?\d{2,8}\b/gi, "inside")
    .replace(/\b(?:HS\s?[246]?|CN\s?8?)\s*\d{2,8}(?:\s\d{2}){0,3}\b\s*/gi, "")
    .replace(/\b(?:HS\s?[246]?|CN8)\b\s*/gi, "")
    .replace(/^\d{2,8}\s+(?=\D)/, "")
    .replace(/(^|\s)(?!(?:19|20)\d\d\b)\d{6,8}\b/g, "$1")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([,.;:])/g, "$1")
    .trim();
  return out ? out.charAt(0).toUpperCase() + out.slice(1) : out;
}
