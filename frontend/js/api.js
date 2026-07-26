/* Minimal API client */
const Api = (() => {
  async function request(path, { method = "GET", body, token, headers = {}, rawBody = false } = {}) {
    const cfg = window.STEM_CONFIG || {};
    const base = (cfg.apiUrl || "").replace(/\/$/, "");
    if (!base) {
      throw new Error("API URL not configured. Set STEM_CONFIG.apiUrl after deploy.");
    }
    const h = { ...headers };
    if (body !== undefined && !(body instanceof FormData) && !h["Content-Type"] && !rawBody) {
      h["Content-Type"] = "application/json";
    }
    if (token) h.Authorization = `Bearer ${token}`;

    let payload;
    if (body === undefined) {
      payload = undefined;
    } else if (rawBody || typeof body === "string") {
      payload = body;
    } else {
      payload = JSON.stringify(body);
    }

    const res = await fetch(`${base}${path}`, {
      method,
      headers: h,
      body: payload,
    });

    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }

    if (!res.ok) {
      const detail =
        (data && (data.error || data.message)) ||
        (data && data.raw && String(data.raw).slice(0, 200)) ||
        res.statusText ||
        "";
      const msg = detail
        ? `HTTP ${res.status}: ${detail}`
        : `HTTP ${res.status}: Request failed`;
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  return {
    health: () => request("/health"),
    me: (token) => request("/me", { token }),
    updateMe: (token, body) =>
      request("/me", { method: "PATCH", body, token }),
    /** Public school catalog (token optional — public GET /schools). */
    listSchools: (token) => request("/schools", token ? { token } : {}),
    createSchool: (token, body) =>
      request("/schools", { method: "POST", body, token }),
    updateSchool: (token, schoolId, body) =>
      request(`/schools/${encodeURIComponent(schoolId)}`, {
        method: "PUT",
        body,
        token,
      }),
    deleteSchool: (token, schoolId) =>
      request(`/schools/${encodeURIComponent(schoolId)}`, {
        method: "DELETE",
        token,
      }),
    listTasks: (token) => request("/tasks", { token }),
    createTask: (token, body) => request("/tasks", { method: "POST", body, token }),
    updateTask: (token, id, body) => request(`/tasks/${id}`, { method: "PUT", body, token }),
    deleteTask: (token, id) => request(`/tasks/${id}`, { method: "DELETE", token }),
    listSubjects: (token) => request("/subjects", { token }),
    listLevels: (token, subjectId) => request(`/subjects/${subjectId}/levels`, { token }),
    listQuestions: (token, subjectId, levelId, includeAnswers = false) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}/questions` +
          (includeAnswers ? "?include_answers=true" : ""),
        { token }
      ),
    createSubject: (token, body) => request("/subjects", { method: "POST", body, token }),
    updateSubject: (token, subjectId, body) =>
      request(`/subjects/${encodeURIComponent(subjectId)}`, {
        method: "PUT",
        body,
        token,
      }),
    createLevel: (token, subjectId, body) =>
      request(`/subjects/${encodeURIComponent(subjectId)}/levels`, {
        method: "POST",
        body,
        token,
      }),
    updateLevel: (token, subjectId, levelId, body) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}`,
        { method: "PUT", body, token }
      ),
    deleteLevel: (token, subjectId, levelId) => {
      if (!subjectId || !levelId) {
        return Promise.reject(new Error("subjectId and levelId are required to delete a level"));
      }
      return request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}`,
        { method: "DELETE", token }
      );
    },
    uploadQuestionsCsv: (token, subjectId, levelId, csvText, replace = false) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}/questions` +
          (replace ? "?replace=true" : ""),
        {
          method: "POST",
          body: csvText,
          token,
          rawBody: true,
          headers: { "Content-Type": "text/csv" },
        }
      ),
    clearQuestions: (token, subjectId, levelId) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}/questions`,
        { method: "DELETE", token }
      ),
    updateQuestion: (token, subjectId, levelId, questionId, body) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}/questions/${encodeURIComponent(questionId)}`,
        { method: "PUT", body, token }
      ),
    deleteQuestion: (token, subjectId, levelId, questionId) =>
      request(
        `/subjects/${encodeURIComponent(subjectId)}/levels/${encodeURIComponent(levelId)}/questions/${encodeURIComponent(questionId)}`,
        { method: "DELETE", token }
      ),
    adminSeed: (token) => request("/admin/seed", { method: "POST", token }),
    startSession: (token, body) => request("/study/sessions", { method: "POST", body, token }),
    submitAnswer: (token, sessionId, body) =>
      request(`/study/sessions/${sessionId}/answers`, { method: "POST", body, token }),
    completeSession: (token, sessionId, body) =>
      request(`/study/sessions/${sessionId}/complete`, { method: "POST", body, token }),
    /** Placement assessment: preview question counts per level */
    previewAssessment: (token, subjectId) =>
      request(
        `/study/assessment/preview?subject_id=${encodeURIComponent(subjectId)}`,
        { token }
      ),
    /** Start assessment (10 balanced questions × each level) */
    startAssessment: (token, body) =>
      request("/study/assessment", { method: "POST", body, token }),
    completeAssessment: (token, assessmentId, body) =>
      request(`/study/assessment/${encodeURIComponent(assessmentId)}/complete`, {
        method: "POST",
        body,
        token,
      }),
    getProgress: (token, subjectId) =>
      request(
        `/study/progress${subjectId ? `?subject_id=${encodeURIComponent(subjectId)}` : ""}`,
        { token }
      ),
    insights: (token) => request("/insights", { token }),
    submitPayment: (token, body) => request("/payments", { method: "POST", body, token }),
    listPayments: (token) => request("/payments", { token }),
  };
})();
