/**
 * Shared PXP tag color constants.
 * ui/frontend/src/constants/tagColors.ts
 *
 * Single source of truth for tag colors — imported by:
 *   - Canvas.tsx     (node fill, edge stroke, arrow markers)
 *   - Week 2: token display chips
 *   - Week 3: alert/deadlock styling
 *
 * Colors satisfy WCAG AA contrast against #0b0f19 background.
 */

export type PXPTag = 'PROPOSE' | 'RATIFY' | 'REVISE' | 'REFUTE' | 'REJECT';

export const TAG_COLORS: Record<PXPTag, string> = {
  PROPOSE: '#8b5cf6',  // Violet   — initial hypothesis
  RATIFY:  '#10b981',  // Emerald  — agreement / consensus (green)
  REVISE:  '#3b82f6',  // Blue     — productive self-correction (blue)
  REFUTE:  '#f59e0b',  // Amber    — rejects position, logs roadblock
  REJECT:  '#ef4444',  // Rose/Red — full conflict
} as const;

/** Background tint (12% opacity) per tag — for chip/badge backgrounds. */
export const TAG_BG_COLORS: Record<PXPTag, string> = {
  PROPOSE: 'rgba(139, 92, 246, 0.12)',
  RATIFY:  'rgba(16, 185, 129, 0.12)',
  REVISE:  'rgba(59, 130, 246, 0.12)',
  REFUTE:  'rgba(245, 158, 11, 0.12)',
  REJECT:  'rgba(239, 68, 68, 0.12)',
} as const;
