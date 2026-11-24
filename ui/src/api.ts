export const token =
  document.querySelector<HTMLMetaElement>('meta[name="mcb-token"]')?.content ?? "";

export async function api<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: {
      "X-UI-Token": token,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || data.detail || `Request failed (${response.status})`);
  return data as T;
}
