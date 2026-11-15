// The only place that talks to the server. Same-origin credentials on every call; every mutation also sends
// X-Sickbay: 1 (the server's guard insists on it) and a JSON body, or multipart for an upload. A response that is not
// 2xx, or a failed request, resolves to { error: "<a message a person can read>" } for the caller to show; a 204
// resolves to {}.

async function request(method, path, init) {
  let res;
  try {
    res = await fetch(path, { method, credentials: "same-origin", cache: "no-store", ...init });
  } catch {
    return { error: "Cannot reach the server." };
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null; // 204, or an HTML sign-in page from Cloudflare
  }
  if (res.ok) return data ?? (res.status === 204 ? {} : { error: "Unexpected response. Reload the page." });
  return { error: data?.error ?? `Request failed (${res.status}).` };
}

export const get = path => request("GET", path, { headers: { Accept: "application/json" } });

export const send = (method, path, body) => request(method, path, {
  headers: { Accept: "application/json", "X-Sickbay": "1", "Content-Type": "application/json" },
  body: JSON.stringify(body ?? {}),
});

// multipart/form-data with the file in the field `file`; the browser sets the boundary, so no Content-Type here.
export function upload(path, file) {
  const form = new FormData();
  form.append("file", file);
  return request("POST", path, { headers: { Accept: "application/json", "X-Sickbay": "1" }, body: form });
}
