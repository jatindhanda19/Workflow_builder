const API_URL = (
  import.meta.env.VITE_API_URL || "http://127.0.0.1:8001"
).replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function apiRequest(path, options = {}) {
  const token = sessionStorage.getItem("wb_access_token");

  const headers = new Headers(options.headers || {});

  if (options.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response;

  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers,
    });
  } catch {
    throw new Error(
      `Cannot connect to the backend at ${API_URL}. Check that FastAPI is running.`
    );
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  let data = null;

  if (contentType.includes("application/json")) {
    data = await response.json();
  } else {
    const text = await response.text();
    data = text ? { detail: text } : null;
  }

  if (!response.ok) {
    if (response.status === 401 && token) {
      sessionStorage.removeItem("wb_access_token");
      sessionStorage.removeItem("wb_user");
      sessionStorage.removeItem("wb_session_id");
      sessionStorage.removeItem("wb_messages");
      window.dispatchEvent(new Event("wb-auth-expired"));
    }

    const detail = data?.detail;

    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg || "Invalid input").join(", ")
          : `Request failed with status ${response.status}`;

    throw new ApiError(message, response.status);
  }

  return data;
}

export function saveAuth(token, user) {
  sessionStorage.setItem("wb_access_token", token);
  sessionStorage.setItem("wb_user", JSON.stringify(user));
}

export function clearAuth() {
  sessionStorage.removeItem("wb_access_token");
  sessionStorage.removeItem("wb_user");
  sessionStorage.removeItem("wb_session_id");
  sessionStorage.removeItem("wb_messages");
}

export function getSavedUser() {
  try {
    const value = sessionStorage.getItem("wb_user");
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

export function getSavedMessages() {
  try {
    const value = sessionStorage.getItem("wb_messages");
    const parsed = value ? JSON.parse(value) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveMessages(messages) {
  sessionStorage.setItem("wb_messages", JSON.stringify(messages));
}
