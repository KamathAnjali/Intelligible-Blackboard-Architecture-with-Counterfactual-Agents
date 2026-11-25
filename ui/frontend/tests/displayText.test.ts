import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanModelTextForDisplay } from '../src/displayText.ts';

test('removes malformed serialized suffix from display text only', () => {
  assert.equal(cleanModelTextForDisplay("SYNTHETIC explanation。'}\""), 'SYNTHETIC explanation。');
  assert.equal(cleanModelTextForDisplay("SYNTHETIC explanation。}'}\""), 'SYNTHETIC explanation。');
});

test('leaves ordinary text and non-string inputs safe', () => {
  assert.equal(cleanModelTextForDisplay('SYNTHETIC ordinary explanation.'), 'SYNTHETIC ordinary explanation.');
  assert.equal(cleanModelTextForDisplay(null), '');
});
