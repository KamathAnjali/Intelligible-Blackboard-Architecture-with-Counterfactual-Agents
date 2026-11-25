import assert from 'node:assert/strict';
import test from 'node:test';
import { getApiJson } from '../src/api.ts';

test('API connection failures explain that the backend must be started', async () => {
  const fetcher = (async () => { throw new TypeError('fetch failed'); }) as typeof fetch;
  await assert.rejects(
    getApiJson('/api/benchmark-results/mscore', fetcher),
    /Start the FastAPI server on port 8000/,
  );
});

test('API errors expose the server detail instead of failing while parsing JSON', async () => {
  const fetcher = (async () => new Response('not found', { status: 404 })) as typeof fetch;
  await assert.rejects(getApiJson('/api/missing', fetcher), /not found/);
});

test('API helper returns successful JSON payloads', async () => {
  const fetcher = (async () => new Response(JSON.stringify({ rows: 200 }), { status: 200 })) as typeof fetch;
  assert.deepEqual(await getApiJson('/api/benchmark-results/mscore', fetcher), { rows: 200 });
});
