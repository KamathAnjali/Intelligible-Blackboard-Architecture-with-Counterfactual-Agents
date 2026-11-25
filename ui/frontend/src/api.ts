/** Fetch JSON from the FastAPI service through Vite's /api proxy. */
export async function getApiJson<T>(path: string, fetcher: typeof fetch = fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(path);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`Cannot reach the project API for ${path}. Start the FastAPI server on port 8000, then retry. (${message})`);
  }

  if (!response.ok) {
    const body = await response.text();
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (parsed.detail !== undefined) detail = String(parsed.detail);
    } catch {
      // Keep plain-text proxy/server errors readable.
    }
    if (response.status >= 500 && /proxy|ECONNREFUSED|connect/i.test(detail)) {
      throw new Error(`The frontend could not reach FastAPI on port 8000 while loading ${path}. Start the backend and retry.`);
    }
    throw new Error(detail || `API request failed (${response.status})`);
  }

  return response.json() as Promise<T>;
}
