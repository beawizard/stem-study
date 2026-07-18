/* Minimal API client */
const Api = (() => {
  async function request(path, { method = "GET", body, token, headers = {} } = {}) {
    const cfg = window.STEM_CONFIG || {};
    const base = (cfg.apiUrl || "").replace(/\/$/, "");
    if (!base) {
      throw new Error("API URL not configured. Set STEM_CONFIG.apiUrl after deploy.");
    }
    const h = { ...headers };
    if (body !== undefined && !(body instanceof FormData)) {
      h["Content-Type"] = "application/json";
    }
    if (token) h.Authorization = `Bearer ${token}`;

    const res = await fetch(`${base}${path}`, {
      method,
      headers: h,
      body: body === undefined ? undefined : (typeof body === "string" ? body : JSON.stringify(body)),
    });

    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }

    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText || "Request failed");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  return {
    health: () => request("/health"),
    me: (token) => request("/me", { token }),
    listTasks: (token) => request("/tasks", { token }),
    createTask: (token, body) => request("/tasks", { method: "POST", body, token }),
    updateTask: (token, id, body) => request(`/tasks/${id}`, { method: "PUT", body, token }),
    deleteTask: (token, id) => request(`/tasks/${id}`, { method: "DELETE", token }),
    listSubjects: (token) => request("/subjects", { token }),
    listLevels: (token, subjectId) => request(`/subjects/${subjectId}/levels`, { token }),
    startSession: (token, body) => request("/study/sessions", { method: "POST", body, token }),
    submitAnswer: (token, sessionId, body) =>
      request(`/study/sessions/${sessionId}/answers`, { method: "POST", body, token }),
    getProgress: (token, subjectId) =>
      request(`/study/progress${subjectId ? `?subject_id=${encodeURIComponent(subjectId)}` : ""}`, { token }),
    insights: (token) => request("/insights", { token }),
    submitPayment: (token, body) => request("/payments", { method: "POST", body, token }),
    listPayments: (token) => request("/payments", { token }),
  };
})();
