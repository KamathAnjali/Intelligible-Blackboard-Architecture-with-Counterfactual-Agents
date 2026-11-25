/** Clean a known malformed JSON tail for presentation without changing saved evidence. */
export function cleanModelTextForDisplay(value: unknown): string {
  const text = value == null ? '' : String(value);
  return text.replace(/\s*\}?\s*'\}\s*"?\s*$/u, '').trim();
}
