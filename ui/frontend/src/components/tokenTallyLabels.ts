export interface TokenTallyLabelInput {
  providerTokens: number;
  estimatedTokens: number;
  estimatedSharePct: number;
  measurementLabel?: string;
}

export function tokenTallyLabels(input: TokenTallyLabelInput) {
  const parts: string[] = [];
  if (input.providerTokens > 0) parts.push(`${input.providerTokens.toLocaleString()} provider tokens`);
  if (input.estimatedTokens > 0) parts.push(`${input.estimatedTokens.toLocaleString()} estimated`);
  return {
    buttonText: parts.join(' + ') || 'Token usage unavailable',
    estimatedShare: `${input.estimatedSharePct.toFixed(0)}% estimated`,
    detailLabel: input.measurementLabel ?? (input.providerTokens > 0
      ? 'provider-reported model-call usage'
      : input.estimatedTokens > 0 ? 'estimated, entry text only' : 'usage unavailable'),
  };
}
