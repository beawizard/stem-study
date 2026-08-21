/* MElon Basic Education SPA – mobile-first */
const App = (() => {
  const main = () => document.getElementById("main");
  const nav = () => document.getElementById("nav");
  let state = {
    route: "home",
    profile: null,
    session: null,
    // Study quiz (client-side answering)
    // phase: countdown | answering | results
    studyPhase: null,
    qIndex: 0,
    clientAnswers: {}, // question_id -> answer string
    // Active-time only (paused during question transitions / loading)
    timerAccumulatedMs: 0,
    timerRunningSince: null,
    timerInterval: null,
    countdownValue: 3,
    countdownTimer: null,
    studyResults: null,
    studyTransitioning: false,
    answerStartedAt: 0,
    // Admin content management (no GCash)
    adminSubjectId: null,
    adminLevelId: null,
    // Study page subject pickers (Category + Topic, same model as Admin)
    studyCategory: null,
    studySubjectId: null,
    studyFocusLevelId: null, // deep-link from Insights level link
    // Placement assessment (Home → Assessment only)
    assessmentCategory: null,
    /** Base topic key, e.g. "Arithmetic (Addition)" — not a per-level subject. */
    assessmentBaseTopic: null,
    assessmentSubjectId: null, // representative subject_id in the base-topic group
    assessmentPreview: null,
    /** Cached Home leaderboard rows for CSV download */
    leaderboardEntries: [],
    /** Mastery hub / create wizard / study-within-collection */
    masteryView: "hub", // hub | create | edit | study
    masteryCreateStep: 1,
    masteryEditId: null, // when editing an existing collection
    masteryDraft: {
      name: "",
      category: "",
      topics: [],
      subject_ids: [],
      start_date: "",
      end_date: "",
      shared: false,
    },
    masteryCollections: [],
    masteryActive: null, // selected published collection
    masterySubjectId: null, // topic (subject) within active collection
  };

  /**
   * In-memory client cache for Study (and Assessment catalog).
   * Subjects TTL 5 min; landing TTL 2 min. Invalidated after completing a set/assessment.
   */
  const StudyCache = (() => {
    const SUBJECTS_TTL_MS = 5 * 60 * 1000;
    const LANDING_TTL_MS = 2 * 60 * 1000;
    let subjectsEntry = null; // { t, data: subjects[] }
    const landingMap = new Map(); // subjectId -> { t, data }

    function getSubjects() {
      if (!subjectsEntry) return null;
      if (Date.now() - subjectsEntry.t > SUBJECTS_TTL_MS) {
        subjectsEntry = null;
        return null;
      }
      return subjectsEntry.data;
    }

    function setSubjects(list) {
      subjectsEntry = { t: Date.now(), data: Array.isArray(list) ? list : [] };
    }

    function getLanding(subjectId) {
      if (!subjectId) return null;
      const e = landingMap.get(subjectId);
      if (!e) return null;
      if (Date.now() - e.t > LANDING_TTL_MS) {
        landingMap.delete(subjectId);
        return null;
      }
      return e.data;
    }

    function setLanding(subjectId, data) {
      if (!subjectId || !data) return;
      landingMap.set(subjectId, { t: Date.now(), data });
      // Sibling subjects share progress_rows; keep a copy under each subject_id
      // only for the selected landing shape (levels differ per subject) — so we
      // only store under the requested id.
    }

    /** Drop all landing entries (progress changed). */
    function invalidateLanding() {
      landingMap.clear();
    }

    /** Drop subjects + landings (admin content change / logout). */
    function invalidateAll() {
      subjectsEntry = null;
      landingMap.clear();
    }

    /**
     * Fetch subjects + optional landing with cache + bootstrap batching.
     * Returns { subjects, landing }.
     */
    async function loadStudyData(tok, subjectId) {
      const cachedSubjects = getSubjects();
      const cachedLanding = subjectId ? getLanding(subjectId) : null;

      // Full cache hit
      if (cachedSubjects && (!subjectId || cachedLanding)) {
        return { subjects: cachedSubjects, landing: cachedLanding || null };
      }

      // Subjects hit — only need landing for this topic
      if (cachedSubjects && subjectId && !cachedLanding) {
        try {
          const landing = await Api.studyLanding(tok, subjectId);
          setLanding(subjectId, landing);
          return { subjects: cachedSubjects, landing };
        } catch (e) {
          // fall through to bootstrap / legacy
        }
      }

      // One round-trip: catalog + landing
      try {
        const boot = await Api.studyBootstrap(tok, subjectId || undefined);
        const subjects = boot.subjects || [];
        setSubjects(subjects);
        let landing = boot.landing || null;
        if (landing && landing.subject_id) {
          setLanding(landing.subject_id, landing);
        }
        // If client asked for a different subject than bootstrap defaulted to
        if (
          subjectId &&
          (!landing || landing.subject_id !== subjectId)
        ) {
          const hit = getLanding(subjectId);
          if (hit) {
            landing = hit;
          } else {
            try {
              landing = await Api.studyLanding(tok, subjectId);
              setLanding(subjectId, landing);
            } catch {
              /* leave landing null */
            }
          }
        }
        return { subjects, landing };
      } catch {
        // Legacy: listSubjects + studyLanding separately
        const res = await Api.listSubjects(tok);
        const subjects = res.subjects || [];
        setSubjects(subjects);
        let landing = null;
        if (subjectId) {
          try {
            landing = await Api.studyLanding(tok, subjectId);
            setLanding(subjectId, landing);
          } catch {
            landing = null;
          }
        }
        return { subjects, landing };
      }
    }

    async function loadSubjects(tok) {
      const hit = getSubjects();
      if (hit) return hit;
      const res = await Api.listSubjects(tok);
      const subjects = res.subjects || [];
      setSubjects(subjects);
      return subjects;
    }

    return {
      getSubjects,
      setSubjects,
      getLanding,
      setLanding,
      invalidateLanding,
      invalidateAll,
      loadStudyData,
      loadSubjects,
    };
  })();

  /**
   * I5: Insights payload cache (TTL 90s). Invalidated with progress changes.
   */
  const InsightsCache = (() => {
    const TTL_MS = 90 * 1000;
    let entry = null; // { t, data, key }

    function cacheKey(subjectId) {
      return subjectId ? String(subjectId) : "__all__";
    }

    function get(subjectId) {
      if (!entry) return null;
      if (entry.key !== cacheKey(subjectId)) return null;
      if (Date.now() - entry.t > TTL_MS) {
        entry = null;
        return null;
      }
      return entry.data;
    }

    function set(data, subjectId) {
      if (!data) {
        entry = null;
        return;
      }
      entry = { t: Date.now(), data, key: cacheKey(subjectId) };
    }

    function invalidate() {
      entry = null;
    }

    return { get, set, invalidate };
  })();

  function clearStudyTimers() {
    if (state.timerInterval) {
      clearInterval(state.timerInterval);
      state.timerInterval = null;
    }
    if (state.countdownTimer) {
      clearInterval(state.countdownTimer);
      state.countdownTimer = null;
    }
  }

  /** Keep the Next/Submit bar above the mobile keyboard (Safari autofill + keyboard). */
  function bindStudyKeyboardAvoidance() {
    const root = document.documentElement;
    const apply = () => {
      const vv = window.visualViewport;
      if (!vv) {
        root.style.setProperty("--vv-keyboard", "0px");
        return;
      }
      // Distance from layout bottom to visible viewport bottom
      const keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      root.style.setProperty("--vv-keyboard", `${Math.round(keyboard)}px`);
    };

    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", apply);
      window.visualViewport.addEventListener("scroll", apply);
    }
    window.addEventListener("resize", apply);
    apply();
    // store cleanup flag so we don't stack forever — re-apply is idempotent
    state._vvBound = true;
  }

  function setStudyModeUi(active) {
    document.body.classList.toggle("study-mode", Boolean(active));
    const mainEl = main();
    if (mainEl) mainEl.classList.toggle("study-mode-main", Boolean(active));
  }

  function setAdminModeUi(active) {
    document.body.classList.toggle("admin-mode", Boolean(active));
    const mainEl = main();
    if (mainEl) mainEl.classList.toggle("admin-mode-main", Boolean(active));
  }

  function getStudyElapsedMs() {
    let ms = state.timerAccumulatedMs || 0;
    if (state.timerRunningSince != null) {
      ms += Date.now() - state.timerRunningSince;
    }
    return Math.max(0, ms);
  }

  function pauseStudyTimer() {
    if (state.timerRunningSince != null) {
      state.timerAccumulatedMs += Date.now() - state.timerRunningSince;
      state.timerRunningSince = null;
    }
    paintStudyTimer();
  }

  function resumeStudyTimer() {
    if (state.studyPhase !== "answering") return;
    if (state.timerRunningSince == null) {
      state.timerRunningSince = Date.now();
    }
    if (!state.timerInterval) {
      state.timerInterval = setInterval(() => paintStudyTimer(), 200);
    }
    paintStudyTimer();
  }

  function paintStudyTimer() {
    const t = document.getElementById("study-timer");
    if (t) t.textContent = `⏱ ${formatDuration(getStudyElapsedMs())}`;
  }

  function resetStudyState() {
    clearStudyTimers();
    state.session = null;
    state.studyPhase = null;
    state.qIndex = 0;
    state.clientAnswers = {};
    state.timerAccumulatedMs = 0;
    state.timerRunningSince = null;
    state.countdownValue = 3;
    state.studyResults = null;
    state.studyTransitioning = false;
    setStudyModeUi(false);
    document.documentElement.style.setProperty("--vv-keyboard", "0px");
  }

  function formatDuration(ms) {
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function toast(msg, isErr = false) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.toggle("err", isErr);
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 3200);
  }

  function token() {
    return Auth.getIdToken();
  }

  function setNavVisible(show) {
    nav().classList.toggle("hidden", !show);
    const adminBtn = document.getElementById("nav-admin");
    if (adminBtn) {
      const isAdmin = show && Auth.isAdmin();
      adminBtn.classList.toggle("hidden", !isAdmin);
    }
    if (show) updateNavProfileAvatar();
    else closeProfileMenu();
  }

  function navigate(route) {
    if (route === "admin" && !Auth.isAdmin()) {
      toast("Admin access required.", true);
      route = "home";
    }
    // Assessment sessions must not leak into Study; clear when leaving Assessment
    if (
      state.session &&
      state.session.is_assessment &&
      route !== "assessment"
    ) {
      resetStudyState();
    }
    // Starting Assessment should not resume a normal study quiz
    if (
      route === "assessment" &&
      state.session &&
      !state.session.is_assessment
    ) {
      resetStudyState();
    }
    state.route = route;
    closeProfileMenu();
    nav().querySelectorAll("button[data-route]").forEach((b) => {
      b.classList.toggle("active", b.dataset.route === route);
    });
    // Highlight profile button when on account/profile pages
    const profileBtn = document.getElementById("btn-profile-menu");
    if (profileBtn) {
      profileBtn.classList.toggle(
        "is-active-route",
        route === "account" ||
          route === "profile" ||
          route === "mastery" ||
          route === "facebook"
      );
    }
    render();
  }

  function learnerInitials(p) {
    const name = learnerDisplayName(p || {});
    if (!name) return "";
    const parts = name.replace(/@.*$/, "").split(/[\s._-]+/).filter(Boolean);
    if (!parts.length) return name.slice(0, 1).toUpperCase();
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  /** Nav avatar: photo (future), else initials, else user icon placeholder. */
  function updateNavProfileAvatar() {
    const img = document.getElementById("nav-profile-img");
    const placeholder = document.getElementById("nav-profile-placeholder");
    const initialsEl = document.getElementById("nav-profile-initials");
    const btn = document.getElementById("btn-profile-menu");
    if (!img || !placeholder || !initialsEl) return;

    const p = state.profile || {};
    const photo = String(p.avatar_url || p.photo_url || "").trim();
    const initials = learnerInitials(p);
    const label = learnerDisplayName(p) || "Profile menu";

    if (btn) {
      btn.title = label;
      btn.setAttribute("aria-label", `Profile menu for ${label}`);
    }

    if (photo) {
      img.src = photo;
      img.alt = label;
      img.classList.remove("hidden");
      placeholder.classList.add("hidden");
      initialsEl.classList.add("hidden");
      img.onerror = () => {
        img.classList.add("hidden");
        img.removeAttribute("src");
        if (initials) {
          initialsEl.textContent = initials;
          initialsEl.classList.remove("hidden");
          placeholder.classList.add("hidden");
        } else {
          initialsEl.classList.add("hidden");
          placeholder.classList.remove("hidden");
        }
      };
      return;
    }

    img.classList.add("hidden");
    img.removeAttribute("src");
    if (initials) {
      initialsEl.textContent = initials;
      initialsEl.classList.remove("hidden");
      placeholder.classList.add("hidden");
    } else {
      initialsEl.classList.add("hidden");
      placeholder.classList.remove("hidden");
    }
  }

  function closeProfileMenu() {
    const menu = document.getElementById("nav-profile-dropdown");
    const btn = document.getElementById("btn-profile-menu");
    if (menu) menu.classList.add("hidden");
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function toggleProfileMenu() {
    const menu = document.getElementById("nav-profile-dropdown");
    const btn = document.getElementById("btn-profile-menu");
    if (!menu || !btn) return;
    const open = menu.classList.contains("hidden");
    menu.classList.toggle("hidden", !open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function doLogout() {
    closeProfileMenu();
    Auth.signOut();
    resetStudyState();
    StudyCache.invalidateAll();
    ProfileCache.invalidate();
    InsightsCache.invalidate();
    state.route = "home";
    state.profile = null;
    state.adminSubjectId = null;
    state.adminLevelId = null;
    toast("Logged out");
    render();
  }

  function bindProfileMenu() {
    const btn = document.getElementById("btn-profile-menu");
    const menu = document.getElementById("nav-profile-dropdown");
    const wrap = document.getElementById("nav-profile");
    if (!btn || !menu || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleProfileMenu();
    });

    menu.querySelectorAll("[data-profile-action]").forEach((item) => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        const action = item.getAttribute("data-profile-action");
        closeProfileMenu();
        if (action === "account") navigate("account");
        else if (action === "profile") navigate("profile");
        else if (action === "mastery") {
          state.masteryView = "hub";
          state.masteryActive = null;
          navigate("mastery");
        } else if (action === "facebook") navigate("facebook");
        else if (action === "logout") doLogout();
      });
    });

    document.addEventListener("click", (e) => {
      if (!wrap) return;
      if (wrap.contains(e.target)) return;
      closeProfileMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeProfileMenu();
    });
  }

  /**
   * H4: short-lived profile cache (default /me without notices).
   * H1: skipIfFresh avoids double-fetch after login/init.
   * H2: notices loaded only when requested (Home banner).
   */
  const ProfileCache = (() => {
    const TTL_MS = 90 * 1000; // 1.5 minutes
    let entry = null; // { t, data, noticesLoaded }

    function get() {
      if (!entry) return null;
      if (Date.now() - entry.t > TTL_MS) {
        entry = null;
        return null;
      }
      return entry.data;
    }

    function noticesLoaded() {
      return Boolean(entry && entry.noticesLoaded && Date.now() - entry.t <= TTL_MS);
    }

    function set(profile, { noticesLoaded: nl = false } = {}) {
      if (!profile) {
        entry = null;
        return;
      }
      const prev = entry && entry.data;
      // Preserve notices if new payload omitted them
      let data = { ...profile };
      if (
        !nl &&
        prev &&
        Array.isArray(prev.content_notices) &&
        data.content_notices == null
      ) {
        data.content_notices = prev.content_notices;
        nl = entry.noticesLoaded;
      }
      if (!Array.isArray(data.content_notices)) {
        data.content_notices = nl ? data.content_notices || [] : data.content_notices || [];
      }
      entry = {
        t: Date.now(),
        data,
        noticesLoaded: Boolean(nl) || Boolean(data.content_notices && data.content_notices.length),
      };
      // If we stored empty notices array from notices=1, mark loaded
      if (nl) entry.noticesLoaded = true;
    }

    function invalidate() {
      entry = null;
    }

    return { get, set, noticesLoaded, invalidate };
  })();

  /**
   * @param {object} [opts]
   * @param {boolean} [opts.force] - ignore cache, always network
   * @param {boolean} [opts.skipIfFresh] - H1: use cache if still within TTL
   * @param {boolean} [opts.notices] - H2: also ensure content_notices (may extra request)
   */
  async function refreshProfile(opts = {}) {
    const force = Boolean(opts.force);
    const skipIfFresh = Boolean(opts.skipIfFresh);
    const wantNotices = Boolean(opts.notices);

    if (!Auth.isLoggedIn()) {
      state.profile = null;
      ProfileCache.invalidate();
      return;
    }

    // H1 / H4: serve from cache when fresh
    if (!force && skipIfFresh) {
      const cached = ProfileCache.get();
      if (cached) {
        state.profile = cached;
        if (wantNotices && !ProfileCache.noticesLoaded()) {
          await ensureContentNotices();
        }
        return;
      }
    } else if (!force) {
      const cached = ProfileCache.get();
      if (cached && (!wantNotices || ProfileCache.noticesLoaded())) {
        state.profile = cached;
        return;
      }
      if (cached && wantNotices && !ProfileCache.noticesLoaded()) {
        state.profile = cached;
        await ensureContentNotices();
        return;
      }
    }

    try {
      // H2: default GET /me without content_notices (fast)
      state.profile = await Api.me(token(), { notices: false });
      ProfileCache.set(state.profile, { noticesLoaded: false });
      // Persist nickname from sign-up session / Cognito token when profile lacks it
      await syncPendingNickname();
      // syncPendingNickname may PATCH and replace profile — re-cache
      if (state.profile) {
        ProfileCache.set(state.profile, {
          noticesLoaded: ProfileCache.noticesLoaded(),
        });
      }
      if (wantNotices) {
        await ensureContentNotices();
      }
    } catch (e) {
      if (e.status === 401) {
        Auth.signOut();
        state.profile = null;
        ProfileCache.invalidate();
      }
    }
  }

  /** H2: lazy-load content_notices into profile (GET /me?notices=1). */
  async function ensureContentNotices() {
    if (!Auth.isLoggedIn()) return;
    if (ProfileCache.noticesLoaded() && state.profile) return;
    try {
      const full = await Api.me(token(), { notices: true });
      const notices = Array.isArray(full.content_notices) ? full.content_notices : [];
      state.profile = {
        ...(state.profile || {}),
        ...full,
        content_notices: notices,
      };
      ProfileCache.set(state.profile, { noticesLoaded: true });
    } catch (e) {
      if (e.status === 401) {
        Auth.signOut();
        state.profile = null;
        ProfileCache.invalidate();
      }
    }
  }

  /**
   * After Home is painted, pull notices in the background and inject banner
   * without a full loading splash.
   */
  function scheduleHomeNoticesRefresh() {
    if (!Auth.isLoggedIn()) return;
    if (ProfileCache.noticesLoaded()) return;
    window.setTimeout(async () => {
      if (state.route !== "home") return;
      await ensureContentNotices();
      if (state.route !== "home") return;
      const notices = (state.profile && state.profile.content_notices) || [];
      if (!notices.length) return;
      const mainEl = main();
      if (!mainEl) return;
      // Re-render Home quietly; keep leaderboard populated
      let boardEntries = [];
      try {
        const board = await Api.leaderboard(token(), { limit: 100 });
        boardEntries = board.entries || [];
      } catch {
        boardEntries = [];
      }
      if (state.route !== "home") return;
      mainEl.innerHTML = viewHome(boardEntries);
      updateNavProfileAvatar();
      bindView();
    }, 0);
  }

  const PENDING_NICKNAME_KEY = "stem_pending_nickname";
  const PENDING_SCHOOL_KEY = "stem_pending_school_id";
  const PENDING_GRADE_KEY = "stem_pending_grade";

  async function syncPendingNickname() {
    const body = {};
    const p = state.profile || {};

    let pendingNick = "";
    try {
      pendingNick = String(sessionStorage.getItem(PENDING_NICKNAME_KEY) || "").trim();
    } catch {
      pendingNick = "";
    }
    if (!pendingNick && typeof Auth.getNicknameFromToken === "function") {
      pendingNick = Auth.getNicknameFromToken() || "";
    }
    if (pendingNick && pendingNick.includes("@")) pendingNick = "";
    const currentNick = String(p.nickname || p.display_name || "").trim();
    if (pendingNick && !currentNick) body.nickname = pendingNick;

    let pendingSchool = "";
    let pendingGrade = "";
    try {
      pendingSchool = String(sessionStorage.getItem(PENDING_SCHOOL_KEY) || "").trim();
      pendingGrade = String(sessionStorage.getItem(PENDING_GRADE_KEY) || "").trim();
    } catch {
      /* ignore */
    }
    if (pendingSchool && !String(p.school_id || "").trim()) body.school_id = pendingSchool;
    if (pendingGrade && !String(p.grade || "").trim()) body.grade = pendingGrade;

    if (!Object.keys(body).length) {
      try {
        sessionStorage.removeItem(PENDING_NICKNAME_KEY);
        sessionStorage.removeItem(PENDING_SCHOOL_KEY);
        sessionStorage.removeItem(PENDING_GRADE_KEY);
      } catch {
        /* ignore */
      }
      return;
    }

    try {
      if (typeof Api.updateMe === "function") {
        state.profile = await Api.updateMe(token(), body);
        ProfileCache.set(state.profile, { noticesLoaded: false });
      }
      try {
        if (body.nickname) sessionStorage.removeItem(PENDING_NICKNAME_KEY);
        if (body.school_id) sessionStorage.removeItem(PENDING_SCHOOL_KEY);
        if (body.grade) sessionStorage.removeItem(PENDING_GRADE_KEY);
      } catch {
        /* ignore */
      }
    } catch {
      // Keep pending for a later session if PATCH fails
    }
  }

  /* ---------- Views ---------- */

  function schoolOptionLabel(s) {
    if (!s) return "";
    if (s.label) return s.label;
    const name = s.name || s.school_id || "";
    const loc = [s.city, s.province].filter(Boolean).join(", ");
    return loc ? `${name} (${loc})` : name;
  }

  function schoolSelectOptionsHtml(schools, selectedId) {
    const list = schools || [];
    const opts = [
      `<option value="">${list.length ? "Select school…" : "No schools yet — ask an admin"}</option>`,
    ];
    for (const s of list) {
      const id = s.school_id || "";
      opts.push(
        `<option value="${escapeAttr(id)}" ${
          id && id === selectedId ? "selected" : ""
        }>${escapeHtml(schoolOptionLabel(s))}</option>`
      );
    }
    return opts.join("");
  }

  /** Fixed grade levels for Sign up and Profile comboboxes (order fixed). */
  const GRADE_LEVELS = [
    "Kindergarten",
    "Grade 1",
    "Grade 2",
    "Grade 3",
    "Grade 4",
    "Grade 5",
    "Grade 6",
    "Grade 7",
    "Grade 8",
    "Grade 9",
    "Grade 10",
    "Grade 11",
    "Grade 12",
  ];

  function gradeSelectOptionsHtml(selectedGrade) {
    const selected = String(selectedGrade || "").trim();
    const opts = [`<option value="">Select grade…</option>`];
    let matched = false;
    for (const g of GRADE_LEVELS) {
      const isSel = g === selected;
      if (isSel) matched = true;
      opts.push(
        `<option value="${escapeAttr(g)}" ${isSel ? "selected" : ""}>${escapeHtml(g)}</option>`
      );
    }
    // Preserve legacy free-text grades until the user picks a standard value
    if (selected && !matched) {
      opts.push(
        `<option value="${escapeAttr(selected)}" selected>${escapeHtml(selected)} (current)</option>`
      );
    }
    return opts.join("");
  }

  function viewAuth() {
    return `
      <div class="card">
        <h1 id="auth-title">Welcome</h1>
        <p id="auth-subtitle" class="muted">Sign up for free. Study available topics at your own pace. When your profile is not active for 6 months, it may be deleted to save resources.</p>
        <div class="tabs">
          <button type="button" id="tab-login" class="active">Log in</button>
          <button type="button" id="tab-signup">Sign up</button>
        </div>
        <form id="auth-form" class="stack">
          <div>
            <label for="email">Email</label>
            <input id="email" type="email" autocomplete="username" required />
          </div>
          <div id="nickname-wrap" class="hidden">
            <label for="nickname">Name / Nickname</label>
            <input id="nickname" type="text" autocomplete="nickname" maxlength="40"
              placeholder="How should we call you?" />
          </div>
          <div id="school-wrap" class="hidden">
            <label for="signup-school">School</label>
            <select id="signup-school">
              <option value="">Loading schools…</option>
            </select>
            <p class="muted school-request-hint">
              (If school is not listed,
              <a href="#" id="school-request-link">request to add</a>)
            </p>
          </div>
          <div id="grade-wrap" class="hidden">
            <label for="signup-grade">Grade</label>
            <select id="signup-grade" autocomplete="off">
              ${gradeSelectOptionsHtml("")}
            </select>
          </div>
          <div id="password-wrap">
            <label for="password" id="password-label">Password</label>
            <div class="password-field">
              <input id="password" type="password" autocomplete="current-password" required minlength="8" />
              <button type="button" class="password-toggle" data-toggle-password="password"
                aria-label="Show password" aria-pressed="false" title="Show password">
                <span class="icon-eye" aria-hidden="true">${eyeIconOpen()}</span>
                <span class="icon-eye-off hidden" aria-hidden="true">${eyeIconOff()}</span>
              </button>
            </div>
            <p class="auth-forgot-wrap">
              <a href="#" id="forgot-password-link">Forgot password?</a>
            </p>
          </div>
          <div id="password-confirm-wrap" class="hidden">
            <label for="password-confirm">Confirm password</label>
            <div class="password-field">
              <input id="password-confirm" type="password" autocomplete="new-password" minlength="8" />
              <button type="button" class="password-toggle" data-toggle-password="password-confirm"
                aria-label="Show confirm password" aria-pressed="false" title="Show password">
                <span class="icon-eye" aria-hidden="true">${eyeIconOpen()}</span>
                <span class="icon-eye-off hidden" aria-hidden="true">${eyeIconOff()}</span>
              </button>
            </div>
            <p id="password-match-hint" class="muted" aria-live="polite"></p>
          </div>
          <div id="code-wrap" class="hidden">
            <label for="code" id="code-label">Email confirmation code</label>
            <input id="code" type="text" inputmode="numeric" autocomplete="one-time-code" />
            <p id="code-hint" class="muted">Check your email after sign-up, then enter the code and tap Confirm email.</p>
          </div>
          <p id="forgot-hint" class="muted hidden" aria-live="polite"></p>
          <button class="btn block" type="submit" id="auth-submit">Log in</button>
          <button class="btn secondary block hidden" type="button" id="auth-confirm">Confirm email</button>
          <button class="btn secondary block hidden" type="button" id="auth-back-login">Back to log in</button>
        </form>
      </div>
      <!-- Modal must stay outside #auth-form (nested forms break validation / submit) -->
      <div id="school-request-modal" class="modal-overlay hidden" role="dialog"
        aria-modal="true" aria-labelledby="school-request-title">
        <div class="modal-card">
          <h2 id="school-request-title">Request a school</h2>
          <p class="muted">We will notify an administrator. You can use a temporary school name until they approve.</p>
          <div id="school-request-form" class="stack">
            <div>
              <label for="req-school-name">School Name</label>
              <input id="req-school-name" name="req_school_name" maxlength="120"
                placeholder="e.g. Rizal Elementary School" autocomplete="organization" />
            </div>
            <div>
              <label for="req-school-city">City</label>
              <input id="req-school-city" name="req_school_city" maxlength="80"
                placeholder="e.g. Cebu City" autocomplete="address-level2" />
            </div>
            <div>
              <label for="req-school-province">Province</label>
              <input id="req-school-province" name="req_school_province" maxlength="80"
                placeholder="e.g. Cebu" autocomplete="address-level1" />
            </div>
            <div class="row" style="margin-top:0.25rem">
              <button class="btn" type="button" id="school-request-submit">Submit request</button>
              <button class="btn secondary" type="button" id="school-request-cancel">Cancel</button>
            </div>
          </div>
        </div>
      </div>`;
  }

  /**
   * Name/Nickname only — never email (used for Home greeting and UI identity).
   */
  function learnerNickname(p) {
    const row = p || {};
    const nick = String(row.nickname || row.display_name || "").trim();
    // Guard: reject values that look like an email address
    if (!nick || nick.includes("@")) return "";
    return nick;
  }

  /** Nickname when set; otherwise empty (do not fall back to email on Home). */
  function learnerDisplayName(p) {
    return learnerNickname(p);
  }

  function leaderboardCsvEscape(value) {
    const s = value == null ? "" : String(value);
    if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  }

  function downloadLeaderboardCsv(entries) {
    const rows = Array.isArray(entries) ? entries : [];
    if (!rows.length) {
      toast("No leaderboard data to download yet.", true);
      return;
    }
    const lines = ["Rank,Name,XP,Grade"];
    for (const e of rows) {
      lines.push(
        [
          e.rank != null ? e.rank : "",
          e.name || "Learner",
          e.xp != null ? e.xp : 0,
          e.grade && e.grade !== "—" ? e.grade : "",
        ]
          .map(leaderboardCsvEscape)
          .join(",")
      );
    }
    // BOM helps Excel open UTF-8 names correctly
    const blob = new Blob(["\ufeff" + lines.join("\n") + "\n"], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const stamp = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `melon-leaderboard-${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Downloaded ${rows.length} row(s)`);
  }

  function leaderboardDownloadIconSvg() {
    return `<svg class="action-svg" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M12 3v12"/>
      <path d="M7 11l5 5 5-5"/>
      <path d="M5 19h14"/>
    </svg>`;
  }

  function leaderboardTableHtml(entries) {
    const rows = Array.isArray(entries) ? entries : [];
    const downloadBtn = `
      <div class="leaderboard-toolbar">
        <button type="button" class="btn-icon secondary leaderboard-csv-btn"
          id="btn-leaderboard-csv"
          title="Download leaderboard as CSV"
          aria-label="Download leaderboard as CSV"
          ${rows.length ? "" : "disabled"}>
          ${leaderboardDownloadIconSvg()}
        </button>
      </div>`;
    if (!rows.length) {
      return `${downloadBtn}
        <p class="muted leaderboard-empty">No rankings yet. Complete a set to earn XP from your speed badge!</p>`;
    }
    const body = rows
      .map((e) => {
        const rank = e.rank != null ? e.rank : "—";
        const name = e.name || "Learner";
        const xp = e.xp != null ? e.xp : 0;
        const grade = e.grade || "—";
        return `<tr>
          <td class="lb-rank">${escapeHtml(String(rank))}</td>
          <td class="lb-name">${escapeHtml(String(name))}</td>
          <td class="lb-xp">${escapeHtml(String(xp))}</td>
          <td class="lb-grade">${escapeHtml(String(grade))}</td>
        </tr>`;
      })
      .join("");
    return `
      ${downloadBtn}
      <div class="table-wrap leaderboard-wrap">
        <table class="leaderboard-table" aria-label="Top 100 leaderboard">
          <thead>
            <tr>
              <th scope="col">Rank</th>
              <th scope="col">Name</th>
              <th scope="col">XP</th>
              <th scope="col">Grade</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function viewHome(leaderboardEntries) {
    const p = state.profile || {};
    const name = learnerNickname(p);
    state.leaderboardEntries = Array.isArray(leaderboardEntries)
      ? leaderboardEntries
      : [];
    return `
      <div class="card">
        <h1>Hello${name ? `, ${escapeHtml(name)}` : ""}</h1>
        <p class="muted">Study time: ${formatDuration(p.total_study_ms || 0)} total · ${p.study_sessions_count || 0} session(s)
          ${p.last_study_elapsed_ms ? ` · last ${formatDuration(p.last_study_elapsed_ms)}` : ""}</p>
        <div class="row home-actions" style="margin-top:1rem">
          <button class="btn home-action-btn accent" type="button" data-go="assessment">Assessment</button>
          <button class="btn home-action-btn" type="button" data-go="study">Start studying</button>
          <button class="btn home-action-btn secondary" type="button" data-go="insights">See insights</button>
        </div>
      </div>
      ${contentNoticesHtml(p.content_notices)}
      <div class="card">
        <h2>How it works</h2>
        <p>1. Take an <strong>Assessment</strong> to find your best starting level, or open Study to pick a set.</p>
        <p>2. Answer questions — accuracy &amp; speed unlock insights.</p>
        <p>3. Complete any set of Level N to unlock Level N+1 (you need not finish every variation of N).</p>
      </div>
      <div class="card leaderboard-card">
        <h2>Leaderboard</h2>
        <p class="muted leaderboard-hint">XP from set badges: Legendary Wizard 5 · Superb Advanced 3 · Cool Novice 1</p>
        ${leaderboardTableHtml(state.leaderboardEntries)}
      </div>`;
  }

  /**
   * Assessment setup: Category + Topic combos (same model as Study),
   * preview of 10 questions × each level, Start Assessment.
   */
  async function viewAssessment() {
    try {
      // Active quiz / results reuse study shell
      if (state.session && state.session.is_assessment && state.studyPhase) {
        if (state.studyPhase === "results" && state.studyResults) {
          return viewAssessmentResults(state.studyResults);
        }
        return viewSession();
      }

      const tok = token();
      const allSubjects = await StudyCache.loadSubjects(tok);
      if (!allSubjects.length) {
        return `<div class="card"><h1>Assessment</h1>
          <p class="muted">No subjects yet. Ask an admin to run seed.</p>
          <button class="btn secondary" type="button" data-go="home">Back to Home</button>
        </div>`;
      }

      const stemOrder = ["Science", "Technology", "Engineering", "Mathematics"];
      const categoriesPresent = [
        ...new Set(
          allSubjects.map((s) => s.category || "Mathematics").filter(Boolean)
        ),
      ];
      categoriesPresent.sort((a, b) => {
        const ia = stemOrder.indexOf(a);
        const ib = stemOrder.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      });

      if (
        !state.assessmentCategory ||
        !categoriesPresent.includes(state.assessmentCategory)
      ) {
        state.assessmentCategory =
          categoriesPresent.find((c) => c === "Mathematics") ||
          categoriesPresent[0] ||
          null;
      }

      // Group "Arithmetic (Addition) - Level 1"…"Level 6" → one Topic option
      const subjectsInCategory = allSubjects.filter(
        (s) => (s.category || "Mathematics") === state.assessmentCategory
      );
      const topicGroups = new Map(); // baseTopic → { baseTopic, subjects, repId }
      for (const s of subjectsInCategory) {
        const base = baseTopicName(s.topic || s.name || "") || s.subject_id;
        if (!topicGroups.has(base)) {
          topicGroups.set(base, {
            baseTopic: base,
            subjects: [],
            repId: s.subject_id,
          });
        }
        const g = topicGroups.get(base);
        g.subjects.push(s);
        // Prefer lowest major as representative (Level 1 subject)
        const maj = parseMajorLevel(s.topic, s.name, s.subject_id);
        const repMaj = parseMajorLevel(
          g.subjects.find((x) => x.subject_id === g.repId)?.topic,
          g.subjects.find((x) => x.subject_id === g.repId)?.name,
          g.repId
        );
        if (maj != null && (repMaj == null || maj < repMaj)) {
          g.repId = s.subject_id;
        }
      }
      const baseTopics = Array.from(topicGroups.keys()).sort((a, b) =>
        a.localeCompare(b)
      );

      if (
        !state.assessmentBaseTopic ||
        !topicGroups.has(state.assessmentBaseTopic)
      ) {
        // Prefer Arithmetic (Addition) when present
        const prefer =
          baseTopics.find((t) => /arithmetic/i.test(t) && /addition/i.test(t)) ||
          baseTopics.find((t) => /arithmetic/i.test(t)) ||
          baseTopics[0] ||
          null;
        state.assessmentBaseTopic = prefer;
      }

      const selectedGroup = state.assessmentBaseTopic
        ? topicGroups.get(state.assessmentBaseTopic)
        : null;
      if (selectedGroup) {
        state.assessmentSubjectId = selectedGroup.repId;
      } else {
        state.assessmentSubjectId = null;
      }

      const catOptions = categoriesPresent
        .map(
          (c) =>
            `<option value="${escapeAttr(c)}" ${
              c === state.assessmentCategory ? "selected" : ""
            }>${escapeHtml(c)}</option>`
        )
        .join("");

      const topicOptions = baseTopics.length
        ? baseTopics
            .map(
              (t) =>
                `<option value="${escapeAttr(t)}" ${
                  t === state.assessmentBaseTopic ? "selected" : ""
                }>${escapeHtml(t)}</option>`
            )
            .join("")
        : `<option value="">No topics</option>`;

      let previewHtml = `<p class="muted">Select a topic to prepare your assessment.</p>`;
      let canStart = false;
      if (selectedGroup && selectedGroup.repId) {
        try {
          const preview = await Api.previewAssessment(tok, selectedGroup.repId);
          state.assessmentPreview = preview;
          const levels = preview.levels || [];
          const rows = levels
            .map(
              (lv) => `<tr>
                <td>${escapeHtml(lv.name || lv.level_id)}</td>
                <td class="num">${lv.sets_in_band ?? "—"}</td>
                <td class="num">${lv.available ?? 0}</td>
                <td class="num">${lv.sample_size ?? 0}</td>
              </tr>`
            )
            .join("");
          canStart = (preview.total_questions || 0) > 0;
          const majorCount =
            preview.major_count != null ? preview.major_count : preview.level_count || 0;
          const topicLabel =
            preview.base_topic || preview.topic || state.assessmentBaseTopic || "";
          previewHtml = `
            <div class="assessment-preview">
              <p class="assessment-topic-label">
                <strong>${escapeHtml(topicLabel)}</strong>
                <span class="muted"> · collective assessment across available levels</span>
              </p>
              <p>
                <strong>${escapeHtml(String(preview.total_questions || 0))}</strong>
                questions
                <span class="muted">
                  (${escapeHtml(String(preview.questions_per_level || 10))} from each of
                  ${escapeHtml(String(majorCount))} Level${
                    majorCount === 1 ? "" : "s"
                  }: Level&nbsp;1…Level&nbsp;${escapeHtml(String(majorCount || 1))})
                </span>
              </p>
              ${
                levels.length
                  ? `<div class="table-wrap"><table class="data-table assessment-level-table">
                      <thead><tr>
                        <th>Level</th><th>Sets</th><th>In bank</th><th>In test</th>
                      </tr></thead>
                      <tbody>${rows}</tbody>
                    </table></div>`
                  : `<p class="muted">No levels with questions yet.</p>`
              }
            </div>`;
        } catch (err) {
          state.assessmentPreview = null;
          previewHtml = `<p class="muted err-text">${escapeHtml(
            err.message || "Could not load assessment preview."
          )}</p>`;
        }
      }

      return `
        <div class="card">
          <div class="row" style="justify-content:space-between;align-items:flex-start;gap:0.5rem">
            <h1 style="margin:0">Assessment</h1>
            <button class="btn secondary btn-sm" type="button" data-go="home">← Home</button>
          </div>
          <p class="muted">
            Find your best starting level. Pick a <strong>topic</strong> (not a single Level).
            We build one test with up to <strong>10 questions from each available Level</strong>
            (Level&nbsp;1, Level&nbsp;2, …) for that topic — e.g. 6 levels → 60 questions.
          </p>
          <div class="study-pickers study-pickers-stacked" style="margin-top:0.75rem">
            <div class="study-picker-field">
              <label for="assess-category">Category</label>
              <select id="assess-category">${catOptions}</select>
            </div>
            <div class="study-picker-field">
              <label for="assess-topic">Topic</label>
              <select id="assess-topic" ${baseTopics.length ? "" : "disabled"}>${topicOptions}</select>
            </div>
          </div>
        </div>
        <div class="card">
          <h2>Your assessment set</h2>
          ${previewHtml}
          <div class="row" style="margin-top:1rem">
            <button class="btn accent" type="button" id="start-assessment"
              ${canStart && selectedGroup ? "" : "disabled"}
              data-subject="${escapeAttr((selectedGroup && selectedGroup.repId) || "")}">
              Start Assessment
            </button>
          </div>
          <p class="muted" style="margin-top:0.5rem;font-size:0.85rem">
            Proficient on a level means at least <strong>Superb Advanced</strong>
            accuracy and speed. We then suggest the next level as your study start.
          </p>
        </div>`;
    } catch (err) {
      return `<div class="card"><h1>Assessment</h1>
        <p class="muted">${escapeHtml(err.message || String(err))}</p>
        <button class="btn" type="button" id="retry-load">Retry</button>
      </div>`;
    }
  }

  function viewAssessmentResults(res) {
    const accPct = Math.round((res.accuracy || 0) * 100);
    const majors = res.major_results || [];
    const levels = res.level_results || [];
    const suggestedMajor = res.suggested_major;
    const suggestedName =
      res.suggested_level_name ||
      (suggestedMajor != null ? `Level ${suggestedMajor}` : res.suggested_level_id || "Level 1");

    const majorRows = majors
      .map((m) => {
        const pct = Math.round((m.accuracy || 0) * 100);
        const badge = m.proficient
          ? `<span class="badge ok">proficient</span>`
          : m.passed
            ? `<span class="badge warn">needs speed</span>`
            : `<span class="badge err">practice</span>`;
        const speed = m.speed_badge_label
          ? escapeHtml(m.speed_badge_label)
          : "—";
        return `<tr>
          <td>Level ${escapeHtml(String(m.major))}</td>
          <td class="num">${pct}%</td>
          <td>${speed}</td>
          <td>${badge}</td>
        </tr>`;
      })
      .join("");

    const levelRows = levels
      .map((lv) => {
        const pct = Math.round((lv.accuracy || 0) * 100);
        const badge = lv.proficient
          ? `<span class="badge ok">proficient</span>`
          : lv.passed
            ? `<span class="badge warn">ok</span>`
            : `<span class="badge err">retry</span>`;
        return `<tr>
          <td>${escapeHtml(lv.name || lv.level_id)}</td>
          <td class="num">${lv.correct}/${lv.answered}</td>
          <td class="num">${pct}%</td>
          <td>${badge}</td>
        </tr>`;
      })
      .join("");

    return `
      <div class="card assessment-results-card">
        <h1>Assessment results</h1>
        <p class="muted">${escapeHtml(res.subject_label || "")}</p>
        <div class="results-summary-row">
          <div>
            <p style="margin:0"><strong>${res.correct || 0}</strong> / ${res.total_questions || 0} correct
              · ${accPct}% · ⏱ ${formatDuration(res.total_elapsed_ms || 0)}</p>
          </div>
        </div>
        <div class="assessment-suggest card-inset">
          <h2 class="assessment-suggest-title">Suggested starting point</h2>
          <p class="assessment-suggest-level">Level ${
            suggestedMajor != null
              ? escapeHtml(String(suggestedMajor))
              : escapeHtml(suggestedName)
          }</p>
          <p class="muted">${escapeHtml(res.suggestion_message || "")}</p>
        </div>
        ${
          majors.length
            ? `<h2>By level band</h2>
               <div class="table-wrap"><table class="data-table">
                 <thead><tr><th>Band</th><th>Accuracy</th><th>Speed</th><th>Status</th></tr></thead>
                 <tbody>${majorRows}</tbody>
               </table></div>`
            : ""
        }
        ${
          levelRows
            ? `<h2>By question set</h2>
               <div class="table-wrap"><table class="data-table">
                 <thead><tr><th>Set</th><th>Score</th><th>Acc.</th><th>Status</th></tr></thead>
                 <tbody>${levelRows}</tbody>
               </table></div>`
            : ""
        }
        <div class="row" style="margin-top:1rem;flex-wrap:wrap">
          <button type="button" class="btn" id="assessment-go-study"
            data-subject="${escapeAttr(res.subject_id || "")}"
            data-level="${escapeAttr(res.suggested_level_id || "")}">
            Start studying at ${escapeHtml(suggestedName)}
          </button>
          <button type="button" class="btn secondary" id="assessment-retake"
            data-subject="${escapeAttr(res.subject_id || "")}">
            Retake assessment
          </button>
          <button type="button" class="btn secondary" data-go="home">Home</button>
        </div>
      </div>`;
  }

  /** Banner when question banks changed since the learner last practiced. */
  function contentNoticesHtml(notices) {
    const list = Array.isArray(notices) ? notices : [];
    if (!list.length) return "";
    const items = list
      .map((n) => {
        const badge =
          n.change_type === "cleared"
            ? `<span class="badge err">removed</span>`
            : `<span class="badge warn">updated</span>`;
        const where = [n.subject_label, n.level_name || n.level_id]
          .filter(Boolean)
          .join(" · ");
        return `<li class="content-notice-item">
          <div class="content-notice-item-head">
            <strong>${escapeHtml(where || n.level_id || "Level")}</strong>
            ${badge}
          </div>
          <p class="muted">${escapeHtml(n.message || "Study content changed since your last attempt.")}</p>
        </li>`;
      })
      .join("");
    return `
      <div class="card content-notice-card" role="status">
        <h2 class="content-notice-title">Content updates</h2>
        <p class="muted content-notice-lead">
          Some levels you practiced have changed since your last attempt. Past scores are kept.
        </p>
        <ul class="content-notice-list">${items}</ul>
        <div class="row" style="margin-top:0.75rem">
          <button type="button" class="btn" data-go="study">Go to Study</button>
        </div>
      </div>`;
  }

  async function viewStudy() {
    try {
      if (state.session && state.studyPhase && !state.session.is_assessment) {
        return viewSession();
      }

      const tok = token();
      // #3 batch + #4 cache: subjects + landing via bootstrap / cache
      // (no full /insights; no separate listSubjects + landing when cold).
      const preferredSid = state.studySubjectId || null;
      const { subjects: allSubjects, landing: bootLanding } =
        await StudyCache.loadStudyData(tok, preferredSid);

      if (!allSubjects.length) {
        return `<div class="card"><h1>Study</h1><p class="muted">No subjects yet. Ask an admin to run seed.</p></div>`;
      }

      // Categories present in content (prefer STEM order)
      const stemOrder = ["Science", "Technology", "Engineering", "Mathematics"];
      const categoriesPresent = [
        ...new Set(
          allSubjects.map((s) => s.category || "Mathematics").filter(Boolean)
        ),
      ];
      categoriesPresent.sort((a, b) => {
        const ia = stemOrder.indexOf(a);
        const ib = stemOrder.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
      });

      // Restore / default Category
      if (
        !state.studyCategory ||
        !categoriesPresent.includes(state.studyCategory)
      ) {
        const preferred =
          categoriesPresent.find((c) => c === "Mathematics") ||
          categoriesPresent[0];
        state.studyCategory = preferred;
      }

      const topicsInCategory = allSubjects
        .filter((s) => (s.category || "Mathematics") === state.studyCategory)
        .slice()
        .sort((a, b) => {
          const ao = Number(a.sort_order) || 0;
          const bo = Number(b.sort_order) || 0;
          if (ao !== bo) return ao - bo;
          return String(a.topic || a.name || "").localeCompare(
            String(b.topic || b.name || "")
          );
        });

      // Restore / default Topic (subject)
      if (
        !state.studySubjectId ||
        !topicsInCategory.some((s) => s.subject_id === state.studySubjectId)
      ) {
        const mathPref = topicsInCategory.find((s) => s.subject_id === "math");
        // Prefer bootstrap landing subject if still in this category
        const fromBoot =
          bootLanding &&
          topicsInCategory.find((s) => s.subject_id === bootLanding.subject_id);
        state.studySubjectId =
          (fromBoot || mathPref || topicsInCategory[0] || {}).subject_id || null;
      }

      const selected =
        topicsInCategory.find((s) => s.subject_id === state.studySubjectId) ||
        null;

      // Landing: use bootstrap result or cache; fetch only if topic differs
      let levels = [];
      let progMap = {};
      let radarProgressRows = [];
      if (selected) {
        let landing = null;
        if (bootLanding && bootLanding.subject_id === selected.subject_id) {
          landing = bootLanding;
        } else {
          landing = StudyCache.getLanding(selected.subject_id);
        }
        if (!landing) {
          try {
            landing = await Api.studyLanding(tok, selected.subject_id);
            StudyCache.setLanding(selected.subject_id, landing);
          } catch (landingErr) {
            // Fallback: legacy multi-call path
            try {
              const [lvRes, progress] = await Promise.all([
                Api.listLevels(tok, selected.subject_id),
                Api.getProgress(tok, selected.subject_id),
              ]);
              levels = lvRes.levels || [];
              progMap = Object.fromEntries(
                (progress.progress || []).map((p) => [
                  `${p.subject_id}:${p.level_id}`,
                  p,
                ])
              );
              const base = baseTopicName(selected.topic || selected.name || "");
              const siblings = allSubjects.filter(
                (s) =>
                  (s.category || "Mathematics") ===
                    (selected.category || "Mathematics") &&
                  baseTopicName(s.topic || s.name || "") === base
              );
              radarProgressRows = await loadProgressRowsForSubjects(
                tok,
                siblings
              );
              landing = null;
            } catch {
              throw landingErr;
            }
          }
        }
        if (landing) {
          levels = landing.levels || [];
          progMap = Object.fromEntries(
            (landing.progress || []).map((p) => [
              `${p.subject_id}:${p.level_id}`,
              p,
            ])
          );
          radarProgressRows = landing.progress_rows || [];
        }
      }

      const categoryOptions = categoriesPresent
        .map(
          (c) =>
            `<option value="${escapeAttr(c)}" ${
              c === state.studyCategory ? "selected" : ""
            }>${escapeHtml(c)}</option>`
        )
        .join("");

      const topicOptions = topicsInCategory.length
        ? topicsInCategory
            .map(
              (s) =>
                `<option value="${escapeAttr(s.subject_id)}" ${
                  s.subject_id === state.studySubjectId ? "selected" : ""
                }>${escapeHtml(s.topic || s.name || s.subject_id)}</option>`
            )
            .join("")
        : `<option value="">No topics in this category</option>`;

      const items = levels
        .map((lv) => {
          const pr = progMap[`${selected.subject_id}:${lv.level_id}`];
          const statusCls = pr
            ? pr.status === "completed"
              ? "ok"
              : pr.status === "failed"
                ? "err"
                : "warn"
            : "";
          return `
          <div class="level-item" data-level-row="${escapeHtml(lv.level_id)}">
            <div class="grow">
              <div class="title">
                <strong>${escapeHtml(lv.name)}</strong>
                ${
                  pr
                    ? `<span class="badge ${statusCls}">${escapeHtml(pr.status)}</span>`
                    : `<span class="badge">new</span>`
                }
                ${speedBadgeHtml(pr && pr.status === "completed" ? pr : null)}
              </div>
              <div class="muted">${lv.question_count || 0} questions · pass ≥ ${Math.round((lv.pass_accuracy || 0.8) * 100)}%
                ${
                  pr && pr.best_elapsed_ms != null
                    ? ` · best ⏱ ${formatDuration(pr.best_elapsed_ms)}`
                    : ""
                }
              </div>
            </div>
            <button class="btn" type="button"
              data-start="${escapeHtml(selected.subject_id)}"
              data-level="${escapeHtml(lv.level_id)}">Start</button>
          </div>`;
        })
        .join("");

      // Radar: one vertex per major Level (1..N) for this base topic
      const radarHtml = selected
        ? performanceRadarHtml({
            subjects: allSubjects,
            progressRows: radarProgressRows,
            focus: { subjectId: selected.subject_id },
            size: 168,
            showCaption: true,
          })
        : performanceRadarHtml({ axes: [] });

      const gradeLabel = selected && selected.grade_level
        ? String(selected.grade_level).trim()
        : "";

      return `
        <div class="card study-landing-card">
          <div class="study-header">
            <div class="study-header-main">
              <div class="study-title-row">
                <h1 class="study-page-title">Study</h1>
                ${
                  gradeLabel
                    ? `<div class="study-grade-badge" title="Content grade level">${escapeHtml(
                        gradeLabel
                      )}</div>`
                    : ""
                }
              </div>
              <div class="study-pickers study-pickers-stacked">
                <div class="study-picker-field">
                  <label for="study-category">Category</label>
                  <select id="study-category">${categoryOptions}</select>
                </div>
                <div class="study-picker-field">
                  <label for="study-topic">Topic</label>
                  <select id="study-topic" ${topicsInCategory.length ? "" : "disabled"}>${topicOptions}</select>
                </div>
              </div>
              ${
                selected
                  ? `<p class="muted study-topic-desc">${escapeHtml(selected.description || "")}</p>`
                  : ""
              }
            </div>
            <div class="study-radar">
              ${radarHtml}
            </div>
          </div>
          <div class="study-levels">
            ${items || "<p class='muted'>No levels configured for this topic.</p>"}
          </div>
        </div>`;
    } catch (e) {
      if (e.status === 402) {
        return viewPaywall(e.message);
      }
      return `<div class="card"><h1>Study</h1><p class="muted">${escapeHtml(e.message)}</p></div>`;
    }
  }

  /** After Study renders, scroll to a level linked from Insights. */
  function focusStudyLevelIfNeeded() {
    const lid = state.studyFocusLevelId;
    if (!lid) return;
    state.studyFocusLevelId = null;
    window.setTimeout(() => {
      const btn = Array.from(main().querySelectorAll("[data-start][data-level]")).find(
        (el) => el.getAttribute("data-level") === lid
      );
      const row = btn && btn.closest(".level-item");
      if (row) {
        row.classList.add("level-item-focus");
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        window.setTimeout(() => row.classList.remove("level-item-focus"), 2200);
      }
      if (btn) {
        try {
          btn.focus({ preventScroll: true });
        } catch {
          btn.focus();
        }
      }
    }, 80);
  }

  function viewSession() {
    const s = state.session;
    if (!s) return `<div class="card"><p class="muted">No active session.</p></div>`;

    if (state.studyPhase === "results" && state.studyResults) {
      return viewStudyResults(state.studyResults);
    }

    const questions = s.questions || [];
    const q = questions[state.qIndex];
    const total = questions.length;
    const isLast = state.qIndex >= total - 1;
    const pct = total ? Math.round((state.qIndex / total) * 100) : 0;
    const saved = q ? state.clientAnswers[q.question_id] : "";
    const inCountdown = state.studyPhase === "countdown";

    // Stable DOM shell: chrome + answer controls stay mounted; only question text swaps in-place.
    return `
      <div class="card study-card ${inCountdown ? "study-countdown-active" : ""}" id="study-shell">
        <div class="study-chrome" id="study-chrome">
          <div class="row study-meta">
            <span class="badge study-badge" id="study-badge">${Math.min(state.qIndex + 1, total)} / ${total}</span>
            <span class="study-timer" id="study-timer" aria-live="polite">
              ⏱ ${formatDuration(getStudyElapsedMs())}
            </span>
            <span class="muted grow study-level" style="text-align:right">${escapeHtml(
              s.is_assessment
                ? "Assessment"
                : s.level_id || ""
            )}</span>
          </div>
          <div class="progress-bar study-progress" style="margin:0.75rem 0">
            <span id="study-progress-fill" style="width:${pct}%"></span>
          </div>
        </div>

        <div class="study-qa" id="study-qa">
          <div class="study-stage" id="study-stage">
            <div class="countdown-overlay ${inCountdown ? "" : "hidden"}" id="countdown-overlay" aria-live="assertive">
              <div class="countdown-bubble" id="countdown-num">${state.countdownValue}</div>
              <p class="countdown-caption">Get ready…</p>
            </div>
            <div class="study-question-wrap ${inCountdown ? "study-obscured" : ""}" id="study-question-wrap">
              <div class="prompt study-prompt-xl" id="prompt">${
                q ? escapeHtml(q.prompt) + " = ?" : ""
              }</div>
            </div>
          </div>

          <form id="answer-form" class="stack study-answer-form ${inCountdown ? "study-obscured" : ""}" autocomplete="off">
            <label class="study-answer-label" for="answer">Your answer</label>
            <input id="answer" class="study-answer-input" inputmode="decimal" autocomplete="off"
              enterkeyhint="${isLast ? "done" : "go"}"
              autocapitalize="off" autocorrect="off" spellcheck="false"
              placeholder="Type here" ${inCountdown ? "disabled" : ""}
              value="${escapeAttr(saved || "")}" />
            <!-- Next sits directly under the answer so it stays near the question + keyboard -->
            <div class="study-actions" id="study-actions">
              <button class="btn study-next-btn" type="submit" id="study-next-btn"
                tabindex="-1" ${inCountdown ? "disabled" : ""}>
                ${isLast ? "Submit ✓" : "Next →"}
              </button>
            </div>
            <p class="muted study-hint" id="study-hint">
              ${
                isLast
                  ? "Blank counts as 0. Tap Submit when ready."
                  : "Blank counts as 0. Tap Next to continue."
              }
            </p>
          </form>
        </div>
      </div>`;
  }

  /** Keep soft keyboard open without breaking Next/Submit taps.
   *
   * Bug history: preventDefault on touchstart/pointerdown blocked the subsequent
   * click on iOS, so the form never submitted and Next appeared broken.
   * Fix: mousedown preventDefault (mouse only) keeps focus; touchend manually
   * submits the form after re-focusing the answer field.
   */
  function keepAnswerKeyboardOpen() {
    const input = document.getElementById("answer");
    const nextBtn = document.getElementById("study-next-btn");
    const answerForm = document.getElementById("answer-form");
    if (!input || !nextBtn || !answerForm || nextBtn.dataset.kbHold === "1") return;
    nextBtn.dataset.kbHold = "1";

    // Desktop / mouse: prevent focus steal; click still fires and submits the form
    nextBtn.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      if (state.studyPhase === "answering" && !input.disabled) {
        focusAnswerField();
      }
    });

    // Touch: do NOT preventDefault on touchstart (that cancels click).
    // On touchend, keep keypad + submit programmatically.
    nextBtn.addEventListener(
      "touchend",
      (e) => {
        if (state.studyPhase !== "answering") return;
        if (nextBtn.disabled || state.studyTransitioning) return;
        // Stop the synthetic mouse click that would double-submit
        if (e.cancelable) e.preventDefault();
        focusAnswerField();
        try {
          if (typeof answerForm.requestSubmit === "function") {
            answerForm.requestSubmit(nextBtn);
          } else {
            answerForm.dispatchEvent(
              new Event("submit", { cancelable: true, bubbles: true })
            );
          }
        } catch (_) {
          answerForm.dispatchEvent(
            new Event("submit", { cancelable: true, bubbles: true })
          );
        }
      },
      { passive: false }
    );
  }

  /** Focus answer field and re-measure keyboard inset (idempotent). */
  function focusAnswerField() {
    const input = document.getElementById("answer");
    if (!input || input.disabled) return;
    try {
      input.focus({ preventScroll: true });
    } catch {
      input.focus();
    }
    // Keep caret at end so typing continues naturally
    try {
      const len = (input.value || "").length;
      input.setSelectionRange(len, len);
    } catch (_) { /* ignore */ }
    if (window.visualViewport) {
      const vv = window.visualViewport;
      const keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      document.documentElement.style.setProperty(
        "--vv-keyboard",
        `${Math.round(keyboard)}px`
      );
    }
  }

  /**
   * In-place question swap — does NOT re-render the page.
   * Keeps answer input mounted so the mobile keyboard stays open.
   * Timer is paused around this transition so load time is not counted.
   */
  function showQuestionInPlace(index) {
    const s = state.session;
    if (!s) return;
    const questions = s.questions || [];
    const total = questions.length;
    if (index < 0 || index >= total) return;

    state.qIndex = index;
    const q = questions[index];
    const isLast = index >= total - 1;
    const pct = total ? Math.round((index / total) * 100) : 0;
    const saved = state.clientAnswers[q.question_id];

    const badge = document.getElementById("study-badge");
    if (badge) badge.textContent = `${index + 1} / ${total}`;

    const fill = document.getElementById("study-progress-fill");
    if (fill) fill.style.width = `${pct}%`;

    const prompt = document.getElementById("prompt");
    if (prompt) {
      prompt.classList.remove("q-swap-in");
      // force reflow for animation
      void prompt.offsetWidth;
      prompt.textContent = `${q.prompt} = ?`;
      prompt.classList.add("q-swap-in");
    }

    const input = document.getElementById("answer");
    if (input) {
      // Never disable the field during quiz — disabling dismisses the mobile keypad
      input.disabled = false;
      // Only rewrite value if it changed (avoids some Android keyboard resets)
      const nextVal = saved != null ? String(saved) : "";
      if (input.value !== nextVal) input.value = nextVal;
      input.setAttribute("enterkeyhint", isLast ? "done" : "go");
    }

    const btn = document.getElementById("study-next-btn");
    if (btn) {
      btn.textContent = isLast ? "Submit ✓" : "Next →";
      // Keep enabled during question swap so focus/keyboard stay stable
      btn.disabled = false;
    }

    const hint = document.getElementById("study-hint");
    if (hint) {
      hint.textContent = isLast
        ? "Blank counts as 0. Tap Submit when ready."
        : "Blank counts as 0. Tap Next to continue.";
    }

    updateStudySubmitEnabled();
    // Re-assert focus after DOM text updates (critical for iOS keyboard persistence)
    focusAnswerField();
    // Second tick: some WebViews drop the keypad on the first focus after a tap
    window.requestAnimationFrame(() => focusAnswerField());
  }

  /**
   * Parse Level N from ids like Level-1-20, Level 1-3, l1 → major number N.
   */
  function parseLevelMajor(levelIdOrName) {
    const text = String(levelIdOrName || "");
    let m = text.match(/level[\s_-]*(\d+)[\s_.-]+\d+/i);
    if (m) return parseInt(m[1], 10);
    m = text.match(/level[\s_-]*(\d+)/i);
    if (m) return parseInt(m[1], 10);
    m = text.match(/^l(\d+)$/i);
    if (m) return parseInt(m[1], 10);
    return null;
  }

  /**
   * After finishing a set: which sets remain, is the Level N band complete,
   * is the whole topic complete, and which incomplete set to offer next.
   */
  async function getSetCompletionContext(subjectId, currentLevelId) {
    const [levelsRes, progressRes] = await Promise.all([
      Api.listLevels(token(), subjectId),
      Api.getProgress(token(), subjectId),
    ]);
    const levels = (levelsRes.levels || [])
      .slice()
      .sort((a, b) => (Number(a.order) || 0) - (Number(b.order) || 0));
    const completedIds = new Set(
      (progressRes.progress || [])
        .filter((p) => p.status === "completed")
        .map((p) => p.level_id)
    );
    // Ensure the set just finished counts even if progress lag
    if (currentLevelId) completedIds.add(currentLevelId);

    const major = parseLevelMajor(currentLevelId);
    const bandLevels =
      major != null
        ? levels.filter((l) => parseLevelMajor(l.level_id || l.name) === major)
        : levels.slice();
    const bandIncomplete = bandLevels.filter((l) => !completedIds.has(l.level_id));
    const topicIncomplete = levels.filter((l) => !completedIds.has(l.level_id));
    const bandComplete = bandLevels.length > 0 && bandIncomplete.length === 0;
    const topicComplete = levels.length > 0 && topicIncomplete.length === 0;

    const current = levels.find((l) => l.level_id === currentLevelId);
    const currentOrder = Number(current && current.order) || 0;

    // Prefer next incomplete by order after current, else first incomplete (e.g. skipped sets)
    let next =
      topicIncomplete.find((l) => (Number(l.order) || 0) > currentOrder) || null;
    if (!next && topicIncomplete.length) next = topicIncomplete[0];

    // Prefer staying in same Level N band when possible
    const nextInBand =
      bandIncomplete.find((l) => (Number(l.order) || 0) > currentOrder) ||
      bandIncomplete[0] ||
      null;
    if (nextInBand) next = nextInBand;

    return {
      major,
      bandComplete,
      topicComplete,
      next,
      bandRemaining: bandIncomplete.length,
      topicRemaining: topicIncomplete.length,
      bandTotal: bandLevels.length,
      topicTotal: levels.length,
      bandDone: bandLevels.length - bandIncomplete.length,
      topicDone: levels.length - topicIncomplete.length,
    };
  }

  function craftResultsCopy(res, ctx) {
    const passed = Boolean(res.passed);
    const major = ctx && ctx.major != null ? ctx.major : null;
    const levelWord = major != null ? `Level ${major}` : "this level";
    const setName = res.level_id || "this set";

    if (!passed) {
      return {
        kicker: "Nice try — keep going!",
        title: "🌱 Almost there!",
        msg:
          (res.recommendation &&
            res.recommendation.actions &&
            res.recommendation.actions[0]) ||
          "Practice again to clear this set.",
        nextLabel: "Try this set again →",
        nextHint: "Retake the same questions when you're ready.",
        nextMode: "retry",
      };
    }

    // Every set in the topic complete
    if (ctx && ctx.topicComplete) {
      return {
        kicker: "Awesome job!",
        title: "🎉 Level cleared!",
        msg:
          major != null
            ? `You finished every set in Level ${major} and this topic. Outstanding work!`
            : "You finished every set in this topic. Outstanding work!",
        nextLabel: "All sets done ✓",
        nextHint: "You completed every question set — great work!",
        nextMode: "done",
      };
    }

    // All Level N-x sets complete; more levels exist in the topic
    if (ctx && ctx.bandComplete && !ctx.topicComplete) {
      const nextName = ctx.next ? ctx.next.name || ctx.next.level_id : "the next level";
      return {
        kicker: "Awesome job!",
        title: `🎉 ${levelWord} cleared!`,
        msg: `You completed all ${ctx.bandTotal} set(s) of ${levelWord}. Ready for ${nextName}?`,
        nextLabel: ctx.next
          ? `Next: ${ctx.next.name || ctx.next.level_id} →`
          : "Continue →",
        nextHint: "Continue to the next level when you're ready.",
        nextMode: "next",
      };
    }

    // One set passed; unfinished sets remain (including earlier Level N-x)
    const remaining =
      ctx && ctx.bandRemaining > 0
        ? ctx.bandRemaining
        : (ctx && ctx.topicRemaining) || 0;
    const nextName = ctx && ctx.next ? ctx.next.name || ctx.next.level_id : null;
    return {
      kicker: "Awesome job!",
      title: "✅ Set complete!",
      msg:
        remaining > 0
          ? `You cleared ${setName}. ${remaining} more set(s) left${
              major != null ? ` in Level ${major}` : ""
            } — keep it up!`
          : "Great work on this set!",
      nextLabel: nextName ? `Next Set: ${nextName} →` : "Next Set →",
      nextHint:
        remaining > 0
          ? "Jump to another unfinished set when you're ready."
          : "Continue when you're ready.",
      nextMode: nextName ? "next" : "done",
    };
  }

  function viewStudyResults(res) {
    const passed = Boolean(res.passed);
    const stars = passed ? 3 : res.accuracy >= 0.6 ? 2 : 1;
    const starHtml = "⭐".repeat(stars) + "☆".repeat(3 - stars);
    const accPct = Math.round((res.accuracy || 0) * 100);
    const ctx = res.setContext || null;
    const copy = craftResultsCopy(res, ctx);

    const details = res.details || [];
    const detailRows = details
      .slice(0, 12)
      .map(
        (d) => `
        <li class="result-item ${d.correct ? "ok" : "no"}">
          <span class="result-emoji">${d.correct ? "🌟" : "💪"}</span>
          <span><strong>${escapeHtml(d.prompt || d.question_id)}</strong>
            → you: ${escapeHtml(d.given_answer)}
            ${d.correct ? "" : ` · correct: ${escapeHtml(d.expected_answer)}`}
          </span>
        </li>`
      )
      .join("");

    const nextDisabled = copy.nextMode === "done" ? "disabled" : "";
    const nextLevelId =
      copy.nextMode === "retry"
        ? res.level_id || ""
        : (ctx && ctx.next && ctx.next.level_id) || "";

    return `
      <div class="card results-card ${passed ? "results-pass" : "results-retry"}">
        <div class="results-confetti" aria-hidden="true"></div>
        <p class="results-kicker">${escapeHtml(copy.kicker)}</p>
        <h1 class="results-title">${copy.title}</h1>
        <div class="results-stars" aria-label="${stars} stars">${starHtml}</div>
        ${
          passed && res.speed_badge
            ? `<div class="results-speed-badge">${speedBadgeHtml(res, "lg")}</div>`
            : ""
        }
        <div class="results-stats">
          <div class="stat-pill">
            <span class="stat-label">Score</span>
            <span class="stat-value">${res.correct}/${res.total_questions}</span>
          </div>
          <div class="stat-pill">
            <span class="stat-label">Accuracy</span>
            <span class="stat-value">${accPct}%</span>
          </div>
          <div class="stat-pill">
            <span class="stat-label">Time</span>
            <span class="stat-value">${formatDuration(res.total_elapsed_ms || 0)}</span>
          </div>
        </div>
        <p class="results-msg muted">${escapeHtml(copy.msg)}</p>
        <div class="results-next-set">
          <div class="results-next-set-row">
            <button type="button" class="btn results-retake-btn" id="retake-set"
              data-subject="${escapeAttr(res.subject_id || "")}"
              data-level="${escapeAttr(res.level_id || "")}"
              title="Retake this same question set">
              Retake
            </button>
            <button type="button" class="btn results-next-set-btn" id="next-set"
              data-subject="${escapeAttr(res.subject_id || "")}"
              data-level="${escapeAttr(res.level_id || "")}"
              data-next-level="${escapeAttr(nextLevelId || "")}"
              data-next-mode="${escapeAttr(copy.nextMode)}"
              ${nextDisabled}>
              ${escapeHtml(copy.nextLabel)}
            </button>
          </div>
          <p class="muted results-next-set-hint">${escapeHtml(copy.nextHint)}</p>
        </div>
        ${
          detailRows
            ? `<ul class="results-list">${detailRows}</ul>`
            : ""
        }
        <div class="row" style="margin-top:1rem">
          <button type="button" class="btn accent" id="end-session">Back to levels</button>
          <button type="button" class="btn secondary" data-go="insights">See insights</button>
        </div>
      </div>`;
  }

  async function viewTasks() {
    try {
      const data = await Api.listTasks(token());
      const tasks = data.tasks || [];
      const list = tasks.map((t) => `
        <div class="task-item ${t.completed ? "done" : ""}" data-id="${t.task_id}">
          <input type="checkbox" ${t.completed ? "checked" : ""} data-toggle="${t.task_id}" />
          <div class="grow">
            <div class="title">${escapeHtml(t.title)}</div>
            <div class="muted">${escapeHtml(t.description || "")}</div>
          </div>
          <button class="btn secondary" type="button" data-del="${t.task_id}" aria-label="Delete">✕</button>
        </div>`).join("");
      return `
        <div class="card">
          <h1>Tasks</h1>
          <form id="task-form" class="stack" style="margin-bottom:1rem">
            <input id="task-title" placeholder="New task title" required maxlength="200" />
            <input id="task-desc" placeholder="Description (optional)" maxlength="2000" />
            <button class="btn" type="submit">Add task</button>
          </form>
          ${list || "<p class='muted'>No tasks yet.</p>"}
        </div>`;
    } catch (e) {
      if (e.status === 402) return viewPaywall(e.message);
      return `<div class="card"><h1>Tasks</h1><p>${escapeHtml(e.message)}</p></div>`;
    }
  }

  async function viewInsights() {
    try {
      // I5: cache full insights payload; I2: notices from profile, not /insights
      let data = InsightsCache.get(null);
      if (!data) {
        data = await Api.insights(token(), { notices: false });
        InsightsCache.set(data, null);
      }
      // Banner: reuse profile notices (H2) without bloating Insights
      if (!ProfileCache.noticesLoaded()) {
        await ensureContentNotices();
      }
      const bannerNotices =
        (state.profile && state.profile.content_notices) ||
        data.content_notices ||
        [];

      const topicSummary = (data.topic_summary || [])
        .map((t) => {
          const total = t.levels_total != null ? t.levels_total : t.levels_tracked;
          const done = t.levels_completed || 0;
          const avg =
            t.avg_elapsed_ms != null ? formatDuration(t.avg_elapsed_ms) : "—";
          const allDone = Boolean(t.all_complete);
          // Badge only when every question set for the topic is completed
          const badgeOrNudge = allDone
            ? speedBadgeHtml(
                {
                  speed_badge: t.speed_badge,
                  speed_badge_label: t.speed_badge_label,
                  status: "completed",
                  best_elapsed_ms: t.avg_elapsed_ms,
                },
                "lg"
              )
            : `<p class="insights-encourage">${escapeHtml(
                t.encourage_message ||
                  "Keep practicing the remaining sets to earn your topic badge!"
              )}</p>`;
          return `
          <div class="insights-topic-card ${allDone ? "is-complete" : "is-in-progress"}">
            <div class="insights-topic-head">
              <div>
                <div class="insights-topic-label">${escapeHtml(t.subject_label || t.subject_id)}</div>
                <div class="muted">${done} of ${total != null ? total : "?"} set(s) completed</div>
              </div>
              ${allDone ? badgeOrNudge : ""}
            </div>
            ${allDone ? `<div class="muted">Avg time: ${avg}</div>` : badgeOrNudge}
          </div>`;
        })
        .join("");

      const rows = (data.progress || [])
        .map((p) => {
          const avgMs = p.avg_elapsed_ms != null ? p.avg_elapsed_ms : p.best_elapsed_ms;
          const timeStr = avgMs != null ? formatDuration(avgMs) : "—";
          const acc =
            p.best_accuracy != null ? `${Math.round(p.best_accuracy * 100)}%` : "—";
          const status = p.status || "new";
          const statusCls =
            status === "completed"
              ? "ok"
              : status === "failed"
                ? "err"
                : status === "new"
                  ? ""
                  : "warn";
          const levelLabel = escapeHtml(p.level_name || p.level_id || "—");
          const levelLink =
            p.subject_id && p.level_id
              ? `<a href="#study" class="insights-level-link"
                    data-study-subject="${escapeAttr(p.subject_id)}"
                    data-study-level="${escapeAttr(p.level_id)}"
                    data-study-category="${escapeAttr(p.category || "")}"
                    title="Open this question set in Study">${levelLabel}</a>`
              : levelLabel;
          return `
          <tr>
            <td class="insights-cell-level">${levelLink}</td>
            <td class="insights-cell-status"><span class="badge ${statusCls}">${escapeHtml(status)}</span></td>
            <td class="insights-cell-time">${timeStr}</td>
            <td class="insights-cell-acc">${acc}</td>
            <td class="insights-cell-badge">${
              status === "completed" ? speedBadgeHtml(p) : ""
            }</td>
          </tr>`;
        })
        .join("");

      // Same major-level radar as Study (Level 1..N for the primary base topic)
      const radarSubjects = (data.topic_summary || []).map((t) => ({
        subject_id: t.subject_id,
        category: t.category,
        topic: t.topic,
        name: t.topic,
      }));
      const radarHtml = performanceRadarHtml({
        subjects: radarSubjects,
        progressRows: data.progress || [],
        size: 168,
        showCaption: true,
        ariaLabel: "Performance radar by major level",
      });

      return `
        ${contentNoticesHtml(bannerNotices)}
        <div class="card insights-summary-card">
          <div class="insights-header">
            <div class="insights-header-main">
              <h1 class="insights-page-title">Insights</h1>
              <p>${escapeHtml(data.summary || "")}</p>
              <p class="muted">Completed levels: ${data.levels_completed || 0} ·
                Avg accuracy: ${data.avg_best_accuracy != null ? Math.round(data.avg_best_accuracy * 100) + "%" : "—"}</p>
            </div>
            <div class="insights-radar">
              ${radarHtml}
            </div>
          </div>
        </div>
        <div class="card">
          <h2>Subjects &amp; topics</h2>
          <p class="muted">Topic badge unlocks when you complete every question set.</p>
          ${topicSummary || "<p class='muted'>No topics yet — start studying!</p>"}
        </div>
        <div class="card insights-progress-card">
          <h2>Progress detail</h2>
          <p class="muted">All question sets. Tap a level name to open it in Study.</p>
          <div class="table-wrap insights-table-wrap">
            <table class="data-table insights-progress-table">
              <colgroup>
                <col class="col-level" />
                <col class="col-status" />
                <col class="col-time" />
                <col class="col-acc" />
                <col class="col-badge" />
              </colgroup>
              <thead>
                <tr>
                  <th>Level</th>
                  <th>Status</th>
                  <th>Time</th>
                  <th>Acc</th>
                  <th>Badge</th>
                </tr>
              </thead>
              <tbody>
                ${
                  rows ||
                  `<tr><td colspan="5" class="muted">No question sets yet</td></tr>`
                }
              </tbody>
            </table>
          </div>
        </div>`;
    } catch (e) {
      if (e.status === 402) return viewPaywall(e.message);
      return `<div class="card"><h1>Insights</h1><p>${escapeHtml(e.message)}</p></div>`;
    }
  }

  function viewPaywall(msg) {
    const cfg = window.STEM_CONFIG || {};
    return `
      <div class="card">
        <h1>Continue learning</h1>
        <p>${escapeHtml(msg || "Your trial has ended.")}</p>
        <p>Send <strong>₱${cfg.monthlyPricePhp || 99}</strong> via GCash to
          <strong>${escapeHtml(cfg.gcashMerchant || "merchant")}</strong>, then submit your reference number.</p>
        <form id="pay-form" class="stack">
          <div>
            <label for="gcash_ref">GCash reference</label>
            <input id="gcash_ref" required minlength="4" maxlength="64" />
          </div>
          <div>
            <label for="amount">Amount (PHP)</label>
            <input id="amount" type="number" step="0.01" value="${cfg.monthlyPricePhp || 99}" required />
          </div>
          <button class="btn accent block" type="submit">Submit payment proof</button>
        </form>
      </div>`;
  }

  function formatAccountDate(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return d.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return String(iso);
    }
  }

  /** Subscription / Facebook benefit blurb for Account page. */
  function accountSubscriptionHtml(p) {
    const followed = Boolean(p.facebook_followed);
    const subActive = Boolean(p.facebook_subscription_active);
    const adsMay = p.ads_may_appear !== false && !p.ad_free_active;
    const subEnd = formatAccountDate(p.facebook_subscription_ends_at || p.subscription_ends_at);
    const nextEng = formatAccountDate(p.next_engagement_due_at);
    const engDays = p.engagement_interval_days || 90;

    let statusLine;
    if (followed && subActive) {
      statusLine = `You followed MElon on Facebook — <strong>6 months free subscription</strong> is active until <strong>${escapeHtml(subEnd)}</strong>.`;
    } else if (followed) {
      statusLine = `You previously followed MElon on Facebook. Your free subscription window ended on <strong>${escapeHtml(subEnd)}</strong>. Follow again or open Facebook from the menu to renew benefits.`;
    } else {
      statusLine = `Follow the <strong>MElon Basic Education</strong> Facebook page to unlock a <strong>6 months free subscription</strong>. Open <strong>Facebook</strong> from the profile menu to get started.`;
    }

    let adsLine;
    if (!followed) {
      adsLine = `If you are <strong>not following</strong> MElon on Facebook, an <strong>ad banner may appear</strong> in a future update (ads are not shown yet).`;
    } else if (p.ad_free_active) {
      adsLine = `Ad banner stays off while you stay engaged. Please leave a Facebook <strong>comment</strong> or <strong>feature request</strong> at least every <strong>${engDays} days</strong> (about 3 months). Next check-in by <strong>${escapeHtml(nextEng)}</strong>.`;
    } else {
      adsLine = `Your last Facebook comment/feature request is overdue (every <strong>${engDays} days</strong>). An <strong>ad banner may appear</strong> in a future update until you post again via the Facebook menu. Ads are not shown yet.`;
    }

    return `
      <div class="account-subscription" style="margin-top:1rem;padding-top:0.85rem;border-top:1px solid var(--border)">
        <h2 style="font-size:1rem;margin:0 0 0.5rem">Subscription &amp; Facebook</h2>
        <p class="muted" style="margin:0.35rem 0">${statusLine}</p>
        <p class="muted" style="margin:0.35rem 0">${adsLine}</p>
        <p class="muted" style="margin:0.5rem 0 0;font-size:0.85rem">
          Study remains available to all learners. Facebook benefits are recorded when you confirm Follow or post feedback in the app after using Facebook.
        </p>
      </div>`;
  }

  async function viewAccount() {
    // Prefer cache; load notices only for the banner
    await refreshProfile({ skipIfFresh: true, notices: true });
    updateNavProfileAvatar();
    const p = state.profile || {};
    const name = learnerDisplayName(p);
    return `
      <div class="card">
        <h1>Account</h1>
        <p><strong>${escapeHtml(name || "Learner")}</strong></p>
        <p class="muted">${escapeHtml(p.email || "")}</p>
        ${Auth.isAdmin() ? `<p><span class="badge ok">Administrator</span></p>` : ""}
        <p class="muted" style="margin-top:0.75rem">Study is free. Profiles inactive for 6 months may be removed to save resources.</p>
        ${accountSubscriptionHtml(p)}
      </div>
      ${contentNoticesHtml(p.content_notices)}`;
  }

  /** Profile page — editable Name, School, Grade + XP / Rank. */
  async function viewProfile() {
    await refreshProfile({ force: true, notices: false });
    updateNavProfileAvatar();
    const p = state.profile || {};
    const name = learnerDisplayName(p);
    const initials = learnerInitials(p);
    const photo = String(p.avatar_url || p.photo_url || "").trim();
    const avatarInner = photo
      ? `<img src="${escapeAttr(photo)}" alt="${escapeAttr(name || "Profile")}" />`
      : initials
        ? `<span class="profile-avatar-initials">${escapeHtml(initials)}</span>`
        : `<svg class="profile-avatar-icon" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="8" r="3.5" fill="currentColor"/>
            <path fill="currentColor" d="M5.5 19.2c.6-3.2 3.2-5.2 6.5-5.2s5.9 2 6.5 5.2c.1.5-.3 1-.8 1H6.3c-.5 0-.9-.5-.8-1z"/>
          </svg>`;

    const xpVal = p.xp != null ? Number(p.xp) : 0;
    const rankVal = p.rank != null ? Number(p.rank) : null;
    const rankLabel = rankVal != null && Number.isFinite(rankVal) ? `#${rankVal}` : "—";

    let schools = [];
    try {
      const data = await Api.listSchools(token());
      schools = data.schools || [];
    } catch {
      schools = [];
    }
    // Keep a pending-requested school visible even though it's not in the public catalog yet
    const currentSchoolId = p.school_id || "";
    if (
      currentSchoolId &&
      !schools.some((s) => s.school_id === currentSchoolId) &&
      (p.school_name || currentSchoolId)
    ) {
      schools = [
        {
          school_id: currentSchoolId,
          name: p.school_name || currentSchoolId,
          label: p.school_name || currentSchoolId,
          pending: true,
        },
        ...schools,
      ];
    }
    const schoolOpts = schoolSelectOptionsHtml(schools, currentSchoolId);

    return `
      <div class="card">
        <div class="profile-header">
          <div class="profile-header-main">
            <h1>Profile</h1>
            <div class="profile-avatar-lg" aria-hidden="true">${avatarInner}</div>
            <p class="muted" style="margin-bottom:0.85rem">${escapeHtml(p.email || "")}</p>
          </div>
          <div class="profile-xp-card" aria-label="Leaderboard stats">
            <div class="profile-xp-stat">
              <span class="profile-xp-label">XP</span>
              <span class="profile-xp-value">${escapeHtml(String(Number.isFinite(xpVal) ? xpVal : 0))}</span>
            </div>
            <div class="profile-xp-stat">
              <span class="profile-xp-label">Rank</span>
              <span class="profile-xp-value">${escapeHtml(rankLabel)}</span>
            </div>
          </div>
        </div>
        <form id="profile-form" class="stack">
          <div>
            <label for="profile-nickname">Name / Nickname</label>
            <input id="profile-nickname" type="text" maxlength="40" required
              value="${escapeAttr(p.nickname || p.display_name || "")}"
              placeholder="How should we call you?" />
          </div>
          <div>
            <label for="profile-school">School</label>
            <select id="profile-school">${schoolOpts}</select>
            ${
              p.school_name && String(p.school_name).toLowerCase().includes("pending")
                ? `<p class="muted school-request-hint">Awaiting admin approval for your school request.</p>`
                : ""
            }
          </div>
          <div>
            <label for="profile-grade">Grade</label>
            <select id="profile-grade">${gradeSelectOptionsHtml(p.grade || "")}</select>
          </div>
          <button class="btn block" type="submit" id="profile-save">Save</button>
        </form>
      </div>`;
  }

  async function viewAdmin() {
    if (!Auth.isAdmin()) {
      return `<div class="card"><h1>Admin</h1><p class="muted">You do not have admin privileges.</p></div>`;
    }

    let subjects = [];
    try {
      const data = await Api.listSubjects(token());
      subjects = data.subjects || [];
    } catch (e) {
      const detail = e && e.message ? e.message : String(e);
      return `<div class="card">
        <h1>Admin</h1>
        <p class="muted">Could not load subjects.</p>
        <p class="muted">${escapeHtml(detail)}</p>
        <p class="muted">If you just imported a large Excel file, wait a few seconds and open Admin again. Check the import log for per-sheet errors.</p>
        <button type="button" class="btn" id="admin-retry-load">Retry</button>
      </div>`;
    }

    if (!state.adminSubjectId && subjects.length) {
      state.adminSubjectId = subjects[0].subject_id;
    }
    const subjectId = state.adminSubjectId;

    let levels = [];
    if (subjectId) {
      try {
        const lv = await Api.listLevels(token(), subjectId);
        levels = lv.levels || [];
      } catch (e) {
        levels = [];
      }
    }
    if (state.adminLevelId && !levels.some((l) => l.level_id === state.adminLevelId)) {
      state.adminLevelId = null;
    }
    if (!state.adminLevelId && levels.length) {
      state.adminLevelId = levels[0].level_id;
    }
    const levelId = state.adminLevelId;

    let questions = [];
    if (subjectId && levelId) {
      try {
        const q = await Api.listQuestions(token(), subjectId, levelId, true);
        questions = q.questions || [];
      } catch (_) {
        questions = [];
      }
    }

    const workingSubject = subjects.find((s) => s.subject_id === subjectId) || null;
    const workingLabel = subjectDisplayLabel(workingSubject);

    const subjectOptions = subjects
      .map(
        (s) =>
          `<option value="${escapeHtml(s.subject_id)}" ${
            s.subject_id === subjectId ? "selected" : ""
          }>${escapeHtml(subjectDisplayLabel(s))}</option>`
      )
      .join("");

    const levelOptions = levels
      .map(
        (l) =>
          `<option value="${escapeHtml(l.level_id)}" ${
            l.level_id === levelId ? "selected" : ""
          }>${escapeHtml(l.name)} · order ${l.order} · ${l.question_count || 0} Q</option>`
      )
      .join("");

    const levelRows = levels
      .map(
        (l) => `
        <tr data-level-row="${escapeHtml(l.level_id)}" data-subject="${escapeHtml(
          l.subject_id || subjectId || ""
        )}">
          <td><span class="badge subject-tag" title="Working subject">${escapeHtml(
            l.subject_label || workingLabel || subjectId || "—"
          )}</span></td>
          <td><code>${escapeHtml(l.level_id)}</code></td>
          <td class="lvl-name-cell">${escapeHtml(l.name)}</td>
          <td>${l.order}</td>
          <td>${Math.round((l.pass_accuracy || 0.8) * 100)}%</td>
          <td>${l.question_count || 0}</td>
          <td class="row-actions">
            <button type="button" class="btn-icon secondary" data-admin-select-level="${escapeHtml(
              l.level_id
            )}" title="Manage questions" aria-label="Manage questions">${iconGear()}</button>
            <button type="button" class="btn-icon secondary" data-admin-edit-level="${encodeURIComponent(
              JSON.stringify({
                level_id: l.level_id,
                name: l.name,
                description: l.description || "",
                order: l.order,
                pass_accuracy: l.pass_accuracy,
                min_questions: l.min_questions,
              })
            )}" title="Edit level" aria-label="Edit level">${iconPencil()}</button>
            <button type="button" class="btn-icon danger" data-admin-delete-level="${escapeHtml(
              l.level_id
            )}" title="Delete level" aria-label="Delete level">${iconTrash()}</button>
          </td>
        </tr>`
      )
      .join("");

    const questionRows = questions
      .map(
        (q, i) => `
        <tr data-qid="${escapeHtml(q.question_id)}" data-subject="${escapeHtml(
          q.subject_id || subjectId || ""
        )}">
          <td>${i + 1}</td>
          <td><span class="badge subject-tag" title="Working subject">${escapeHtml(
            q.subject_label || workingLabel || subjectId || "—"
          )}</span></td>
          <td><code>${escapeHtml(q.question_id)}</code></td>
          <td>
            <input class="inline-input q-prompt" value="${escapeAttr(q.prompt)}" />
          </td>
          <td>
            <input class="inline-input q-answer" value="${escapeAttr(q.answer ?? "")}" />
          </td>
          <td class="row-actions">
            <button type="button" class="btn-icon secondary" data-admin-save-q="${escapeHtml(
              q.question_id
            )}" title="Save question" aria-label="Save question">${iconSave()}</button>
            <button type="button" class="btn-icon danger" data-admin-delete-q="${escapeHtml(
              q.question_id
            )}" title="Delete question" aria-label="Delete question">${iconTrash()}</button>
          </td>
        </tr>`
      )
      .join("");

    const activeCategory = (workingSubject && workingSubject.category) || "Mathematics";
    const categoryOptions = ["Science", "Technology", "Engineering", "Mathematics"]
      .map(
        (c) =>
          `<option value="${escapeHtml(c)}" ${
            c === activeCategory ? "selected" : ""
          }>${escapeHtml(c)}</option>`
      )
      .join("");

    const formTopic = workingSubject
      ? workingSubject.topic || workingSubject.name || ""
      : "";
    const formDesc = workingSubject ? workingSubject.description || "" : "";
    const formOrder =
      workingSubject && workingSubject.sort_order != null
        ? workingSubject.sort_order
        : 1;
    const formGradeLevel = workingSubject
      ? workingSubject.grade_level || ""
      : "";

    let schools = [];
    try {
      const sch =
        typeof Api.listSchoolsAdmin === "function"
          ? await Api.listSchoolsAdmin(token())
          : await Api.listSchools(token());
      schools = sch.schools || [];
    } catch {
      schools = [];
    }
    const schoolRows = schools
      .map((s) => {
        const pending = Boolean(s.pending || s.status === "pending");
        const statusCell = pending
          ? `<span class="badge badge-pending">Pending</span>`
          : `<span class="badge badge-active">Active</span>`;
        const approveBtn = pending
          ? `<button type="button" class="btn-icon accent" data-admin-approve-school="${escapeAttr(
              s.school_id
            )}" title="Approve school request" aria-label="Approve school request">✓</button>`
          : "";
        const requester = s.requester_email
          ? `<div class="muted" style="font-size:0.8rem">${escapeHtml(s.requester_email)}</div>`
          : "";
        return `
        <tr data-school-id="${escapeAttr(s.school_id)}" class="${pending ? "row-pending" : ""}">
          <td>${escapeHtml(s.name || "")}${requester}</td>
          <td>${escapeHtml(s.city || "—")}</td>
          <td>${escapeHtml(s.province || "—")}</td>
          <td>${statusCell}</td>
          <td class="row-actions">
            ${approveBtn}
            <button type="button" class="btn-icon secondary" data-admin-edit-school="${encodeURIComponent(
              JSON.stringify({
                school_id: s.school_id,
                name: s.name,
                city: s.city || "",
                province: s.province || "",
              })
            )}" title="Edit school" aria-label="Edit school">${iconPencil()}</button>
            <button type="button" class="btn-icon danger" data-admin-delete-school="${escapeAttr(
              s.school_id
            )}" title="Delete school" aria-label="Delete school">${iconTrash()}</button>
          </td>
        </tr>`;
      })
      .join("");

    return `
      <div class="card">
        <h1>Admin · Content</h1>
        <p class="muted">Manage subjects by STEM category and topic, then levels and question banks via CSV or multi-sheet Excel.</p>
        <div class="row" style="margin-top:0.75rem">
          <button type="button" class="btn secondary" id="admin-seed">Seed Math defaults</button>
        </div>
      </div>

      <div class="card">
        <h2>Schools</h2>
        <p class="muted">Schools appear in Sign up and Profile. Location is City and Province.
          Learner requests show as <strong>Pending</strong> — approve to activate and update their profile.</p>
        <form id="admin-school-form" class="stack">
          <input type="hidden" id="school-edit-id" value="" />
          <div>
            <label for="school-name">School name</label>
            <input id="school-name" required maxlength="120" placeholder="e.g. Rizal Elementary School" />
          </div>
          <div class="row">
            <div class="grow">
              <label for="school-city">City</label>
              <input id="school-city" maxlength="80" placeholder="e.g. Cebu City" />
            </div>
            <div class="grow">
              <label for="school-province">Province</label>
              <input id="school-province" maxlength="80" placeholder="e.g. Cebu" />
            </div>
          </div>
          <div class="row">
            <button class="btn" type="submit" id="admin-school-save">Add school</button>
            <button class="btn secondary hidden" type="button" id="admin-school-cancel">Cancel edit</button>
          </div>
        </form>
        <div class="table-wrap" style="margin-top:1rem">
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>City</th>
                <th>Province</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              ${
                schoolRows ||
                `<tr><td colspan="5" class="muted">No schools yet. Add one above.</td></tr>`
              }
            </tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h2>Subjects</h2>
        <form id="admin-subject-form" class="stack">
          <div class="row">
            <div class="grow">
              <label for="subj-category">Category</label>
              <select id="subj-category" required>${categoryOptions}</select>
            </div>
            <div class="grow">
              <label for="subj-topic">Topic</label>
              <input id="subj-topic" required maxlength="100" placeholder="e.g. Addition, Algebra"
                value="${escapeAttr(formTopic)}" />
            </div>
          </div>
          <div class="row">
            <div class="grow">
              <label for="subj-grade-level">Grade level</label>
              <select id="subj-grade-level">
                ${gradeSelectOptionsHtml(formGradeLevel)}
              </select>
            </div>
            <div class="grow">
              <label for="subj-desc">Description</label>
              <input id="subj-desc" maxlength="2000" placeholder="Optional"
                value="${escapeAttr(formDesc)}" />
            </div>
          </div>
          <div class="row" style="align-items:flex-end">
            <div>
              <label for="subj-order">Sort order</label>
              <input id="subj-order" type="number" min="0" max="10000" value="${formOrder}" />
            </div>
            <button class="btn" type="submit" id="admin-subject-add">Add subject</button>
            <button class="btn secondary" type="button" id="admin-subject-edit" ${
              subjectId ? "" : "disabled"
            }>Edit Subject</button>
          </div>
          <p class="muted" style="margin:0">Working subject is shown as <strong>Category – Topic</strong> (e.g. Mathematics – Addition). Set <strong>Grade level</strong> (Kindergarten or Grade&nbsp;1–12) so Study shows it next to Category. Use <strong>Edit Subject</strong> to update the selected working subject.</p>
        </form>
        <div class="row" style="margin-top:1rem">
          <div class="grow">
            <label for="admin-subject-select">Working subject</label>
            <select id="admin-subject-select">${subjectOptions || "<option value=''>No subjects yet</option>"}</select>
          </div>
        </div>
      </div>

      <div class="card ${subjectId ? "" : "hidden"}">
        <h2>Levels · <span class="muted">${escapeHtml(workingLabel || subjectId || "")}</span></h2>
        <form id="admin-level-form" class="stack">
          <div class="row">
            <div class="grow">
              <label for="lvl-id">Level ID</label>
              <input id="lvl-id" required pattern="[a-zA-Z0-9_-]{1,64}" placeholder="e.g. l4" maxlength="64" />
            </div>
            <div class="grow">
              <label for="lvl-name">Name</label>
              <input id="lvl-name" required maxlength="100" placeholder="Level 4 – Multiplication" />
            </div>
          </div>
          <div>
            <label for="lvl-desc">Description</label>
            <input id="lvl-desc" maxlength="2000" placeholder="Optional" />
          </div>
          <div class="row">
            <div>
              <label for="lvl-order">Order (unlock sequence)</label>
              <input id="lvl-order" type="number" min="1" max="1000" value="${
                levels.length ? Math.max(...levels.map((l) => l.order || 0)) + 1 : 1
              }" required />
            </div>
            <div>
              <label for="lvl-pass">Pass accuracy (0–1)</label>
              <input id="lvl-pass" type="number" min="0" max="1" step="0.05" value="0.8" required />
            </div>
            <div>
              <label for="lvl-minq">Min questions</label>
              <input id="lvl-minq" type="number" min="1" max="500" value="5" required />
            </div>
            <button class="btn" type="submit" style="align-self:flex-end">Add level</button>
          </div>
        </form>
        <div class="table-wrap" style="margin-top:1rem">
          <table class="data-table">
            <thead>
              <tr><th>Subject</th><th>ID</th><th>Name</th><th>Order</th><th>Pass</th><th>Q</th><th>Actions</th></tr>
            </thead>
            <tbody>
              ${levelRows || `<tr><td colspan="7" class="muted">No levels yet for this subject.</td></tr>`}
            </tbody>
          </table>
        </div>
        <div id="admin-level-edit" class="stack hidden" style="margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border)">
          <h3>Edit level <span id="edit-lvl-id-label" class="muted"></span></h3>
          <input type="hidden" id="edit-lvl-id" />
          <div class="row">
            <div class="grow">
              <label for="edit-lvl-name">Name</label>
              <input id="edit-lvl-name" maxlength="100" required />
            </div>
            <div class="grow">
              <label for="edit-lvl-desc">Description</label>
              <input id="edit-lvl-desc" maxlength="2000" />
            </div>
          </div>
          <div class="row">
            <div>
              <label for="edit-lvl-order">Order</label>
              <input id="edit-lvl-order" type="number" min="1" max="1000" />
            </div>
            <div>
              <label for="edit-lvl-pass">Pass accuracy</label>
              <input id="edit-lvl-pass" type="number" min="0" max="1" step="0.05" />
            </div>
            <div>
              <label for="edit-lvl-minq">Min questions</label>
              <input id="edit-lvl-minq" type="number" min="1" max="500" />
            </div>
            <button type="button" class="btn" id="admin-level-save" style="align-self:flex-end">Save level</button>
            <button type="button" class="btn secondary" id="admin-level-edit-cancel" style="align-self:flex-end">Cancel</button>
          </div>
        </div>
      </div>

      <div class="card ${subjectId ? "" : "hidden"}">
        <h2>Import · <span class="muted">${escapeHtml(workingLabel || subjectId || "")}</span></h2>

        <div class="import-block">
          <h3>Excel (multiple levels)</h3>
          <p class="muted">
            Each <strong>worksheet</strong> becomes one level. The <strong>sheet name</strong> is the level name
            (e.g. sheet <code>Level1-0</code> → level &quot;Level1-0&quot;). Rows use the same formats as CSV:
            <code>1,+,2,=,3</code> or <code>1+2,3</code>. Each sheet is imported as one question set (replace mode).
            Tag the working subject’s <strong>Grade level</strong> below (or in Subjects) before/after import.
          </p>
          <form id="admin-excel-form" class="stack">
            <div>
              <label for="excel-file">Upload Excel (.xlsx / .xls)</label>
              <input id="excel-file" type="file" accept=".xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel" />
            </div>
            <div>
              <label for="excel-grade-level">Grade level (for this topic)</label>
              <select id="excel-grade-level">
                ${gradeSelectOptionsHtml(formGradeLevel)}
              </select>
            </div>
            <label class="check-row">
              <input type="checkbox" id="excel-replace" checked />
              Replace questions on existing levels that match sheet names
            </label>
            <button class="btn accent" type="submit" id="admin-excel-submit">Import Excel workbook</button>
          </form>
          <div id="excel-import-log" class="muted import-log hidden"></div>
        </div>

        <div class="import-block" style="margin-top:1.25rem">
          <h3>CSV (single level)</h3>
          <p class="muted">
            Import into the <strong>working level</strong> below. Formats:
            <code>1,+,2,=,3</code> or <code>1+2,3</code> (one question per line).
          </p>
          <div class="row">
            <div class="grow">
              <label for="admin-level-select">Working level</label>
              <select id="admin-level-select">${levelOptions || "<option value=''>No levels — create one or import Excel</option>"}</select>
            </div>
          </div>
          <form id="admin-csv-form" class="stack" style="margin-top:0.75rem">
            <div>
              <label for="csv-file">Upload CSV file</label>
              <input id="csv-file" type="file" accept=".csv,text/csv,text/plain" />
            </div>
            <div>
              <label for="csv-text">Or paste CSV</label>
              <textarea id="csv-text" rows="8" placeholder="1,+,2,=,3&#10;4,+,5,=,9&#10;2,+,3,=,5"></textarea>
            </div>
            <label class="check-row">
              <input type="checkbox" id="csv-replace" />
              Replace existing questions on this level (clear set before import)
            </label>
            <button class="btn accent" type="submit" id="admin-csv-submit">Import CSV</button>
          </form>
        </div>

        <div class="row" style="margin-top:1rem">
          <button type="button" class="btn danger" id="admin-clear-questions" ${levelId ? "" : "disabled"}>Delete all questions on this level</button>
          <button type="button" class="btn danger" id="admin-delete-level-full" ${levelId ? "" : "disabled"}>Delete entire level</button>
        </div>

        <h3 style="margin-top:1.25rem">Current questions${
          levelId
            ? ` · ${escapeHtml(levelId)} · ${escapeHtml(workingLabel || subjectId || "")}`
            : ""
        } (${questions.length})</h3>
        ${
          levelId
            ? `<div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr><th>#</th><th>Subject</th><th>ID</th><th>Prompt</th><th>Answer</th><th>Actions</th></tr>
            </thead>
            <tbody>
              ${
                questionRows ||
                `<tr><td colspan="6" class="muted">No questions yet. Import CSV or Excel above.</td></tr>`
              }
            </tbody>
          </table>
        </div>`
            : `<p class="muted">Select or create a level to view and edit questions.</p>`
        }
      </div>`;
  }

  /** Display format for working subject: Category - Topic */
  function subjectDisplayLabel(s) {
    if (!s) return "";
    if (s.label) return s.label;
    const category = s.category || "";
    const topic = s.topic || s.name || "";
    if (category && topic) return `${category} - ${topic}`;
    return topic || s.subject_id || "";
  }

  const SPEED_BADGE_ASSETS = {
    cool_novice: {
      src: "assets/badges/badge_cool_novice.jpg",
      label: "Cool Novice",
      title: "Cool Novice — finished in 2 minutes or more",
    },
    superb_advanced: {
      src: "assets/badges/badge_superb_advanced.jpg",
      label: "Superb Advanced",
      title: "Superb Advanced — more than 30 seconds and under 2 minutes",
    },
    legendary_wizard: {
      src: "assets/badges/badge_legendary_wizard.jpg",
      label: "Legendary Wizard",
      title: "Legendary Wizard — finished in 30 seconds or less",
    },
  };

  /** Derive badge id from completion ms (mirrors backend thresholds). */
  function speedBadgeFromElapsedMs(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return null;
    const n = Math.max(0, Number(ms));
    if (n <= 30000) return "legendary_wizard";
    if (n < 120000) return "superb_advanced";
    return "cool_novice";
  }

  function resolveSpeedBadgeId(row) {
    if (!row) return null;
    if (row.speed_badge && SPEED_BADGE_ASSETS[row.speed_badge]) return row.speed_badge;
    // Backfill for progress completed before badges were stored
    if (row.status && row.status !== "completed" && row.status !== "failed") {
      // only show for completed (or when time exists on completed topic summary)
    }
    const ms =
      row.best_elapsed_ms != null
        ? row.best_elapsed_ms
        : row.avg_elapsed_ms != null
          ? row.avg_elapsed_ms
          : row.last_elapsed_ms != null
            ? row.last_elapsed_ms
            : row.total_elapsed_ms != null
              ? row.total_elapsed_ms
              : null;
    if (ms == null) return null;
    // Topic summary may omit status; still show best badge from avg time
    if (row.status && row.status !== "completed" && !row.levels_completed) return null;
    return speedBadgeFromElapsedMs(ms);
  }

  /** Kid-friendly speed badge image next to status. */
  function speedBadgeHtml(row, size) {
    if (!row) return "";
    const badgeId = resolveSpeedBadgeId(row);
    if (!badgeId) return "";
    const meta = SPEED_BADGE_ASSETS[badgeId];
    if (!meta) return "";
    const label = row.speed_badge_label || meta.label;
    const cls = size === "lg" ? "speed-badge speed-badge-lg" : "speed-badge";
    // Absolute path from site root so nested routes / cache busting never break images
    const src = meta.src.startsWith("/") ? meta.src : `/${meta.src}`;
    return `<span class="${cls}" title="${escapeAttr(meta.title)}">
      <img src="${src}" alt="${escapeAttr(label)}" class="speed-badge-img" width="40" height="40"
        loading="eager" decoding="async"
        onerror="this.onerror=null;this.classList.add('is-broken');const f=this.parentElement&&this.parentElement.querySelector('.speed-badge-fallback');if(f)f.classList.add('is-visible');" />
      <span class="speed-badge-fallback" aria-hidden="true">🏅</span>
      <span class="speed-badge-label">${escapeHtml(label)}</span>
    </span>`;
  }

  /* ---------- Performance radar (shared: Study, Insights, future pages) ---------- */

  /**
   * Time component for radar score (mirrors badge thresholds):
   * ≤30s → 1, ≥120s → 0, linear between.
   */
  function timeScoreFromElapsedMs(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return 0;
    const n = Math.max(0, Number(ms));
    if (n <= 30000) return 1;
    if (n >= 120000) return 0;
    return 1 - (n - 30000) / 90000;
  }

  /**
   * Combined performance score: 55% accuracy + 45% speed.
   * Unstarted / no data → 0.
   */
  function performanceScoreFromProgress(pr) {
    if (!pr) return 0;
    const status = pr.status || "";
    if (status === "new" || status === "") {
      if (pr.best_accuracy == null && pr.best_elapsed_ms == null && pr.avg_elapsed_ms == null) {
        return 0;
      }
    }
    let acc = pr.best_accuracy != null ? Number(pr.best_accuracy) : null;
    if (acc == null) {
      if (status === "completed") acc = 1;
      else return 0;
    }
    acc = Math.max(0, Math.min(1, acc));
    const ms =
      pr.best_elapsed_ms != null
        ? pr.best_elapsed_ms
        : pr.avg_elapsed_ms != null
          ? pr.avg_elapsed_ms
          : null;
    const tScore = ms != null ? timeScoreFromElapsedMs(ms) : status === "completed" ? 0.5 : 0;
    let score = 0.55 * acc + 0.45 * tScore;
    if (status === "failed") score *= 0.85;
    else if (status === "in_progress") score *= 0.7;
    return Math.max(0, Math.min(1, score));
  }

  /**
   * Base topic name: strip trailing " - Level N" so all major-level subjects
   * under Arithmetic (Addition) group together.
   * "Arithmetic (Addition) - Level 3" → "Arithmetic (Addition)"
   */
  function baseTopicName(topicOrName) {
    const raw = String(topicOrName || "").trim();
    if (!raw) return "";
    return raw.replace(/\s*[-–]\s*Level\s+\d+\s*$/i, "").trim() || raw;
  }

  /**
   * Major level number from subject topic / id / set name.
   * "Arithmetic (Addition) - Level 3" → 3
   * "mathematics-arithmetic-addition-level-3" → 3
   * "Level 3-1" → 3
   */
  function parseMajorLevel(...candidates) {
    for (const c of candidates) {
      if (c == null || c === "") continue;
      const s = String(c);
      let m = s.match(/(?:^|[\s\-–_])Level\s+(\d+)\s*$/i);
      if (m) return parseInt(m[1], 10);
      m = s.match(/Level\s+(\d+)\s*[-–.]\s*\d+/i);
      if (m) return parseInt(m[1], 10);
      m = s.match(/level[_-]?(\d+)\s*$/i);
      if (m) return parseInt(m[1], 10);
      m = s.match(/level[_-]?(\d+)/i);
      if (m) return parseInt(m[1], 10);
    }
    return null;
  }

  /**
   * Build major-level buckets for one base topic.
   * Each bucket = one radar axis (Level 1, Level 2, …).
   *
   * subjects: catalog rows { subject_id, category, topic, name }
   * progressRows: set-level progress { subject_id, status, best_accuracy, best_elapsed_ms, … }
   * focus: { subjectId } | { category, baseTopic } to select which topic group
   */
  function majorLevelBucketsForTopic(subjects, progressRows, focus = {}) {
    const all = subjects || [];
    let category = focus.category || null;
    let baseTopic = focus.baseTopic || null;

    if (focus.subjectId) {
      const sel = all.find((s) => s.subject_id === focus.subjectId);
      if (sel) {
        category = category || sel.category || "Mathematics";
        baseTopic = baseTopic || baseTopicName(sel.topic || sel.name || "");
      }
    }

    // Subjects that belong to this base topic (e.g. all 6 Addition levels)
    let group = all.filter((s) => {
      const sc = s.category || "Mathematics";
      const sb = baseTopicName(s.topic || s.name || "");
      if (category && sc !== category) return false;
      if (baseTopic && sb !== baseTopic) return false;
      return true;
    });

    // Insights with no focus: pick the largest base-topic group
    if (!baseTopic && !focus.subjectId) {
      const tallies = new Map();
      for (const s of all) {
        const key = `${s.category || "Mathematics"}|${baseTopicName(s.topic || s.name || "")}`;
        if (!tallies.has(key)) tallies.set(key, []);
        tallies.get(key).push(s);
      }
      let best = [];
      for (const list of tallies.values()) {
        if (list.length > best.length) best = list;
      }
      group = best.length ? best : all;
      if (group[0]) {
        category = group[0].category || "Mathematics";
        baseTopic = baseTopicName(group[0].topic || group[0].name || "");
      }
    }

    const buckets = new Map(); // majorLevel → { majorLevel, subjectIds, progressRows }
    const ensure = (maj) => {
      if (!buckets.has(maj)) {
        buckets.set(maj, {
          majorLevel: maj,
          subjectIds: new Set(),
          progressRows: [],
          baseTopic: baseTopic || "",
          category: category || "",
        });
      }
      return buckets.get(maj);
    };

    const sidToMajor = new Map();
    for (const s of group) {
      const maj = parseMajorLevel(s.topic, s.name, s.subject_id);
      if (maj == null) continue;
      ensure(maj).subjectIds.add(s.subject_id);
      sidToMajor.set(s.subject_id, maj);
    }

    for (const row of progressRows || []) {
      let maj = sidToMajor.get(row.subject_id);
      if (maj == null) {
        // Only accept rows for subjects in this group
        if (sidToMajor.size && !sidToMajor.has(row.subject_id)) continue;
        maj = parseMajorLevel(
          row.topic,
          row.subject_label,
          row.level_name,
          row.level_id,
          row.subject_id
        );
      }
      if (maj == null) continue;
      if (sidToMajor.size && !sidToMajor.has(row.subject_id) && baseTopic) {
        const rowBase = baseTopicName(row.topic || row.subject_label || "");
        if (rowBase && rowBase !== baseTopic) continue;
      }
      ensure(maj).progressRows.push(row);
    }

    // Ensure every major-level subject appears even with zero progress rows
    for (const maj of sidToMajor.values()) ensure(maj);

    return {
      category: category || "",
      baseTopic: baseTopic || "",
      buckets: [...buckets.values()].sort((a, b) => a.majorLevel - b.majorLevel),
    };
  }

  /**
   * Convert major-level buckets → radar axes (dynamic vertex count).
   * Axis score = mean of accuracy+time scores across question sets in that level.
   */
  function buildMajorLevelRadarAxes(bucketsOrResult) {
    const buckets = Array.isArray(bucketsOrResult)
      ? bucketsOrResult
      : (bucketsOrResult && bucketsOrResult.buckets) || [];
    return buckets
      .filter((b) => b && b.majorLevel != null)
      .map((b) => {
        const rows = b.progressRows || [];
        let value = 0;
        let accSum = 0;
        let accN = 0;
        let timeSum = 0;
        let timeN = 0;
        let done = 0;
        if (rows.length) {
          let sum = 0;
          for (const r of rows) {
            sum += performanceScoreFromProgress(r);
            if (r.best_accuracy != null) {
              accSum += Number(r.best_accuracy);
              accN += 1;
            }
            const ms =
              r.best_elapsed_ms != null
                ? r.best_elapsed_ms
                : r.avg_elapsed_ms != null
                  ? r.avg_elapsed_ms
                  : null;
            if (ms != null) {
              timeSum += Number(ms);
              timeN += 1;
            }
            if (r.status === "completed") done += 1;
          }
          value = sum / rows.length;
        } else if (typeof b.value === "number") {
          value = Math.max(0, Math.min(1, b.value));
        }
        const accStr = accN ? `${Math.round((accSum / accN) * 100)}%` : "—";
        const timeStr = timeN ? formatDuration(timeSum / timeN) : "—";
        const maj = b.majorLevel;
        return {
          label: String(maj),
          value: Math.max(0, Math.min(1, value)),
          title: `Level ${maj}: ${done}/${rows.length || 0} sets · avg accuracy ${accStr} · avg time ${timeStr} · score ${Math.round(value * 100)}%`,
        };
      });
  }

  /**
   * Pure SVG spider/radar chart. axes: [{ label, value 0–1, title? }]
   * Vertex count is dynamic (e.g. 6 levels → hexagon).
   */
  function radarChartSvg(axes, options = {}) {
    const list = (axes || []).filter(Boolean);
    if (!list.length) {
      return `<div class="radar-empty muted" title="Complete a set to fill the radar">No data yet</div>`;
    }
    const size = options.size || 168;
    const pad = options.pad != null ? options.pad : list.length > 8 ? 32 : 28;
    const cx = size / 2;
    const cy = size / 2;
    const rMax = size / 2 - pad;
    const n = list.length;
    const rings = [0.25, 0.5, 0.75, 1];

    const angleAt = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const pt = (i, t) => {
      const a = angleAt(i);
      const rr = rMax * Math.max(0, Math.min(1, t));
      return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
    };

    const ringPolys = rings
      .map((t) => {
        const pts = Array.from({ length: n }, (_, i) => pt(i, t).join(",")).join(" ");
        return `<polygon class="radar-ring" points="${pts}" />`;
      })
      .join("");

    const spokes = Array.from({ length: n }, (_, i) => {
      const [x, y] = pt(i, 1);
      return `<line class="radar-spoke" x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" />`;
    }).join("");

    const dataPts = list.map((ax, i) => pt(i, Number(ax.value) || 0));
    const dataPoly = dataPts.map((p) => p.join(",")).join(" ");
    const dots = dataPts
      .map(([x, y], i) => {
        const title = escapeAttr(
          list[i].title || `${list[i].label}: ${Math.round((list[i].value || 0) * 100)}%`
        );
        return `<circle class="radar-dot" cx="${x}" cy="${y}" r="3.2"><title>${title}</title></circle>`;
      })
      .join("");

    const showEvery = n > 12 ? 2 : 1;
    const labels = list
      .map((ax, i) => {
        if (i % showEvery !== 0 && i !== 0) return "";
        const a = angleAt(i);
        const [lx, ly] = pt(i, 1.2);
        const anchor =
          Math.abs(Math.cos(a)) < 0.15 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
        const dy = Math.sin(a) > 0.55 ? 4 : Math.sin(a) < -0.55 ? -2 : 3;
        return `<text class="radar-label" x="${lx}" y="${ly + dy}" text-anchor="${anchor}">${escapeHtml(
          ax.label
        )}</text>`;
      })
      .join("");

    const aria = escapeAttr(
      options.ariaLabel ||
        `Performance radar: ${list
          .map((a) => `Level ${a.label} ${Math.round((a.value || 0) * 100)} percent`)
          .join(", ")}`
    );

    return `<svg class="radar-chart" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}"
      role="img" aria-label="${aria}">
      <g class="radar-grid">${ringPolys}${spokes}</g>
      <polygon class="radar-area" points="${dataPoly}" />
      <polygon class="radar-outline" points="${dataPoly}" />
      ${dots}
      ${labels}
    </svg>`;
  }

  /**
   * Common radar HTML for any page.
   * Prefer: performanceRadarHtml({ subjects, progressRows, focus, size, ariaLabel })
   * Or pass pre-built axes: performanceRadarHtml({ axes, size, ariaLabel })
   */
  function performanceRadarHtml(input = {}) {
    let axes = input.axes;
    let meta = { baseTopic: "", category: "" };
    if (!axes) {
      const grouped = majorLevelBucketsForTopic(
        input.subjects || [],
        input.progressRows || [],
        input.focus || {}
      );
      meta = { baseTopic: grouped.baseTopic, category: grouped.category };
      axes = buildMajorLevelRadarAxes(grouped);
    }
    const topicLabel = meta.baseTopic || input.topicLabel || "topic";
    const svg = radarChartSvg(axes, {
      size: input.size || 168,
      pad: input.pad,
      ariaLabel:
        input.ariaLabel ||
        `Performance radar for ${topicLabel}: levels ${axes.map((a) => a.label).join(", ")}`,
    });
    const caption =
      input.showCaption && meta.baseTopic
        ? `<div class="radar-caption muted">${escapeHtml(meta.baseTopic)}</div>`
        : "";
    return `<div class="radar-wrap" data-radar-topic="${escapeAttr(meta.baseTopic || "")}">${svg}${caption}</div>`;
  }

  /**
   * Load set-level progress rows for subjects (includes unstarted as status "new").
   * Used when Insights payload is unavailable.
   */
  async function loadProgressRowsForSubjects(tok, subjectsList) {
    const rows = [];
    await Promise.all(
      (subjectsList || []).map(async (s) => {
        if (!s || !s.subject_id) return;
        try {
          const [lvRes, progRes] = await Promise.all([
            Api.listLevels(tok, s.subject_id),
            Api.getProgress(tok, s.subject_id),
          ]);
          const pmap = Object.fromEntries(
            (progRes.progress || []).map((p) => [p.level_id, p])
          );
          for (const lv of lvRes.levels || []) {
            const pr = pmap[lv.level_id];
            rows.push({
              subject_id: s.subject_id,
              level_id: lv.level_id,
              level_name: lv.name,
              topic: s.topic || s.name,
              category: s.category,
              status: (pr && pr.status) || "new",
              best_accuracy: pr && pr.best_accuracy,
              best_elapsed_ms:
                pr && (pr.best_elapsed_ms != null ? pr.best_elapsed_ms : pr.avg_elapsed_ms),
              avg_elapsed_ms:
                pr && (pr.avg_elapsed_ms != null ? pr.avg_elapsed_ms : pr.best_elapsed_ms),
            });
          }
        } catch {
          /* skip subject on error */
        }
      })
    );
    return rows;
  }

  /**
   * Fallback PUT /subjects/{id} when Api.updateSubject is missing
   * (stale mobile cache of api.js without the new method).
   */
  async function updateSubjectFallback(subjectId, body) {
    const cfg = window.STEM_CONFIG || {};
    const base = (cfg.apiUrl || "").replace(/\/$/, "");
    if (!base) throw new Error("API URL not configured.");
    const res = await fetch(`${base}/subjects/${encodeURIComponent(subjectId)}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token()}`,
      },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    if (!res.ok) {
      const err = new Error((data && data.error) || res.statusText || "Update failed");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  /* ---------- Mastery (topic collections → Study-like flow) ---------- */

  function resetMasteryDraft() {
    const today = new Date();
    const end = new Date(today);
    end.setDate(end.getDate() + 30);
    const iso = (d) => d.toISOString().slice(0, 10);
    state.masteryDraft = {
      name: "",
      category: "",
      topics: [],
      subject_ids: [],
      start_date: iso(today),
      end_date: iso(end),
      shared: false,
    };
    state.masteryCreateStep = 1;
    state.masteryEditId = null;
  }

  function fillMasteryDraftFromCollection(c) {
    state.masteryDraft = {
      name: c.name || "",
      category: c.category || "",
      topics: Array.isArray(c.topics) ? [...c.topics] : [],
      subject_ids: Array.isArray(c.subject_ids) ? [...c.subject_ids] : [],
      start_date: c.start_date || "",
      end_date: c.end_date || "",
      shared: !!c.shared,
    };
    state.masteryEditId = c.mastery_id || null;
    state.masteryCreateStep = 1;
  }

  function masterySetsInCategory(allSubjects, category) {
    const rows = [];
    for (const s of allSubjects || []) {
      if ((s.category || "Mathematics") !== category) continue;
      rows.push({
        subject_id: s.subject_id,
        topic: s.topic || s.name || s.subject_id,
        grade_level: (s.grade_level || "").trim(),
        sort_order: Number(s.sort_order) || 0,
      });
    }
    rows.sort((a, b) => {
      if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order;
      return String(a.topic).localeCompare(String(b.topic));
    });
    return rows;
  }

  async function viewMastery() {
    // Active quiz / results from a mastery collection reuse Study session UI
    if (
      state.masteryView === "study" &&
      state.session &&
      state.studyPhase &&
      !state.session.is_assessment
    ) {
      if (state.studyPhase === "results" && state.studyResults) {
        return viewStudyResults(state.studyResults);
      }
      return viewSession();
    }

    if (state.masteryView === "create" || state.masteryView === "edit") {
      return viewMasteryCreate();
    }
    if (state.masteryView === "study" && state.masteryActive) {
      return viewMasteryStudy();
    }
    return viewMasteryHub();
  }

  async function viewMasteryHub() {
    let collections = [];
    try {
      const data = await Api.listMastery(token());
      collections = data.collections || [];
      state.masteryCollections = collections;
    } catch (e) {
      return `<div class="card"><h1>Mastery</h1>
        <p class="muted">${escapeHtml(e.message || "Could not load collections")}</p></div>`;
    }

    const isAdmin = typeof Auth.isAdmin === "function" && Auth.isAdmin();
    const chips = collections.length
      ? collections
          .map((c) => {
            const win = c.window_status || "active";
            const badge =
              win === "upcoming"
                ? `<span class="badge warn">Upcoming</span>`
                : win === "ended"
                  ? `<span class="badge err">Ended</span>`
                  : `<span class="badge ok">Active</span>`;
            const shared = c.shared
              ? `<span class="badge subject-tag">Shared</span>`
              : "";
            const canManage = Boolean(c.can_manage || c.is_owner || isAdmin);
            const actions = canManage
              ? `<span class="mastery-chip-actions">
                  <button type="button" class="btn-icon secondary mastery-chip-action"
                    data-mastery-edit="${escapeAttr(c.mastery_id)}"
                    title="Edit collection" aria-label="Edit ${escapeAttr(c.name || "collection")}">${iconPencil()}</button>
                  <button type="button" class="btn-icon danger mastery-chip-action"
                    data-mastery-delete="${escapeAttr(c.mastery_id)}"
                    title="Delete collection" aria-label="Delete ${escapeAttr(c.name || "collection")}">${iconTrash()}</button>
                </span>`
              : "";
            return `<div class="mastery-chip-wrap">
              <button type="button" class="btn mastery-chip ${
                win === "ended" ? "secondary" : ""
              }" data-mastery-open="${escapeAttr(c.mastery_id)}">
                <span class="mastery-chip-name">${escapeHtml(c.name || "Untitled")}</span>
                ${shared}${badge}
              </button>
              ${actions}
            </div>`;
          })
          .join("")
      : `<p class="muted">No published mastery collections yet. Create one to get started.</p>`;

    return `
      <div class="card mastery-hub-card">
        <div class="mastery-hub-header">
          <h1>Mastery</h1>
          <button type="button" class="btn accent" id="btn-mastery-create">+ Create</button>
        </div>
        <p class="muted">Build a collection of topics, set dates, and study them like the Study page. Scores, XP, and times count toward your normal Study progress.</p>
        <h2 class="mastery-section-title">Published collections</h2>
        <div class="mastery-chip-row" role="list">${chips}</div>
      </div>`;
  }

  async function viewMasteryCreate() {
    const editing = state.masteryView === "edit" && state.masteryEditId;
    const step = Math.min(4, Math.max(1, Number(state.masteryCreateStep) || 1));
    state.masteryCreateStep = step;
    if (!state.masteryDraft) resetMasteryDraft();
    const draft = state.masteryDraft;
    const isAdmin = typeof Auth.isAdmin === "function" && Auth.isAdmin();

    let allSubjects = [];
    try {
      const tok = token();
      allSubjects = await StudyCache.loadSubjects(tok);
    } catch {
      allSubjects = [];
    }

    const stemOrder = ["Science", "Technology", "Engineering", "Mathematics"];
    const categoriesPresent = [
      ...new Set(
        allSubjects.map((s) => s.category || "Mathematics").filter(Boolean)
      ),
    ];
    categoriesPresent.sort((a, b) => {
      const ia = stemOrder.indexOf(a);
      const ib = stemOrder.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
    if (!draft.category && categoriesPresent.length) {
      draft.category =
        categoriesPresent.find((c) => c === "Mathematics") || categoriesPresent[0];
    }

    const stepsHtml = [1, 2, 3, 4]
      .map(
        (n) =>
          `<span class="mastery-step-dot ${n === step ? "active" : n < step ? "done" : ""}">Step ${n}</span>`
      )
      .join("");

    let body = "";
    if (step === 1) {
      body = `
        <label for="mastery-name">Collection name</label>
        <input id="mastery-name" type="text" maxlength="80" required
          value="${escapeAttr(draft.name || "")}"
          placeholder="e.g. Grade 5 fluency pack" />
        <p class="muted">Give this mastery set a clear name learners will recognize.</p>`;
    } else if (step === 2) {
      const opts = categoriesPresent.length
        ? categoriesPresent
            .map(
              (c) =>
                `<option value="${escapeAttr(c)}" ${
                  c === draft.category ? "selected" : ""
                }>${escapeHtml(c)}</option>`
            )
            .join("")
        : `<option value="">No categories available</option>`;
      body = `
        <label for="mastery-category">Category</label>
        <select id="mastery-category">${opts}</select>
        <p class="muted">Topics in the next step come from this category.</p>`;
    } else if (step === 3) {
      const sets = masterySetsInCategory(allSubjects, draft.category);
      const selectedIds = new Set(
        (draft.subject_ids || []).map((id) => String(id))
      );
      const selectedTopics = new Set(
        (draft.topics || []).map((t) => String(t).toLowerCase())
      );
      const rows = sets.length
        ? sets
            .map((g) => {
              const checked =
                selectedIds.has(g.subject_id) ||
                selectedTopics.has(String(g.topic).toLowerCase())
                  ? "checked"
                  : "";
              const grade = g.grade_level || "—";
              return `<tr>
                <td class="mastery-check-cell">
                  <input type="checkbox" class="mastery-topic-cb"
                    data-topic="${escapeAttr(g.topic)}"
                    data-subject-id="${escapeAttr(g.subject_id)}"
                    ${checked} />
                </td>
                <td>${escapeHtml(g.topic)}</td>
                <td class="mastery-grade-cell">${escapeHtml(grade)}</td>
              </tr>`;
            })
            .join("")
        : `<tr><td colspan="3" class="muted">No sets in this category.</td></tr>`;
      body = `
        <p class="muted">Select <strong>at least 2 sets</strong> for <strong>${escapeHtml(
          draft.category || "—"
        )}</strong>.</p>
        <div class="table-wrap mastery-topics-wrap">
          <table class="mastery-topics-table" aria-label="Select sets">
            <thead>
              <tr>
                <th scope="col" class="mastery-check-cell">
                  <input type="checkbox" id="mastery-topic-all" title="Select all" aria-label="Select all sets" />
                </th>
                <th scope="col">Topic</th>
                <th scope="col">Grade Level</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    } else {
      body = `
        <div class="row mastery-dates">
          <div>
            <label for="mastery-start">Start date</label>
            <input id="mastery-start" type="date" value="${escapeAttr(
              draft.start_date || ""
            )}" required />
          </div>
          <div>
            <label for="mastery-end">End date</label>
            <input id="mastery-end" type="date" value="${escapeAttr(
              draft.end_date || ""
            )}" required />
          </div>
        </div>
        ${
          isAdmin
            ? `<p class="mastery-shared-note">As admin, this collection is <strong>visible to all learners</strong> on their Mastery page.</p>`
            : ""
        }
        <p class="muted">Publishing makes this collection appear at the top of the Mastery page.</p>
        <div class="mastery-review muted">
          <div><strong>${escapeHtml(draft.name || "Untitled")}</strong></div>
          <div>${escapeHtml(draft.category || "—")} · ${(draft.subject_ids || draft.topics || []).length} set(s)</div>
          <div>${escapeHtml((draft.topics || []).join(", ") || "—")}</div>
        </div>`;
    }

    const backLabel = step === 1 ? "Cancel" : "Back";
    const nextLabel =
      step === 4 ? (editing ? "Save changes" : "Publish") : "Next";

    return `
      <div class="card mastery-create-card">
        <h1>${editing ? "Edit Mastery" : "Create Mastery"}</h1>
        <div class="mastery-steps">${stepsHtml}</div>
        <div class="stack mastery-step-body">${body}</div>
        <div class="row mastery-wizard-actions" style="margin-top:1rem">
          <button type="button" class="btn secondary" id="btn-mastery-back">${backLabel}</button>
          <button type="button" class="btn accent" id="btn-mastery-next">${nextLabel}</button>
        </div>
      </div>`;
  }

  async function viewMasteryStudy() {
    const collection = state.masteryActive;
    if (!collection) {
      state.masteryView = "hub";
      return viewMasteryHub();
    }
    try {
      const tok = token();
      const { subjects: allSubjects } = await StudyCache.loadStudyData(tok, null);
      const allowed = new Set(collection.subject_ids || []);
      const topicsInCollection = allSubjects
        .filter((s) => allowed.has(s.subject_id))
        .slice()
        .sort((a, b) => {
          const ao = Number(a.sort_order) || 0;
          const bo = Number(b.sort_order) || 0;
          if (ao !== bo) return ao - bo;
          return String(a.topic || a.name || "").localeCompare(
            String(b.topic || b.name || "")
          );
        });

      if (!topicsInCollection.length) {
        return `<div class="card">
          <button type="button" class="btn secondary btn-sm" data-mastery-hub>← Mastery</button>
          <h1>${escapeHtml(collection.name || "Mastery")}</h1>
          <p class="muted">No matching topics found for this collection.</p>
        </div>`;
      }

      if (
        !state.masterySubjectId ||
        !topicsInCollection.some((s) => s.subject_id === state.masterySubjectId)
      ) {
        state.masterySubjectId = topicsInCollection[0].subject_id;
      }
      // Keep Study pickers in sync so Start buttons / caches stay consistent
      state.studyCategory = collection.category || topicsInCollection[0].category;
      state.studySubjectId = state.masterySubjectId;

      const selected =
        topicsInCollection.find((s) => s.subject_id === state.masterySubjectId) ||
        topicsInCollection[0];

      let landing = StudyCache.getLanding(selected.subject_id);
      if (!landing) {
        landing = await Api.studyLanding(tok, selected.subject_id);
        StudyCache.setLanding(selected.subject_id, landing);
      }
      const levels = (landing && landing.levels) || [];
      const progMap = Object.fromEntries(
        ((landing && landing.progress) || []).map((p) => [
          `${p.subject_id}:${p.level_id}`,
          p,
        ])
      );
      const radarProgressRows = (landing && landing.progress_rows) || [];

      const topicOptions = topicsInCollection
        .map(
          (s) =>
            `<option value="${escapeAttr(s.subject_id)}" ${
              s.subject_id === selected.subject_id ? "selected" : ""
            }>${escapeHtml(s.topic || s.name || s.subject_id)}</option>`
        )
        .join("");

      const items = levels
        .map((lv) => {
          const pr = progMap[`${selected.subject_id}:${lv.level_id}`];
          const statusCls = pr
            ? pr.status === "completed"
              ? "ok"
              : pr.status === "failed"
                ? "err"
                : "warn"
            : "";
          return `
          <div class="level-item">
            <div class="grow">
              <div class="title">
                <strong>${escapeHtml(lv.name)}</strong>
                ${
                  pr
                    ? `<span class="badge ${statusCls}">${escapeHtml(pr.status)}</span>`
                    : `<span class="badge">new</span>`
                }
                ${speedBadgeHtml(pr && pr.status === "completed" ? pr : null)}
              </div>
              <div class="muted">${lv.question_count || 0} questions · pass ≥ ${Math.round(
                (lv.pass_accuracy || 0.8) * 100
              )}%
                ${
                  pr && pr.best_elapsed_ms != null
                    ? ` · best ⏱ ${formatDuration(pr.best_elapsed_ms)}`
                    : ""
                }
              </div>
            </div>
            <button class="btn" type="button"
              data-start="${escapeHtml(selected.subject_id)}"
              data-level="${escapeHtml(lv.level_id)}">Start</button>
          </div>`;
        })
        .join("");

      const radarHtml = performanceRadarHtml({
        subjects: allSubjects,
        progressRows: radarProgressRows,
        focus: { subjectId: selected.subject_id },
        size: 168,
        showCaption: true,
      });

      const win = collection.window_status || "active";
      const windowNote =
        win === "upcoming"
          ? `<p class="muted">Starts ${escapeHtml(collection.start_date || "")}.</p>`
          : win === "ended"
            ? `<p class="muted">Ended ${escapeHtml(collection.end_date || "")}. You can still practice; progress still counts.</p>`
            : `<p class="muted">${escapeHtml(collection.start_date || "")} → ${escapeHtml(
                collection.end_date || ""
              )}</p>`;

      return `
        <div class="card study-landing-card mastery-study-card">
          <button type="button" class="btn secondary btn-sm" data-mastery-hub>← Mastery</button>
          <div class="study-header" style="margin-top:0.5rem">
            <div class="study-header-main">
              <h1 class="study-page-title">${escapeHtml(collection.name || "Mastery")}</h1>
              <p class="muted">${escapeHtml(collection.category || "")} · ${(
                collection.topics || []
              ).length} topic(s)</p>
              ${windowNote}
              <div class="study-pickers study-pickers-stacked">
                <div class="study-picker-field">
                  <label for="mastery-study-topic">Topic</label>
                  <select id="mastery-study-topic">${topicOptions}</select>
                </div>
              </div>
            </div>
            <div class="study-radar">${radarHtml}</div>
          </div>
          <div class="study-levels">
            ${items || "<p class='muted'>No levels configured for this topic.</p>"}
          </div>
        </div>`;
    } catch (e) {
      if (e.status === 402) return viewPaywall(e.message);
      return `<div class="card"><h1>Mastery</h1><p class="muted">${escapeHtml(
        e.message || String(e)
      )}</p></div>`;
    }
  }

  function captureMasteryCreateStep() {
    const draft = state.masteryDraft;
    const step = state.masteryCreateStep;
    if (step === 1) {
      draft.name = (
        document.getElementById("mastery-name")?.value || ""
      ).trim();
      if (!draft.name) {
        toast("Enter a collection name", true);
        return false;
      }
    } else if (step === 2) {
      const nextCat =
        document.getElementById("mastery-category")?.value || draft.category;
      if (!nextCat) {
        toast("Select a category", true);
        return false;
      }
      // Changing category clears prior topic picks (keep picks when editing same category)
      if (nextCat !== draft.category) {
        draft.topics = [];
        draft.subject_ids = [];
      }
      draft.category = nextCat;
    } else if (step === 3) {
      const picked = [];
      const pickedIds = [];
      main()
        .querySelectorAll(".mastery-topic-cb:checked")
        .forEach((cb) => {
          const t = cb.getAttribute("data-topic");
          const sid = cb.getAttribute("data-subject-id");
          if (t) picked.push(t);
          if (sid) pickedIds.push(sid);
        });
      draft.topics = picked;
      draft.subject_ids = pickedIds;
      if (picked.length < 2) {
        toast("Select at least 2 sets", true);
        return false;
      }
    } else if (step === 4) {
      draft.start_date =
        document.getElementById("mastery-start")?.value || draft.start_date;
      draft.end_date =
        document.getElementById("mastery-end")?.value || draft.end_date;
      draft.shared = typeof Auth.isAdmin === "function" && Auth.isAdmin();
      if (!draft.start_date || !draft.end_date) {
        toast("Choose start and end dates", true);
        return false;
      }
      if (draft.end_date < draft.start_date) {
        toast("End date must be on or after start date", true);
        return false;
      }
    }
    return true;
  }

  function bindMasteryPage() {
    const createBtn = document.getElementById("btn-mastery-create");
    if (createBtn) {
      createBtn.onclick = () => {
        resetMasteryDraft();
        state.masteryView = "create";
        state.masteryCreateStep = 1;
        render();
      };
    }

    main().querySelectorAll("[data-mastery-open]").forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.getAttribute("data-mastery-open");
        try {
          const col =
            (state.masteryCollections || []).find((c) => c.mastery_id === id) ||
            (await Api.getMastery(token(), id));
          state.masteryActive = col;
          state.masterySubjectId = null;
          state.masteryView = "study";
          render();
        } catch (e) {
          toast(e.message || String(e), true);
        }
      };
    });

    main().querySelectorAll("[data-mastery-edit]").forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const id = btn.getAttribute("data-mastery-edit");
        try {
          const col =
            (state.masteryCollections || []).find((c) => c.mastery_id === id) ||
            (await Api.getMastery(token(), id));
          fillMasteryDraftFromCollection(col);
          state.masteryView = "edit";
          state.masteryActive = null;
          render();
        } catch (e) {
          toast(e.message || String(e), true);
        }
      };
    });

    main().querySelectorAll("[data-mastery-delete]").forEach((btn) => {
      btn.onclick = async (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        const id = btn.getAttribute("data-mastery-delete");
        const col = (state.masteryCollections || []).find(
          (c) => c.mastery_id === id
        );
        const label = (col && col.name) || "this collection";
        if (
          !window.confirm(
            `Delete mastery “${label}”? Learners will no longer see it on the Mastery page.`
          )
        ) {
          return;
        }
        btn.disabled = true;
        try {
          await Api.deleteMastery(token(), id);
          toast("Mastery collection deleted");
          if (state.masteryActive && state.masteryActive.mastery_id === id) {
            state.masteryActive = null;
            state.masteryView = "hub";
          }
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
        }
      };
    });

    main().querySelectorAll("[data-mastery-hub]").forEach((btn) => {
      btn.onclick = () => {
        state.masteryView = "hub";
        state.masteryActive = null;
        state.masteryEditId = null;
        render();
      };
    });

    const topicSel = document.getElementById("mastery-study-topic");
    if (topicSel) {
      topicSel.onchange = () => {
        state.masterySubjectId = topicSel.value || null;
        state.studySubjectId = state.masterySubjectId;
        render();
      };
    }

    const back = document.getElementById("btn-mastery-back");
    const next = document.getElementById("btn-mastery-next");
    if (back) {
      back.onclick = () => {
        if (state.masteryCreateStep <= 1) {
          state.masteryView = "hub";
          state.masteryEditId = null;
          render();
          return;
        }
        captureMasteryCreateStep();
        state.masteryCreateStep -= 1;
        render();
      };
    }
    if (next) {
      next.onclick = async () => {
        if (!captureMasteryCreateStep()) return;
        if (state.masteryCreateStep < 4) {
          state.masteryCreateStep += 1;
          render();
          return;
        }
        const draft = state.masteryDraft;
        const editing = state.masteryView === "edit" && state.masteryEditId;
        const payload = {
          name: draft.name,
          category: draft.category,
          topics: draft.topics,
          subject_ids: draft.subject_ids || [],
          start_date: draft.start_date,
          end_date: draft.end_date,
          shared: !!draft.shared,
        };
        next.disabled = true;
        next.textContent = editing ? "Saving…" : "Publishing…";
        try {
          if (editing) {
            const updated = await Api.updateMastery(
              token(),
              state.masteryEditId,
              payload
            );
            toast(`Updated “${updated.name}”`);
          } else {
            const created = await Api.createMastery(token(), payload);
            toast(`Published “${created.name}”`);
          }
          state.masteryView = "hub";
          state.masteryActive = null;
          state.masteryEditId = null;
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          next.disabled = false;
          next.textContent = editing ? "Save changes" : "Publish";
        }
      };
    }

    const selectAll = document.getElementById("mastery-topic-all");
    if (selectAll) {
      const boxes = () =>
        Array.from(main().querySelectorAll(".mastery-topic-cb"));
      selectAll.onchange = () => {
        boxes().forEach((cb) => {
          cb.checked = selectAll.checked;
        });
      };
      // Indeterminate sync
      const syncAll = () => {
        const all = boxes();
        const n = all.filter((c) => c.checked).length;
        selectAll.checked = all.length > 0 && n === all.length;
        selectAll.indeterminate = n > 0 && n < all.length;
      };
      boxes().forEach((cb) => {
        cb.onchange = syncAll;
      });
      syncAll();
    }
  }

  /* ---------- Loading (kid-friendly MElon animation) ---------- */

  function loadingMessageForRoute(route) {
    switch (route) {
      case "home":
        return "Going home…";
      case "study":
        return "Opening Study…";
      case "assessment":
        return "Opening Assessment…";
      case "insights":
        return "Opening Insights…";
      case "tasks":
        return "Loading tasks…";
      case "account":
        return "Opening Account…";
      case "profile":
        return "Opening Profile…";
      case "mastery":
        return "Opening Mastery…";
      case "facebook":
        return "Opening Facebook…";
      case "admin":
        return "Opening Admin…";
      case "pay":
        return "Opening payment…";
      default:
        return "Loading…";
    }
  }

  /** Official MElon Facebook profile URL (from STEM_CONFIG or default). */
  function facebookPageUrl() {
    const cfg = window.STEM_CONFIG || {};
    return (
      (cfg.facebookPageUrl || "").trim() ||
      "https://www.facebook.com/profile.php?id=61592589455670"
    );
  }

  /**
   * Facebook community page: Follow + Write Feedback.
   *
   * Technical note: Meta does not allow a third-party site to Follow a Page or
   * post a comment using only a username/password collected in-app. Doing that
   * would require a Meta Developer app, Facebook Login (OAuth), and Graph API
   * permissions (with app review). Without that, the only complete flow is to
   * open Facebook so the signed-in Facebook user can Follow or comment there.
   */
  async function viewFacebook() {
    await refreshProfile({ skipIfFresh: true, notices: false });
    const pageUrl = facebookPageUrl();
    const p = state.profile || {};
    const followed = Boolean(p.facebook_followed);
    // Hide confirm after claim; show again when ad-free window lapses (need new post)
    const engDone = Boolean(p.ad_free_active);
    const followConfirmHtml = followed
      ? `<p class="muted" id="fb-follow-done" style="margin:0.5rem 0 0">✓ Follow confirmed — 6 months free subscription is active.</p>`
      : `<button type="button" class="btn secondary" id="fb-follow-confirm">I followed Melon</button>`;
    const feedbackConfirmHtml = engDone
      ? `<p class="muted" id="fb-feedback-done" style="margin:0.5rem 0 0">✓ Check-in recorded. Next Facebook comment/feature request by ${escapeHtml(formatAccountDate(p.next_engagement_due_at))}.</p>`
      : `<button type="button" class="btn secondary" id="fb-feedback-confirm">I posted on Facebook</button>`;
    const followHelp = followed
      ? `You already confirmed following MElon. Free subscription is tracked on your Account page.`
      : `Following MElon on Facebook unlocks a <strong>6 months free subscription</strong>. After you follow on Facebook, tap <strong>I followed Melon</strong> to activate it.`;
    const feedbackHelp = engDone
      ? `Your last check-in is still valid. Post again after the due date so a future ad banner stays off.`
      : `Leave a Facebook comment or feature request at least every <strong>3 months</strong> so an ad banner (coming later) stays hidden while you follow us.`;

    return `
      <div class="card facebook-page">
        <div class="row" style="justify-content:space-between;align-items:flex-start;gap:0.5rem">
          <h1 style="margin:0">MElon on Facebook</h1>
          <button type="button" class="btn secondary btn-sm" data-go="home">← Home</button>
        </div>
        <p class="muted" style="margin-top:0.5rem">
          Stay connected with MElon Basic Education. Follow our page or leave feedback for the team.
        </p>
      </div>

      <div class="card facebook-action-card">
        <h2>Follow</h2>
        <p class="muted" style="margin:0 0 0.65rem">${followHelp}</p>
        <form id="fb-follow-form" class="stack facebook-form">
          <div>
            <label for="fb-follow-name">Your name (optional)</label>
            <input id="fb-follow-name" type="text" maxlength="80" autocomplete="name"
              placeholder="How should we greet you?"
              value="${escapeAttr(p.facebook_display_name || learnerDisplayName(p) || "")}" />
          </div>
          <div>
            <label for="fb-follow-handle">Facebook name or profile link (optional)</label>
            <input id="fb-follow-handle" type="text" maxlength="200" autocomplete="off"
              placeholder="e.g. Jane D. or facebook.com/…"
              value="${escapeAttr(p.facebook_handle || "")}" />
          </div>
          <div class="row" id="fb-follow-actions">
            ${
              followed
                ? ""
                : `<button type="submit" class="btn accent" id="fb-follow-btn">Open Facebook to Follow</button>`
            }
            ${followConfirmHtml}
          </div>
        </form>
      </div>

      <div class="card facebook-action-card">
        <h2>Write Feedback</h2>
        <p class="muted" style="margin:0 0 0.65rem">${feedbackHelp}</p>
        <form id="fb-feedback-form" class="stack facebook-form">
          <div>
            <label for="fb-feedback-name">Your name (optional)</label>
            <input id="fb-feedback-name" type="text" maxlength="80" autocomplete="name"
              placeholder="Name"
              value="${escapeAttr(p.facebook_display_name || learnerDisplayName(p) || "")}" />
          </div>
          <div>
            <label for="fb-feedback-kind">Type</label>
            <select id="fb-feedback-kind" ${engDone ? "disabled" : ""}>
              <option value="comment">Comment</option>
              <option value="feedback">Feedback</option>
              <option value="feature_request">Feature request</option>
            </select>
          </div>
          <div>
            <label for="fb-feedback-text">Your message</label>
            <textarea id="fb-feedback-text" ${engDone ? "" : "required"} maxlength="2000" rows="5"
              placeholder="Tell us what you like or what we can improve…"
              ${engDone ? "disabled" : ""}></textarea>
          </div>
          <div class="row" id="fb-feedback-actions">
            ${
              engDone
                ? ""
                : `<button type="submit" class="btn accent" id="fb-feedback-btn">Open Facebook to post</button>`
            }
            ${feedbackConfirmHtml}
          </div>
        </form>
        <p id="fb-feedback-copy-hint" class="muted hidden" style="margin-top:0.75rem;font-size:0.9rem"></p>
      </div>

      <div class="card">
        <div class="row">
          <button type="button" class="btn" data-go="home">Home</button>
          <a class="btn secondary" href="${escapeAttr(pageUrl)}" target="_blank" rel="noopener noreferrer">
            Open Facebook page
          </a>
        </div>
      </div>`;
  }

  function bindFacebookPage() {
    const pageUrl = facebookPageUrl();

    const followForm = document.getElementById("fb-follow-form");
    if (followForm) {
      followForm.onsubmit = (e) => {
        e.preventDefault();
        const name = (document.getElementById("fb-follow-name")?.value || "").trim();
        const handle = (document.getElementById("fb-follow-handle")?.value || "").trim();
        try {
          sessionStorage.setItem(
            "stem_fb_follow_meta",
            JSON.stringify({ name, handle, at: Date.now() })
          );
        } catch {
          /* private mode */
        }
        window.open(pageUrl, "_blank", "noopener,noreferrer");
        toast(
          name
            ? `Thanks, ${name}! Follow MElon on Facebook, then tap “I followed Melon”.`
            : "Follow MElon on Facebook, then tap “I followed Melon”."
        );
      };
    }

    const followConfirm = document.getElementById("fb-follow-confirm");
    if (followConfirm) {
      followConfirm.onclick = async () => {
        const name = (document.getElementById("fb-follow-name")?.value || "").trim();
        const handle = (document.getElementById("fb-follow-handle")?.value || "").trim();
        followConfirm.disabled = true;
        try {
          const profile = await Api.claimFacebookFollow(token(), {
            display_name: name,
            handle,
            confirmed: true,
          });
          state.profile = profile;
          ProfileCache.set(profile, { noticesLoaded: false });
          toast("6 months free subscription activated. Thank you for following!");
          // Hide confirm button (and stay on page so user sees it disappear)
          followConfirm.classList.add("hidden");
          followConfirm.setAttribute("hidden", "hidden");
          const openBtn = document.getElementById("fb-follow-btn");
          if (openBtn) {
            openBtn.classList.add("hidden");
            openBtn.setAttribute("hidden", "hidden");
          }
          const actions = document.getElementById("fb-follow-actions");
          if (actions && !document.getElementById("fb-follow-done")) {
            const done = document.createElement("p");
            done.id = "fb-follow-done";
            done.className = "muted";
            done.style.margin = "0.5rem 0 0";
            done.textContent =
              "✓ Follow confirmed — 6 months free subscription is active.";
            actions.appendChild(done);
          }
        } catch (err) {
          toast(err.message || String(err), true);
          followConfirm.disabled = false;
        }
      };
    }

    const feedbackForm = document.getElementById("fb-feedback-form");
    if (feedbackForm) {
      feedbackForm.onsubmit = async (e) => {
        e.preventDefault();
        const name = (document.getElementById("fb-feedback-name")?.value || "").trim();
        const text = (document.getElementById("fb-feedback-text")?.value || "").trim();
        const kind = (document.getElementById("fb-feedback-kind")?.value || "comment").trim();
        if (!text) {
          toast("Please write your feedback first.", true);
          return;
        }
        const composed = name
          ? `Feedback from ${name} (MElon Basic Education):\n\n${text}`
          : `Feedback via MElon Basic Education:\n\n${text}`;

        let copied = false;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(composed);
            copied = true;
          }
        } catch {
          copied = false;
        }

        try {
          sessionStorage.setItem(
            "stem_fb_feedback_draft",
            JSON.stringify({ name, text, kind, at: Date.now() })
          );
        } catch {
          /* private mode */
        }

        const hint = document.getElementById("fb-feedback-copy-hint");
        if (hint) {
          hint.classList.remove("hidden");
          hint.textContent = copied
            ? "Message copied. Paste it on Facebook, then tap “I posted on Facebook”."
            : "Post your message on Facebook, then tap “I posted on Facebook”.";
        }

        window.open(pageUrl, "_blank", "noopener,noreferrer");
        toast(
          copied
            ? "Message copied — paste on Facebook, then confirm here."
            : "Facebook opened — post your comment, then confirm here."
        );
      };
    }

    const feedbackConfirm = document.getElementById("fb-feedback-confirm");
    if (feedbackConfirm) {
      feedbackConfirm.onclick = async () => {
        const name = (document.getElementById("fb-feedback-name")?.value || "").trim();
        const text = (document.getElementById("fb-feedback-text")?.value || "").trim();
        const kind = (document.getElementById("fb-feedback-kind")?.value || "comment").trim();
        if (!text) {
          toast("Write your message first, then post it on Facebook.", true);
          return;
        }
        feedbackConfirm.disabled = true;
        try {
          const profile = await Api.claimFacebookEngagement(token(), {
            kind,
            display_name: name,
            text,
          });
          state.profile = profile;
          ProfileCache.set(profile, { noticesLoaded: false });
          toast("Thanks! Your 3‑month ad-free check-in is recorded.");
          feedbackConfirm.classList.add("hidden");
          feedbackConfirm.setAttribute("hidden", "hidden");
          const openBtn = document.getElementById("fb-feedback-btn");
          if (openBtn) {
            openBtn.classList.add("hidden");
            openBtn.setAttribute("hidden", "hidden");
          }
          const ta = document.getElementById("fb-feedback-text");
          const kindEl = document.getElementById("fb-feedback-kind");
          if (ta) ta.disabled = true;
          if (kindEl) kindEl.disabled = true;
          const actions = document.getElementById("fb-feedback-actions");
          if (actions && !document.getElementById("fb-feedback-done")) {
            const done = document.createElement("p");
            done.id = "fb-feedback-done";
            done.className = "muted";
            done.style.margin = "0.5rem 0 0";
            done.textContent = `✓ Check-in recorded. Next by ${formatAccountDate(profile.next_engagement_due_at)}.`;
            actions.appendChild(done);
          }
        } catch (err) {
          toast(err.message || String(err), true);
          feedbackConfirm.disabled = false;
        }
      };
    }
  }

  /** Cute bouncing melon SVG used during page transitions. */
  function melonLoaderSvg() {
    return `
      <svg class="melon-svg" viewBox="0 0 120 130" width="112" height="122" focusable="false" aria-hidden="true">
        <defs>
          <radialGradient id="melonBodyGrad" cx="38%" cy="32%" r="68%">
            <stop offset="0%" stop-color="#bbf7d0"/>
            <stop offset="55%" stop-color="#4ade80"/>
            <stop offset="100%" stop-color="#16a34a"/>
          </radialGradient>
          <linearGradient id="melonStripeGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#15803d" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="#14532d" stop-opacity="0.45"/>
          </linearGradient>
        </defs>
        <!-- stem -->
        <path class="melon-stem" d="M60 28 C58 18, 62 10, 60 4" fill="none" stroke="#854d0e" stroke-width="4" stroke-linecap="round"/>
        <!-- leaf -->
        <g class="melon-leaf">
          <ellipse cx="74" cy="14" rx="16" ry="8" fill="#22c55e" transform="rotate(25 74 14)"/>
          <path d="M62 16 Q74 10 86 14" fill="none" stroke="#15803d" stroke-width="1.5" stroke-linecap="round"/>
        </g>
        <!-- body -->
        <ellipse class="melon-body" cx="60" cy="78" rx="42" ry="40" fill="url(#melonBodyGrad)" stroke="#15803d" stroke-width="2.5"/>
        <!-- stripes -->
        <path d="M32 58 Q40 78 32 98" fill="none" stroke="url(#melonStripeGrad)" stroke-width="5" stroke-linecap="round"/>
        <path d="M48 52 Q54 78 48 104" fill="none" stroke="url(#melonStripeGrad)" stroke-width="5" stroke-linecap="round"/>
        <path d="M72 52 Q66 78 72 104" fill="none" stroke="url(#melonStripeGrad)" stroke-width="5" stroke-linecap="round"/>
        <path d="M88 58 Q80 78 88 98" fill="none" stroke="url(#melonStripeGrad)" stroke-width="5" stroke-linecap="round"/>
        <!-- cheeks -->
        <ellipse cx="38" cy="84" rx="7" ry="5" fill="#fb7185" opacity="0.55"/>
        <ellipse cx="82" cy="84" rx="7" ry="5" fill="#fb7185" opacity="0.55"/>
        <!-- eyes -->
        <g class="melon-eyes">
          <ellipse cx="46" cy="72" rx="5.5" ry="6.5" fill="#134e4a"/>
          <ellipse cx="74" cy="72" rx="5.5" ry="6.5" fill="#134e4a"/>
          <circle cx="48" cy="70" r="1.8" fill="#fff"/>
          <circle cx="76" cy="70" r="1.8" fill="#fff"/>
        </g>
        <!-- smile -->
        <path d="M48 92 Q60 102 72 92" fill="none" stroke="#134e4a" stroke-width="3" stroke-linecap="round"/>
        <!-- sparkles -->
        <g class="melon-sparkle melon-sparkle-a" fill="#fbbf24">
          <path d="M18 48 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2 z"/>
        </g>
        <g class="melon-sparkle melon-sparkle-b" fill="#fde68a">
          <path d="M100 56 l1.5 4 4 1.5 -4 1.5 -1.5 4 -1.5 -4 -4 -1.5 4 -1.5 z"/>
        </g>
      </svg>`;
  }

  function loadingViewHtml(message) {
    const msg = escapeHtml(message || "Loading…");
    return `
      <div class="loading-screen card" role="status" aria-live="polite" aria-busy="true" aria-label="${msg}">
        <div class="melon-loader" aria-hidden="true">
          <div class="melon-bounce">
            ${melonLoaderSvg()}
          </div>
          <div class="melon-ground-shadow"></div>
          <div class="loading-dots">
            <span></span><span></span><span></span>
          </div>
        </div>
        <p class="loading-message">${msg}</p>
        <p class="loading-hint">MElon is getting things ready…</p>
      </div>`;
  }

  function showLoading(message) {
    const el = main();
    if (el) el.innerHTML = loadingViewHtml(message);
  }

  /* ---------- Render / bind ---------- */

  async function render() {
    const el = main();
    if (!Auth.isLoggedIn()) {
      setNavVisible(false);
      setAdminModeUi(false);
      el.innerHTML = viewAuth();
      bindAuth();
      return;
    }
    setNavVisible(true);
    setAdminModeUi(state.route === "admin");
    showLoading(loadingMessageForRoute(state.route));

    let html;
    switch (state.route) {
      case "study": html = await viewStudy(); break;
      case "assessment": html = await viewAssessment(); break;
      case "tasks": html = await viewTasks(); break;
      case "insights": html = await viewInsights(); break;
      case "account": html = await viewAccount(); break;
      case "profile": html = await viewProfile(); break;
      case "mastery": html = await viewMastery(); break;
      case "facebook": html = await viewFacebook(); break;
      case "admin": html = await viewAdmin(); break;
      case "pay": html = viewPaywall(); break;
      case "home":
      default:
        // H1: skip network if profile just loaded (login/init)
        // H2: paint without notices first; scheduleHomeNoticesRefresh after
        await refreshProfile({ skipIfFresh: true, notices: false });
        updateNavProfileAvatar();
        let boardEntries = [];
        try {
          const board = await Api.leaderboard(token(), { limit: 100 });
          boardEntries = board.entries || [];
        } catch {
          boardEntries = [];
        }
        html = viewHome(boardEntries);
        break;
    }
    el.innerHTML = html;
    updateNavProfileAvatar();
    if (state.route === "admin") {
      bindAdmin();
    } else {
      bindView();
    }
    if (state.route === "profile") {
      bindProfileForm();
    }
    if (state.route === "mastery") {
      bindMasteryPage();
    }
    if (state.route === "facebook") {
      bindFacebookPage();
    }
    if (state.route === "study" && !state.studyPhase) {
      focusStudyLevelIfNeeded();
    }
    if (state.route === "home") {
      scheduleHomeNoticesRefresh();
    }
  }

  function bindProfileForm() {
    const form = document.getElementById("profile-form");
    if (!form) return;
    form.onsubmit = async (e) => {
      e.preventDefault();
      const nickname = (document.getElementById("profile-nickname")?.value || "").trim();
      const schoolId = (document.getElementById("profile-school")?.value || "").trim();
      const grade = (document.getElementById("profile-grade")?.value || "").trim();
      if (!nickname) {
        toast("Name / Nickname is required.", true);
        return;
      }
      const btn = document.getElementById("profile-save");
      if (btn) btn.disabled = true;
      try {
        state.profile = await Api.updateMe(token(), {
          nickname,
          school_id: schoolId,
          grade,
        });
        ProfileCache.set(state.profile, { noticesLoaded: false });
        updateNavProfileAvatar();
        toast("Profile saved");
        render();
      } catch (err) {
        toast(err.message || String(err), true);
        if (btn) btn.disabled = false;
      }
    };
  }

  function passwordPolicyOk(password) {
    // Match Cognito pool policy: min 8, upper, lower, digit, symbol
    return (
      password.length >= 8 &&
      /[a-z]/.test(password) &&
      /[A-Z]/.test(password) &&
      /[0-9]/.test(password) &&
      /[^A-Za-z0-9]/.test(password)
    );
  }

  /** Open eye icon (password currently hidden → click to show) */
  function eyeIconOpen() {
    return `<svg class="eye-svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
      <circle cx="12" cy="12" r="3"/>
    </svg>`;
  }

  /** Eye-off icon (password currently visible → click to hide) */
  function eyeIconOff() {
    return `<svg class="eye-svg" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
      <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/>
      <line x1="1" y1="1" x2="23" y2="23"/>
    </svg>`;
  }

  function setPasswordToggleUi(btn, visible) {
    const open = btn.querySelector(".icon-eye");
    const off = btn.querySelector(".icon-eye-off");
    if (open) open.classList.toggle("hidden", visible);
    if (off) off.classList.toggle("hidden", !visible);
    btn.setAttribute("aria-pressed", visible ? "true" : "false");
    const isConfirm = (btn.getAttribute("data-toggle-password") || "") === "password-confirm";
    const noun = isConfirm ? "confirm password" : "password";
    btn.setAttribute("aria-label", visible ? `Hide ${noun}` : `Show ${noun}`);
    btn.title = visible ? `Hide ${noun}` : `Show ${noun}`;
  }

  function bindAuth() {
    let mode = "login"; // login | signup | forgot | reset
    const tabLogin = document.getElementById("tab-login");
    const tabSignup = document.getElementById("tab-signup");
    const submit = document.getElementById("auth-submit");
    const confirmBtn = document.getElementById("auth-confirm");
    const backLoginBtn = document.getElementById("auth-back-login");
    const passwordWrap = document.getElementById("password-wrap");
    const passwordEl = document.getElementById("password");
    const passwordLabel = document.getElementById("password-label");
    const passwordConfirmWrap = document.getElementById("password-confirm-wrap");
    const passwordConfirmEl = document.getElementById("password-confirm");
    const matchHint = document.getElementById("password-match-hint");
    const codeWrap = document.getElementById("code-wrap");
    const codeLabel = document.getElementById("code-label");
    const codeHint = document.getElementById("code-hint");
    const forgotHint = document.getElementById("forgot-hint");
    const forgotLink = document.getElementById("forgot-password-link");
    const forgotLinkWrap = forgotLink && forgotLink.closest(".auth-forgot-wrap");
    const emailEl = document.getElementById("email");
    const nicknameWrap = document.getElementById("nickname-wrap");
    const nicknameEl = document.getElementById("nickname");
    const schoolWrap = document.getElementById("school-wrap");
    const schoolEl = document.getElementById("signup-school");
    const gradeWrap = document.getElementById("grade-wrap");
    const gradeEl = document.getElementById("signup-grade");
    const tabsEl = document.querySelector(".tabs");
    const authTitle = document.getElementById("auth-title");
    const authSubtitle = document.getElementById("auth-subtitle");
    const AUTH_TITLE_DEFAULT = "Welcome";
    const AUTH_SUBTITLE_DEFAULT =
      "Sign up for free. Study available topics at your own pace. When your profile is not active for 6 months, it may be deleted to save resources.";
    const AUTH_TITLE_RESET = "Change Password";
    const AUTH_SUBTITLE_RESET =
      "After initiating password change, check your email including spam folder for the code.";

    function setAuthHeading(title, subtitle) {
      if (authTitle) authTitle.textContent = title;
      if (authSubtitle) authSubtitle.textContent = subtitle;
    }

    // Pending school requested in this session (not in public catalog until approved)
    let pendingTempSchool = null; // { school_id, label }

    async function loadSignupSchools(selectedId) {
      if (!schoolEl) return;
      try {
        const data = await Api.listSchools();
        const list = data.schools || [];
        const keepId =
          selectedId ||
          (pendingTempSchool && pendingTempSchool.school_id) ||
          schoolEl.value ||
          "";
        schoolEl.innerHTML = schoolSelectOptionsHtml(list, keepId);
        // Re-attach temporary school option after catalog reload
        if (pendingTempSchool && pendingTempSchool.school_id) {
          selectTemporarySchool(
            pendingTempSchool.school_id,
            pendingTempSchool.label
          );
        } else if (
          keepId &&
          !list.some((s) => s.school_id === keepId) &&
          !Array.from(schoolEl.options).some((o) => o.value === keepId)
        ) {
          const keepLabel = schoolEl.selectedOptions?.[0]?.textContent || keepId;
          const opt = document.createElement("option");
          opt.value = keepId;
          opt.textContent = keepLabel;
          opt.selected = true;
          schoolEl.appendChild(opt);
        }
      } catch {
        schoolEl.innerHTML = `<option value="">Could not load schools</option>`;
      }
    }
    // Prefetch for Sign up combobox (public endpoint)
    loadSignupSchools();

    /**
     * Put a temporary (pending) school into the School combobox and select it.
     */
    function selectTemporarySchool(schoolId, displayLabel) {
      if (!schoolEl || !schoolId) return;
      const label = displayLabel || "Temporary school";
      // Drop previous temporary option(s)
      Array.from(schoolEl.options).forEach((o) => {
        if (o.dataset && o.dataset.tempSchool === "1") o.remove();
      });
      let opt = Array.from(schoolEl.options).find((o) => o.value === schoolId);
      if (!opt) {
        opt = document.createElement("option");
        opt.value = schoolId;
        schoolEl.appendChild(opt);
      }
      opt.dataset.tempSchool = "1";
      opt.textContent = label;
      opt.selected = true;
      schoolEl.value = schoolId;
      // Force UI selection (some browsers need selectedIndex)
      schoolEl.selectedIndex = Array.from(schoolEl.options).findIndex(
        (o) => o.value === schoolId
      );
      schoolEl.dispatchEvent(new Event("change", { bubbles: true }));
      pendingTempSchool = { school_id: schoolId, label };
      try {
        sessionStorage.setItem(PENDING_SCHOOL_KEY, schoolId);
      } catch {
        /* private mode */
      }
      updateSignupButtonState();
    }

    // Request school modal (sign-up only)
    const schoolReqLink = document.getElementById("school-request-link");
    const schoolReqModal = document.getElementById("school-request-modal");
    const schoolReqCancel = document.getElementById("school-request-cancel");
    const schoolReqSubmit = document.getElementById("school-request-submit");

    function openSchoolRequestModal(e) {
      if (e) e.preventDefault();
      if (!schoolReqModal) return;
      schoolReqModal.classList.remove("hidden");
      const nameInput = document.getElementById("req-school-name");
      if (nameInput) {
        nameInput.value = "";
        nameInput.focus();
      }
      const cityInput = document.getElementById("req-school-city");
      const provInput = document.getElementById("req-school-province");
      if (cityInput) cityInput.value = "";
      if (provInput) provInput.value = "";
    }

    function closeSchoolRequestModal() {
      if (schoolReqModal) schoolReqModal.classList.add("hidden");
    }

    if (schoolReqLink) schoolReqLink.onclick = openSchoolRequestModal;
    if (schoolReqCancel) schoolReqCancel.onclick = (e) => {
      if (e) e.preventDefault();
      closeSchoolRequestModal();
    };
    if (schoolReqModal) {
      schoolReqModal.addEventListener("click", (ev) => {
        if (ev.target === schoolReqModal) closeSchoolRequestModal();
      });
    }

    /**
     * Submit request → API → close dialog → select Temporary school in combobox.
     */
    async function submitSchoolRequest(ev) {
      if (ev) {
        ev.preventDefault();
        ev.stopPropagation();
      }
      const name = (document.getElementById("req-school-name")?.value || "").trim();
      const city = (document.getElementById("req-school-city")?.value || "").trim();
      const province = (
        document.getElementById("req-school-province")?.value || ""
      ).trim();
      if (!name) {
        toast("Please enter a school name.", true);
        document.getElementById("req-school-name")?.focus();
        return;
      }
      const requester_email = (emailEl?.value || "").trim();
      if (schoolReqSubmit) {
        schoolReqSubmit.disabled = true;
        schoolReqSubmit.textContent = "Submitting…";
      }
      try {
        const school = await Api.requestSchool({
          name,
          city,
          province,
          requester_email,
        });
        const sid = school.school_id || "";
        if (!sid) {
          throw new Error("Server did not return a school id.");
        }
        // Combobox label: Temporary school (+ requested name for clarity)
        const tempLabel = name
          ? `Temporary school: ${name}`
          : "Temporary school";

        // 1) Close popup first for snappy UX
        closeSchoolRequestModal();

        // 2) Select temporary school in School combobox
        selectTemporarySchool(sid, tempLabel);

        toast("Request sent. Temporary school is selected — finish sign-up.");
      } catch (err) {
        toast(err.message || String(err), true);
      } finally {
        if (schoolReqSubmit) {
          schoolReqSubmit.disabled = false;
          schoolReqSubmit.textContent = "Submit request";
        }
      }
    }

    if (schoolReqSubmit) {
      schoolReqSubmit.addEventListener("click", submitSchoolRequest);
    }
    // Enter key in modal fields submits the request (no nested form)
    ["req-school-name", "req-school-city", "req-school-province"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          submitSchoolRequest(ev);
        }
      });
    });

    function updateSignupButtonState() {
      if (mode === "login" || mode === "forgot") {
        submit.disabled = false;
        if (matchHint && mode === "login") matchHint.textContent = "";
        return;
      }
      if (mode !== "signup" && mode !== "reset") {
        submit.disabled = false;
        return;
      }
      const password = passwordEl.value;
      const confirm = passwordConfirmEl.value;
      const emailOk = emailEl.value.trim().length > 0 && emailEl.checkValidity();
      const nicknameOk =
        mode === "reset" ? true : nicknameEl && nicknameEl.value.trim().length > 0;
      const schoolOk =
        mode === "reset" ? true : schoolEl && schoolEl.value.trim().length > 0;
      const gradeOk =
        mode === "reset" ? true : gradeEl && gradeEl.value.trim().length > 0;
      const policyOk = passwordPolicyOk(password);
      const match = password.length > 0 && password === confirm;
      const codeOk =
        mode !== "reset" ||
        (document.getElementById("code") &&
          document.getElementById("code").value.trim().length > 0);

      if (!password && !confirm) {
        matchHint.textContent = "Use 8+ chars with upper, lower, number, and symbol.";
        matchHint.style.color = "var(--muted)";
      } else if (!policyOk) {
        matchHint.textContent = "Password must be 8+ chars with upper, lower, number, and symbol.";
        matchHint.style.color = "var(--danger)";
      } else if (!match) {
        matchHint.textContent = "Passwords do not match.";
        matchHint.style.color = "var(--danger)";
      } else {
        matchHint.textContent = "Passwords match.";
        matchHint.style.color = "var(--ok)";
      }

      if (mode === "reset") {
        submit.disabled = !(emailOk && codeOk && policyOk && match);
      } else {
        submit.disabled = !(
          emailOk &&
          nicknameOk &&
          schoolOk &&
          gradeOk &&
          policyOk &&
          match
        );
      }
    }

    function resetPasswordVisibility() {
      // Mask fields again when switching tabs; toggles stay available on Log in and Sign up
      document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
        const input = document.getElementById(btn.getAttribute("data-toggle-password"));
        if (!input) return;
        input.type = "password";
        setPasswordToggleUi(btn, false);
      });
    }

    function hideSignupExtras() {
      if (nicknameWrap) nicknameWrap.classList.add("hidden");
      if (nicknameEl) {
        nicknameEl.required = false;
      }
      if (schoolWrap) schoolWrap.classList.add("hidden");
      if (schoolEl) schoolEl.required = false;
      if (gradeWrap) gradeWrap.classList.add("hidden");
      if (gradeEl) gradeEl.required = false;
    }

    function setLoginMode() {
      mode = "login";
      setAuthHeading(AUTH_TITLE_DEFAULT, AUTH_SUBTITLE_DEFAULT);
      if (tabsEl) tabsEl.classList.remove("hidden");
      tabLogin.classList.add("active");
      tabSignup.classList.remove("active");
      submit.textContent = "Log in";
      submit.disabled = false;
      submit.classList.remove("hidden");
      if (passwordWrap) passwordWrap.classList.remove("hidden");
      if (passwordLabel) passwordLabel.textContent = "Password";
      passwordEl.required = true;
      passwordEl.autocomplete = "current-password";
      passwordConfirmWrap.classList.add("hidden");
      passwordConfirmEl.required = false;
      passwordConfirmEl.value = "";
      hideSignupExtras();
      codeWrap.classList.add("hidden");
      confirmBtn.classList.add("hidden");
      if (backLoginBtn) backLoginBtn.classList.add("hidden");
      if (forgotLinkWrap) forgotLinkWrap.classList.remove("hidden");
      if (forgotHint) {
        forgotHint.classList.add("hidden");
        forgotHint.textContent = "";
      }
      if (codeLabel) codeLabel.textContent = "Email confirmation code";
      if (codeHint) {
        codeHint.textContent =
          "Check your email after sign-up, then enter the code and tap Confirm email.";
      }
      if (matchHint) matchHint.textContent = "";
      resetPasswordVisibility();
    }

    function setSignupMode() {
      mode = "signup";
      setAuthHeading(AUTH_TITLE_DEFAULT, AUTH_SUBTITLE_DEFAULT);
      if (tabsEl) tabsEl.classList.remove("hidden");
      tabSignup.classList.add("active");
      tabLogin.classList.remove("active");
      submit.textContent = "Sign up";
      submit.classList.remove("hidden");
      if (passwordWrap) passwordWrap.classList.remove("hidden");
      if (passwordLabel) passwordLabel.textContent = "Password";
      passwordEl.required = true;
      passwordEl.autocomplete = "new-password";
      passwordConfirmWrap.classList.remove("hidden");
      passwordConfirmEl.required = true;
      if (nicknameWrap) nicknameWrap.classList.remove("hidden");
      if (nicknameEl) nicknameEl.required = true;
      if (schoolWrap) schoolWrap.classList.remove("hidden");
      if (schoolEl) schoolEl.required = true;
      if (gradeWrap) gradeWrap.classList.remove("hidden");
      if (gradeEl) gradeEl.required = true;
      loadSignupSchools();
      codeWrap.classList.add("hidden");
      confirmBtn.classList.add("hidden");
      if (backLoginBtn) backLoginBtn.classList.add("hidden");
      if (forgotLinkWrap) forgotLinkWrap.classList.add("hidden");
      if (forgotHint) {
        forgotHint.classList.add("hidden");
        forgotHint.textContent = "";
      }
      if (codeLabel) codeLabel.textContent = "Email confirmation code";
      if (codeHint) {
        codeHint.textContent =
          "Check your email after sign-up, then enter the code and tap Confirm email.";
      }
      resetPasswordVisibility();
      updateSignupButtonState();
    }

    /** Request a Cognito reset code (step 1). */
    function setForgotMode() {
      mode = "forgot";
      setAuthHeading(AUTH_TITLE_RESET, AUTH_SUBTITLE_RESET);
      if (tabsEl) tabsEl.classList.add("hidden");
      hideSignupExtras();
      if (passwordWrap) passwordWrap.classList.add("hidden");
      passwordEl.required = false;
      passwordConfirmWrap.classList.add("hidden");
      passwordConfirmEl.required = false;
      codeWrap.classList.add("hidden");
      confirmBtn.classList.add("hidden");
      if (backLoginBtn) backLoginBtn.classList.remove("hidden");
      if (forgotLinkWrap) forgotLinkWrap.classList.add("hidden");
      if (forgotHint) {
        forgotHint.classList.remove("hidden");
        forgotHint.textContent =
          "Enter your account email. We will send a verification code to reset your password.";
      }
      submit.textContent = "Send reset code";
      submit.disabled = false;
      submit.classList.remove("hidden");
      resetPasswordVisibility();
    }

    /** Enter code + new password (step 2). */
    function setResetMode() {
      mode = "reset";
      setAuthHeading(AUTH_TITLE_RESET, AUTH_SUBTITLE_RESET);
      if (tabsEl) tabsEl.classList.add("hidden");
      hideSignupExtras();
      if (passwordWrap) passwordWrap.classList.remove("hidden");
      if (passwordLabel) passwordLabel.textContent = "New password";
      passwordEl.required = true;
      passwordEl.value = "";
      passwordEl.autocomplete = "new-password";
      passwordConfirmWrap.classList.remove("hidden");
      passwordConfirmEl.required = true;
      passwordConfirmEl.value = "";
      codeWrap.classList.remove("hidden");
      if (codeLabel) codeLabel.textContent = "Reset code from email";
      if (codeHint) {
        codeHint.textContent =
          "Enter the code from your email, then choose a new password.";
      }
      const codeEl = document.getElementById("code");
      if (codeEl) codeEl.value = "";
      confirmBtn.classList.add("hidden");
      if (backLoginBtn) backLoginBtn.classList.remove("hidden");
      if (forgotLinkWrap) forgotLinkWrap.classList.add("hidden");
      if (forgotHint) {
        forgotHint.classList.remove("hidden");
        forgotHint.textContent = "Almost done — set your new password below.";
      }
      submit.textContent = "Set new password";
      submit.classList.remove("hidden");
      resetPasswordVisibility();
      updateSignupButtonState();
    }

    tabLogin.onclick = setLoginMode;
    tabSignup.onclick = setSignupMode;

    if (forgotLink) {
      forgotLink.onclick = (e) => {
        e.preventDefault();
        setForgotMode();
      };
    }
    if (backLoginBtn) {
      backLoginBtn.onclick = () => setLoginMode();
    }

    // Show / hide password toggles (eye icons on Password + Confirm password)
    document.querySelectorAll("[data-toggle-password]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const inputId = btn.getAttribute("data-toggle-password");
        const input = document.getElementById(inputId);
        if (!input) return;
        const willShow = input.type === "password";
        input.type = willShow ? "text" : "password";
        setPasswordToggleUi(btn, willShow);
        input.focus({ preventScroll: true });
      });
    });

    const codeEl = document.getElementById("code");
    ["input", "change"].forEach((evt) => {
      emailEl.addEventListener(evt, updateSignupButtonState);
      passwordEl.addEventListener(evt, updateSignupButtonState);
      passwordConfirmEl.addEventListener(evt, updateSignupButtonState);
      if (codeEl) codeEl.addEventListener(evt, updateSignupButtonState);
      if (nicknameEl) nicknameEl.addEventListener(evt, updateSignupButtonState);
      if (schoolEl) schoolEl.addEventListener(evt, updateSignupButtonState);
      if (gradeEl) gradeEl.addEventListener(evt, updateSignupButtonState);
    });

    document.getElementById("auth-form").onsubmit = async (e) => {
      e.preventDefault();
      const email = emailEl.value.trim();
      const password = passwordEl.value;
      try {
        if (mode === "login") {
          showLoading("Signing you in…");
          try {
            await Auth.signIn(email, password);
            // H1/H2: one fast /me (no notices); Home uses cache via skipIfFresh
            await refreshProfile({ force: true, notices: false });
            toast("Welcome back!");
            navigate("home");
          } catch (loginErr) {
            // Restore auth form if sign-in fails mid-loading
            const authRoot = main();
            if (authRoot) {
              authRoot.innerHTML = viewAuth();
              bindAuth();
            }
            throw loginErr;
          }
        } else if (mode === "forgot") {
          if (!email || !emailEl.checkValidity()) {
            toast("Enter a valid email address.", true);
            return;
          }
          submit.disabled = true;
          const delivery = await Auth.forgotPassword(email);
          const dest =
            (delivery &&
              delivery.CodeDeliveryDetails &&
              delivery.CodeDeliveryDetails.Destination) ||
            "";
          toast(
            dest
              ? `Reset code sent to ${dest}. Check Inbox and Spam/Junk (sender may be Cognito/Amazon).`
              : "Reset code sent if the email is registered. Check Inbox and Spam/Junk."
          );
          if (forgotHint) {
            forgotHint.classList.remove("hidden");
            forgotHint.textContent = dest
              ? `Code delivered to ${dest}. Also check Spam. Then enter the code and a new password.`
              : "Check your email (including Spam). Then enter the code and a new password.";
          }
          setResetMode();
        } else if (mode === "reset") {
          const code = (document.getElementById("code")?.value || "").trim();
          if (!code) {
            toast("Enter the reset code from your email.", true);
            return;
          }
          if (!passwordPolicyOk(password) || password !== passwordConfirmEl.value) {
            updateSignupButtonState();
            toast("Passwords must match and meet the policy.", true);
            return;
          }
          submit.disabled = true;
          await Auth.confirmForgotPassword(email, code, password);
          toast("Password updated. You can log in with your new password.");
          setLoginMode();
        } else {
          const nickname = nicknameEl ? nicknameEl.value.trim() : "";
          const schoolId = schoolEl ? schoolEl.value.trim() : "";
          const grade = gradeEl ? gradeEl.value.trim() : "";
          if (!nickname) {
            toast("Please enter a name or nickname.", true);
            updateSignupButtonState();
            return;
          }
          if (!schoolId) {
            toast("Please select a school.", true);
            updateSignupButtonState();
            return;
          }
          if (!grade) {
            toast("Please select a grade.", true);
            updateSignupButtonState();
            return;
          }
          if (!passwordPolicyOk(password) || password !== passwordConfirmEl.value) {
            updateSignupButtonState();
            toast("Passwords must match and meet the policy.", true);
            return;
          }
          submit.disabled = true;
          // Cognito self-sign-up: ordinary user only (not in admin group)
          await Auth.signUp(email, password, { nickname });
          try {
            sessionStorage.setItem(PENDING_NICKNAME_KEY, nickname);
            sessionStorage.setItem(PENDING_SCHOOL_KEY, schoolId);
            sessionStorage.setItem(PENDING_GRADE_KEY, grade);
          } catch {
            /* private mode */
          }
          toast("Account created. Check your email for a confirmation code.");
          codeWrap.classList.remove("hidden");
          confirmBtn.classList.remove("hidden");
          submit.disabled = true;
        }
      } catch (err) {
        toast(err.message || String(err), true);
        if (mode === "signup" || mode === "reset") updateSignupButtonState();
        else submit.disabled = false;
      }
    };

    confirmBtn.onclick = async () => {
      const email = emailEl.value.trim();
      const code = document.getElementById("code").value.trim();
      if (!code) {
        toast("Enter the confirmation code from your email.", true);
        return;
      }
      try {
        await Auth.confirm(email, code);
        toast("Email confirmed — you can log in.");
        setLoginMode();
      } catch (err) {
        toast(err.message || String(err), true);
      }
    };
  }

  function currentAdminSubjectId() {
    const sel = document.getElementById("admin-subject-select");
    return (sel && sel.value) || state.adminSubjectId || null;
  }

  function currentAdminLevelId() {
    const sel = document.getElementById("admin-level-select");
    return (sel && sel.value) || state.adminLevelId || null;
  }

  /** Two-step confirm on a button (avoids blocked window.confirm). Supports icon buttons. */
  function armDangerButton(btn, armedLabel) {
    if (btn.dataset.armed === "1") return true;
    const originalHtml = btn.innerHTML;
    btn.dataset.armed = "1";
    btn.dataset.originalHtml = originalHtml;
    btn.dataset.originalLabel = btn.getAttribute("aria-label") || btn.textContent || "";
    btn.innerHTML = armedLabel || "✓?";
    btn.classList.add("danger-armed");
    window.setTimeout(() => {
      if (btn.dataset.armed === "1") {
        btn.dataset.armed = "0";
        btn.innerHTML = btn.dataset.originalHtml || originalHtml;
        btn.classList.remove("danger-armed");
      }
    }, 4000);
    toast("Tap the button again to confirm delete.");
    return false;
  }

  function restoreArmedButton(btn, fallbackLabel) {
    if (!btn) return;
    btn.dataset.armed = "0";
    if (btn.dataset.originalHtml) {
      btn.innerHTML = btn.dataset.originalHtml;
    } else if (fallbackLabel) {
      btn.textContent = fallbackLabel;
    }
    btn.classList.remove("danger-armed");
  }

  /** Inline SVG icons for admin action columns */
  function iconGear() {
    return `<svg class="action-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`;
  }
  function iconPencil() {
    return `<svg class="action-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
  }
  function iconTrash() {
    return `<svg class="action-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`;
  }
  function iconSave() {
    return `<svg class="action-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`;
  }

  function bindAdmin() {
    const root = main();

    const retryLoad = document.getElementById("admin-retry-load");
    if (retryLoad) {
      retryLoad.onclick = () => render();
    }

    // Schools: add / edit form
    const schoolForm = document.getElementById("admin-school-form");
    const schoolSaveBtn = document.getElementById("admin-school-save");
    const schoolCancelBtn = document.getElementById("admin-school-cancel");
    const schoolEditId = document.getElementById("school-edit-id");
    if (schoolForm) {
      schoolForm.onsubmit = async (e) => {
        e.preventDefault();
        const name = (document.getElementById("school-name")?.value || "").trim();
        const city = (document.getElementById("school-city")?.value || "").trim();
        const province = (document.getElementById("school-province")?.value || "").trim();
        if (!name) {
          toast("School name is required.", true);
          return;
        }
        const editId = (schoolEditId && schoolEditId.value) || "";
        if (schoolSaveBtn) schoolSaveBtn.disabled = true;
        try {
          if (editId) {
            await Api.updateSchool(token(), editId, { name, city, province });
            toast("School updated");
          } else {
            await Api.createSchool(token(), { name, city, province });
            toast("School added");
          }
          render();
        } catch (err) {
          toast(err.message || String(err), true);
          if (schoolSaveBtn) schoolSaveBtn.disabled = false;
        }
      };
    }
    if (schoolCancelBtn) {
      schoolCancelBtn.onclick = () => {
        if (schoolEditId) schoolEditId.value = "";
        const n = document.getElementById("school-name");
        const c = document.getElementById("school-city");
        const p = document.getElementById("school-province");
        if (n) n.value = "";
        if (c) c.value = "";
        if (p) p.value = "";
        if (schoolSaveBtn) schoolSaveBtn.textContent = "Add school";
        schoolCancelBtn.classList.add("hidden");
      };
    }

    // Single delegated click handler (more reliable than per-button onclick)
    root.onclick = async (ev) => {
      const t = ev.target;
      if (!(t instanceof Element)) return;
      const btn = t.closest("button");
      if (!btn || !root.contains(btn)) return;

      // Subject / level selectors are change events; handled below

      if (btn.hasAttribute("data-admin-approve-school")) {
        ev.preventDefault();
        const sid = btn.getAttribute("data-admin-approve-school");
        if (!sid) return;
        btn.disabled = true;
        try {
          await Api.approveSchool(token(), sid);
          toast("School approved. Requester profiles updated.");
          await render();
        } catch (err) {
          toast(err.message || String(err), true);
          btn.disabled = false;
        }
        return;
      }

      if (btn.hasAttribute("data-admin-edit-school")) {
        ev.preventDefault();
        try {
          const data = JSON.parse(
            decodeURIComponent(btn.getAttribute("data-admin-edit-school") || "")
          );
          if (schoolEditId) schoolEditId.value = data.school_id || "";
          const n = document.getElementById("school-name");
          const c = document.getElementById("school-city");
          const p = document.getElementById("school-province");
          if (n) n.value = data.name || "";
          if (c) c.value = data.city || "";
          if (p) p.value = data.province || "";
          if (schoolSaveBtn) schoolSaveBtn.textContent = "Save school";
          if (schoolCancelBtn) schoolCancelBtn.classList.remove("hidden");
          document.getElementById("admin-school-form")?.scrollIntoView({
            behavior: "smooth",
            block: "nearest",
          });
        } catch {
          toast("Could not open school editor.", true);
        }
        return;
      }

      if (btn.hasAttribute("data-admin-delete-school")) {
        ev.preventDefault();
        const sid = btn.getAttribute("data-admin-delete-school");
        if (!sid) return;
        if (!window.confirm("Delete this school? Learners who selected it keep their saved name.")) {
          return;
        }
        btn.disabled = true;
        try {
          await Api.deleteSchool(token(), sid);
          toast("School deleted");
          render();
        } catch (err) {
          toast(err.message || String(err), true);
          btn.disabled = false;
        }
        return;
      }

      if (btn.hasAttribute("data-admin-select-level")) {
        ev.preventDefault();
        state.adminLevelId = btn.getAttribute("data-admin-select-level");
        render();
        return;
      }

      if (btn.hasAttribute("data-admin-edit-level")) {
        ev.preventDefault();
        const editPanel = document.getElementById("admin-level-edit");
        try {
          const data = JSON.parse(
            decodeURIComponent(btn.getAttribute("data-admin-edit-level") || "")
          );
          document.getElementById("edit-lvl-id").value = data.level_id;
          document.getElementById("edit-lvl-id-label").textContent = data.level_id;
          document.getElementById("edit-lvl-name").value = data.name || "";
          document.getElementById("edit-lvl-desc").value = data.description || "";
          document.getElementById("edit-lvl-order").value = data.order;
          document.getElementById("edit-lvl-pass").value = data.pass_accuracy;
          document.getElementById("edit-lvl-minq").value = data.min_questions;
          if (editPanel) {
            editPanel.classList.remove("hidden");
            editPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        } catch (e) {
          toast("Could not open level editor.", true);
        }
        return;
      }

      if (btn.id === "admin-level-edit-cancel") {
        ev.preventDefault();
        document.getElementById("admin-level-edit")?.classList.add("hidden");
        return;
      }

      if (btn.id === "admin-level-save") {
        ev.preventDefault();
        const subjectId = currentAdminSubjectId();
        const lid = document.getElementById("edit-lvl-id")?.value;
        if (!subjectId || !lid) {
          toast("Missing subject or level id.", true);
          return;
        }
        btn.disabled = true;
        try {
          await Api.updateLevel(token(), subjectId, lid, {
            name: document.getElementById("edit-lvl-name").value.trim(),
            description: document.getElementById("edit-lvl-desc").value.trim(),
            order: parseInt(document.getElementById("edit-lvl-order").value, 10),
            pass_accuracy: parseFloat(document.getElementById("edit-lvl-pass").value),
            min_questions: parseInt(document.getElementById("edit-lvl-minq").value, 10),
          });
          toast("Level updated");
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
        }
        return;
      }

      if (btn.hasAttribute("data-admin-delete-level") || btn.id === "admin-delete-level-full") {
        ev.preventDefault();
        const levelId =
          btn.getAttribute("data-admin-delete-level") || currentAdminLevelId();
        const subjectId = currentAdminSubjectId();
        if (!subjectId) {
          toast("Select a subject first.", true);
          return;
        }
        if (!levelId) {
          toast("No level selected to delete.", true);
          return;
        }
        if (!armDangerButton(btn, "✓?")) return;
        btn.disabled = true;
        try {
          const r = await Api.deleteLevel(token(), subjectId, levelId);
          toast(
            `Level "${levelId}" deleted (cleared ${r.questions_cleared || 0} questions)`
          );
          if (state.adminLevelId === levelId) state.adminLevelId = null;
          state.adminSubjectId = subjectId;
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
          restoreArmedButton(btn);
        }
        return;
      }

      if (btn.id === "admin-clear-questions") {
        ev.preventDefault();
        const subjectId = currentAdminSubjectId();
        const levelId = currentAdminLevelId();
        if (!subjectId || !levelId) {
          toast("Select subject and level first.", true);
          return;
        }
        if (!armDangerButton(btn, "Confirm clear all?")) return;
        btn.disabled = true;
        try {
          const r = await Api.clearQuestions(token(), subjectId, levelId);
          toast(`Cleared ${r.cleared} questions from ${levelId}`);
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
          restoreArmedButton(btn, "Delete all questions on this level");
        }
        return;
      }

      if (btn.hasAttribute("data-admin-save-q")) {
        ev.preventDefault();
        const qid = btn.getAttribute("data-admin-save-q");
        const row = btn.closest("tr");
        const subjectId = currentAdminSubjectId();
        const levelId = currentAdminLevelId();
        if (!row || !subjectId || !levelId || !qid) {
          toast("Missing question context.", true);
          return;
        }
        const promptEl = row.querySelector(".q-prompt");
        const answerEl = row.querySelector(".q-answer");
        const prompt = promptEl ? promptEl.value.trim() : "";
        const answer = answerEl ? answerEl.value.trim() : "";
        if (!prompt || !answer) {
          toast("Prompt and answer are required.", true);
          return;
        }
        btn.disabled = true;
        try {
          await Api.updateQuestion(token(), subjectId, levelId, qid, {
            prompt,
            answer,
          });
          toast("Question saved");
          btn.disabled = false;
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
        }
        return;
      }

      if (btn.hasAttribute("data-admin-delete-q")) {
        ev.preventDefault();
        const qid = btn.getAttribute("data-admin-delete-q");
        const subjectId = currentAdminSubjectId();
        const levelId = currentAdminLevelId();
        if (!subjectId || !levelId || !qid) {
          toast("Missing question context.", true);
          return;
        }
        if (!armDangerButton(btn, "✓?")) return;
        btn.disabled = true;
        try {
          await Api.deleteQuestion(token(), subjectId, levelId, qid);
          toast("Question deleted");
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
          restoreArmedButton(btn);
        }
        return;
      }

      if (btn.id === "admin-seed") {
        ev.preventDefault();
        btn.disabled = true;
        try {
          const r = await Api.adminSeed(token());
          toast(
            `Seed done: subject_created=${r.subject_created}, levels=${r.levels_created}, questions=${r.questions_imported}`
          );
          if (!state.adminSubjectId) state.adminSubjectId = "math";
          render();
        } catch (e) {
          toast(e.message || String(e), true);
          btn.disabled = false;
        }
      }
    };

    const subjectSelect = document.getElementById("admin-subject-select");
    const levelSelect = document.getElementById("admin-level-select");

    if (subjectSelect) {
      subjectSelect.onchange = () => {
        state.adminSubjectId = subjectSelect.value || null;
        state.adminLevelId = null;
        render();
      };
    }
    if (levelSelect) {
      levelSelect.onchange = () => {
        state.adminLevelId = levelSelect.value || null;
        render();
      };
    }

    const subjectForm = document.getElementById("admin-subject-form");
    if (subjectForm) {
      subjectForm.onsubmit = async (e) => {
        e.preventDefault();
        try {
          const category = document.getElementById("subj-category").value;
          const topic = document.getElementById("subj-topic").value.trim();
          if (!topic) {
            toast("Topic is required.", true);
            return;
          }
          const gradeLevel = (
            document.getElementById("subj-grade-level")?.value || ""
          ).trim();
          const created = await Api.createSubject(token(), {
            category,
            topic,
            description: document.getElementById("subj-desc").value.trim(),
            sort_order: parseInt(document.getElementById("subj-order").value, 10) || 0,
            grade_level: gradeLevel || null,
          });
          toast(`Subject "${subjectDisplayLabel(created)}" created`);
          state.adminSubjectId = created.subject_id;
          state.adminLevelId = null;
          render();
        } catch (err) {
          toast(err.message || String(err), true);
        }
      };
    }

    const editSubjectBtn = document.getElementById("admin-subject-edit");
    if (editSubjectBtn) {
      editSubjectBtn.onclick = async (e) => {
        e.preventDefault();
        const subjectId = currentAdminSubjectId();
        if (!subjectId) {
          toast("Select a working subject first.", true);
          return;
        }
        const category = document.getElementById("subj-category")?.value;
        const topic = document.getElementById("subj-topic")?.value.trim();
        if (!topic) {
          toast("Topic is required.", true);
          return;
        }
        editSubjectBtn.disabled = true;
        try {
          const gradeLevel = (
            document.getElementById("subj-grade-level")?.value || ""
          ).trim();
          const payload = {
            category,
            topic,
            description: document.getElementById("subj-desc")?.value.trim() || "",
            sort_order: parseInt(document.getElementById("subj-order")?.value, 10) || 0,
            grade_level: gradeLevel,
          };
          // Prefer Api.updateSubject; fall back if a stale-cached api.js is loaded
          const updated =
            typeof Api.updateSubject === "function"
              ? await Api.updateSubject(token(), subjectId, payload)
              : await updateSubjectFallback(subjectId, payload);
          toast(`Updated to "${subjectDisplayLabel(updated)}"`);
          // Re-render so impact areas refresh: Working subject dropdown,
          // Levels · label (impact 1), and SUBJECT column tags (impact 2)
          state.adminSubjectId = updated.subject_id || subjectId;
          render();
        } catch (err) {
          const msg = err && err.message ? String(err.message) : String(err);
          // API Gateway returns generic statusText when Lambda times out
          if (
            /request failed|internal server|status/i.test(msg) ||
            err.status === 502 ||
            err.status === 504 ||
            err.status === 503
          ) {
            toast(
              "Save timed out or failed. Try again — large question banks no longer block Edit Subject.",
              true
            );
          } else {
            toast(msg, true);
          }
          editSubjectBtn.disabled = false;
        }
      };
    }

    const levelForm = document.getElementById("admin-level-form");
    if (levelForm) {
      levelForm.onsubmit = async (e) => {
        e.preventDefault();
        const subjectId = currentAdminSubjectId();
        if (!subjectId) {
          toast("Select a subject first.", true);
          return;
        }
        try {
          const created = await Api.createLevel(token(), subjectId, {
            level_id: document.getElementById("lvl-id").value.trim(),
            name: document.getElementById("lvl-name").value.trim(),
            description: document.getElementById("lvl-desc").value.trim(),
            order: parseInt(document.getElementById("lvl-order").value, 10),
            pass_accuracy: parseFloat(document.getElementById("lvl-pass").value),
            min_questions: parseInt(document.getElementById("lvl-minq").value, 10),
          });
          toast(`Level "${created.level_id}" created`);
          state.adminSubjectId = subjectId;
          state.adminLevelId = created.level_id;
          render();
        } catch (err) {
          toast(err.message || String(err), true);
        }
      };
    }

    const csvFile = document.getElementById("csv-file");
    const csvText = document.getElementById("csv-text");
    if (csvFile && csvText) {
      csvFile.onchange = async () => {
        const file = csvFile.files && csvFile.files[0];
        if (!file) return;
        try {
          csvText.value = await file.text();
          toast(`Loaded ${file.name} into the CSV box`);
        } catch {
          toast("Could not read CSV file.", true);
        }
      };
    }

    const csvForm = document.getElementById("admin-csv-form");
    if (csvForm) {
      csvForm.onsubmit = async (e) => {
        e.preventDefault();
        const subjectId = currentAdminSubjectId();
        const levelId = currentAdminLevelId();
        if (!subjectId) {
          toast("Select a subject first.", true);
          return;
        }
        if (!levelId) {
          toast("Select a working level for CSV import (or import Excel to create levels).", true);
          return;
        }
        const text = (csvText && csvText.value) || "";
        if (!text.trim()) {
          toast("Paste CSV or choose a CSV file first.", true);
          return;
        }
        const replace = Boolean(
          document.getElementById("csv-replace") &&
            document.getElementById("csv-replace").checked
        );
        const submitBtn = document.getElementById("admin-csv-submit");
        if (replace) {
          if (submitBtn && !armDangerButton(submitBtn, "Confirm replace CSV?")) {
            return;
          }
        }
        if (submitBtn) submitBtn.disabled = true;
        try {
          const summary = await Api.uploadQuestionsCsv(
            token(),
            subjectId,
            levelId,
            text,
            replace
          );
          toast(
            `CSV: imported ${summary.imported} questions` +
              (replace ? ` (replaced; cleared ${summary.cleared || 0})` : "") +
              ` · total ${summary.question_count}`
          );
          if (csvFile) csvFile.value = "";
          if (csvText) csvText.value = "";
          const rep = document.getElementById("csv-replace");
          if (rep) rep.checked = false;
          state.adminSubjectId = subjectId;
          state.adminLevelId = levelId;
          render();
        } catch (err) {
          toast(err.message || String(err), true);
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.dataset.armed = "0";
            submitBtn.textContent = submitBtn.dataset.originalLabel || "Import CSV";
            submitBtn.classList.remove("danger-armed");
          }
        }
      };
    }

    const excelForm = document.getElementById("admin-excel-form");
    if (excelForm) {
      excelForm.onsubmit = async (e) => {
        e.preventDefault();
        const subjectId = currentAdminSubjectId();
        if (!subjectId) {
          toast("Select a subject first.", true);
          return;
        }
        const excelInput = document.getElementById("excel-file");
        const file = excelInput && excelInput.files && excelInput.files[0];
        if (!file) {
          toast("Choose an Excel file (.xlsx or .xls).", true);
          return;
        }
        const replace = Boolean(
          document.getElementById("excel-replace") &&
            document.getElementById("excel-replace").checked
        );
        const submitBtn = document.getElementById("admin-excel-submit");
        const logEl = document.getElementById("excel-import-log");
        if (submitBtn) submitBtn.disabled = true;
        if (logEl) {
          logEl.classList.remove("hidden");
          logEl.textContent = "Importing workbook…";
        }
        try {
          // Fetch current levels for order baseline
          let existingLevels = [];
          try {
            const lv = await Api.listLevels(token(), subjectId);
            existingLevels = lv.levels || [];
          } catch (_) {
            existingLevels = [];
          }
          const excelGrade = (
            document.getElementById("excel-grade-level")?.value || ""
          ).trim();
          if (excelGrade) {
            try {
              if (typeof Api.updateSubject === "function") {
                await Api.updateSubject(token(), subjectId, {
                  grade_level: excelGrade,
                });
              } else {
                await updateSubjectFallback(subjectId, {
                  grade_level: excelGrade,
                });
              }
            } catch (gradeErr) {
              toast(
                `Import OK but grade tag failed: ${gradeErr.message || gradeErr}`,
                true
              );
            }
          }
          const results = await importExcelWorkbook(file, subjectId, {
            replace,
            existingLevels,
          });
          const okCount = results.filter((r) => r.imported != null).length;
          const errCount = results.filter((r) => r.error).length;
          const skipCount = results.filter((r) => r.skipped).length;
          const lines = results.map((r) => {
            if (r.skipped) return `• ${r.sheet}: skipped (${r.reason})`;
            if (r.error) return `• ${r.sheet}: ERROR ${r.error}`;
            return `• ${r.sheet} → level ${r.level_id}: +${r.imported} questions (total ${r.question_count})${r.created_level ? " [new level]" : ""}`;
          });
          if (logEl) logEl.innerHTML = lines.map((l) => escapeHtml(l)).join("<br>");
          toast(
            `Excel import finished: ${okCount} sheet(s) ok, ${skipCount} skipped, ${errCount} error(s)`
          );
          if (excelInput) excelInput.value = "";
          // Focus first imported level
          const firstOk = results.find((r) => r.level_id && r.imported != null);
          state.adminSubjectId = subjectId;
          if (firstOk) state.adminLevelId = firstOk.level_id;
          render();
        } catch (err) {
          toast(err.message || String(err), true);
          if (logEl) logEl.textContent = err.message || String(err);
          if (submitBtn) submitBtn.disabled = false;
        }
      };
    }
  }

  function startStudyCountdown() {
    clearStudyTimers();
    state.countdownValue = 3;
    const paint = () => {
      const el = document.getElementById("countdown-num");
      if (!el) return;
      el.textContent = state.countdownValue > 0 ? String(state.countdownValue) : "Go!";
      el.classList.remove("pop");
      void el.offsetWidth;
      el.classList.add("pop");
    };
    paint();
    state.countdownTimer = setInterval(() => {
      state.countdownValue -= 1;
      if (state.countdownValue <= 0) {
        clearInterval(state.countdownTimer);
        state.countdownTimer = null;
        paint();
        window.setTimeout(() => beginStudyAnswering(), 450);
        return;
      }
      paint();
    }, 1000);
  }

  function beginStudyAnswering() {
    state.studyPhase = "answering";
    state.timerAccumulatedMs = 0;
    state.timerRunningSince = null;
    state.studyTransitioning = false;
    setStudyModeUi(true);
    bindStudyKeyboardAvoidance();

    // Reveal question area without full app re-mount if shell exists
    const overlay = document.getElementById("countdown-overlay");
    if (overlay) overlay.classList.add("hidden");
    const qWrap = document.getElementById("study-question-wrap");
    if (qWrap) qWrap.classList.remove("study-obscured");
    const form = document.getElementById("answer-form");
    if (form) form.classList.remove("study-obscured");
    const actions = document.getElementById("study-actions");
    if (actions) actions.classList.remove("hidden");
    const input = document.getElementById("answer");
    if (input) input.disabled = false;
    const nextBtn = document.getElementById("study-next-btn");
    if (nextBtn) nextBtn.disabled = false;
    const shell = document.getElementById("study-shell");
    if (shell) shell.classList.remove("study-countdown-active");

    // If shell not present (edge case), fall back to full render once
    if (!document.getElementById("study-shell")) {
      render();
    } else {
      showQuestionInPlace(0);
      bindStudySessionControls();
    }

    keepAnswerKeyboardOpen();
    resumeStudyTimer();
    // Open keypad once answering starts
    window.setTimeout(() => focusAnswerField(), 30);
    window.setTimeout(() => focusAnswerField(), 200);
    updateStudySubmitEnabled();
  }

  function updateStudySubmitEnabled() {
    const s = state.session;
    if (!s || state.studyPhase !== "answering") return;
    const total = (s.questions || []).length;
    const isLast = state.qIndex >= total - 1;
    const btn = document.getElementById("study-next-btn");
    if (!btn) return;
    if (!isLast) {
      btn.disabled = false;
      btn.textContent = "Next →";
      return;
    }
    const priorOk = (s.questions || []).slice(0, -1).every((q) =>
      Object.prototype.hasOwnProperty.call(state.clientAnswers, q.question_id)
    );
    btn.disabled = !priorOk || state.studyTransitioning;
    btn.textContent = "Submit ✓";
  }

  function bindStudySessionControls() {
    if (!state.session || !state.studyPhase || state.studyPhase === "results") {
      return;
    }

    if (state.studyPhase === "countdown") {
      return;
    }

    const answerForm = document.getElementById("answer-form");
    const input = document.getElementById("answer");
    const nextBtn = document.getElementById("study-next-btn");
    if (!answerForm || !input || !nextBtn) return;

    // Avoid stacking duplicate handlers on re-bind
    if (answerForm.dataset.bound === "1") {
      updateStudySubmitEnabled();
      keepAnswerKeyboardOpen();
      return;
    }
    answerForm.dataset.bound = "1";

    updateStudySubmitEnabled();
    keepAnswerKeyboardOpen();
    input.addEventListener("input", () => updateStudySubmitEnabled());
    // If the OS blurs the field (e.g. after animation), reopen the keypad while answering
    input.addEventListener("blur", () => {
      if (state.studyPhase !== "answering" || state.studyTransitioning) return;
      // Defer so button-tap submit can complete without fighting blur
      window.setTimeout(() => {
        if (state.studyPhase === "answering" && !state.studyTransitioning) {
          const active = document.activeElement;
          if (active !== input && active !== nextBtn) {
            focusAnswerField();
          }
        }
      }, 40);
    });

    answerForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (state.studyPhase !== "answering" || !state.session || state.studyTransitioning) return;

      const questions = state.session.questions || [];
      const q = questions[state.qIndex];
      if (!q) return;

      const isLast = state.qIndex >= questions.length - 1;
      let raw = input.value.trim();
      if (!raw) raw = "0";

      if (isLast && nextBtn.disabled) {
        toast("Answer the earlier questions first, then submit.", true);
        focusAnswerField();
        return;
      }

      state.clientAnswers[q.question_id] = raw;

      if (!isLast) {
        // Pause timer for transition (loading next question not counted)
        pauseStudyTimer();
        state.studyTransitioning = true;
        // Do NOT disable nextBtn — disabling steals focus and collapses the keypad

        const qa = document.getElementById("study-qa");
        if (qa) qa.classList.add("study-swap");

        // Keep focus on the answer field across the swap
        focusAnswerField();

        // Wait for paint/transition frame(s) only — no full page render
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

        showQuestionInPlace(state.qIndex + 1);
        // Clear previous answer for the new question (showQuestionInPlace restores saved)
        // Ensure field is empty when no saved answer and keep keypad
        if (!Object.prototype.hasOwnProperty.call(state.clientAnswers, questions[state.qIndex]?.question_id)) {
          if (input.value !== "") input.value = "";
        }
        focusAnswerField();

        if (qa) {
          // brief animation then clear
          window.setTimeout(() => {
            qa.classList.remove("study-swap");
            focusAnswerField();
          }, 180);
        }

        state.studyTransitioning = false;
        updateStudySubmitEnabled();
        // Resume active answering time only after next question is on screen
        resumeStudyTimer();
        focusAnswerField();
        return;
      }

      // Final submit — batch to server (pause so submit network time is not counted)
      pauseStudyTimer();
      nextBtn.disabled = true;
      nextBtn.textContent = "Submitting…";
      const totalElapsed = getStudyElapsedMs();
      clearStudyTimers();

      const answers = questions.map((qq) => ({
        question_id: qq.question_id,
        answer: Object.prototype.hasOwnProperty.call(state.clientAnswers, qq.question_id)
          ? state.clientAnswers[qq.question_id]
          : "0",
      }));

      try {
        let res;
        if (state.session.is_assessment) {
          res = await Api.completeAssessment(token(), state.session.session_id, {
            total_elapsed_ms: totalElapsed,
            answers,
          });
          state.studyPhase = "results";
          state.studyResults = res;
          // Progress / radar / profile stats out of date
          StudyCache.invalidateLanding();
          ProfileCache.invalidate();
          InsightsCache.invalidate();
        } else {
          res = await Api.completeSession(token(), state.session.session_id, {
            total_elapsed_ms: totalElapsed,
            answers,
          });
          // Enrich results with band/topic completion so messaging is accurate
          let setContext = null;
          try {
            if (res.subject_id && res.level_id) {
              setContext = await getSetCompletionContext(res.subject_id, res.level_id);
            }
          } catch (_) {
            setContext = null;
          }
          state.studyPhase = "results";
          state.studyResults = { ...res, setContext };
          StudyCache.invalidateLanding();
          ProfileCache.invalidate();
          InsightsCache.invalidate();
        }
        setStudyModeUi(false);
        document.documentElement.style.setProperty("--vv-keyboard", "0px");
        render();
      } catch (err) {
        toast(err.message || String(err), true);
        nextBtn.disabled = false;
        nextBtn.textContent = "Submit";
        // Resume timer after failed submit so learner can continue
        state.timerAccumulatedMs = totalElapsed;
        state.timerRunningSince = null;
        resumeStudyTimer();
      }
    });
  }

  /**
   * Start a study session for subject/level and enter countdown.
   * Shared by level list Start and results "Next Set".
   */
  async function beginLevelSession(subjectId, levelId) {
    clearStudyTimers();
    const session = await Api.startSession(token(), {
      subject_id: subjectId,
      level_id: levelId,
    });
    state.session = session;
    state.studyPhase = "countdown";
    state.qIndex = 0;
    state.clientAnswers = {};
    state.timerAccumulatedMs = 0;
    state.timerRunningSince = null;
    state.studyResults = null;
    state.studyTransitioning = false;
    state.countdownValue = 3;
    setStudyModeUi(true);
    render();
    bindStudyKeyboardAvoidance();
    startStudyCountdown();
    return session;
  }

  /** Start placement assessment for a subject (all levels, 10q each). */
  async function beginAssessmentSession(subjectId) {
    clearStudyTimers();
    const session = await Api.startAssessment(token(), {
      subject_id: subjectId,
    });
    state.session = { ...session, is_assessment: true };
    state.studyPhase = "countdown";
    state.qIndex = 0;
    state.clientAnswers = {};
    state.timerAccumulatedMs = 0;
    state.timerRunningSince = null;
    state.studyResults = null;
    state.studyTransitioning = false;
    state.countdownValue = 3;
    state.route = "assessment";
    setStudyModeUi(true);
    render();
    bindStudyKeyboardAvoidance();
    startStudyCountdown();
    return session;
  }

  /**
   * Resolve the next incomplete set (by admin order). Prefers remaining sets
   * in the same Level N band, then any unfinished set in the topic.
   */
  async function findNextLevel(subjectId, currentLevelId) {
    const ctx = await getSetCompletionContext(subjectId, currentLevelId);
    return ctx.next || null;
  }

  function bindView() {
    main().querySelectorAll("[data-go]").forEach((b) => {
      b.onclick = () => navigate(b.dataset.go);
    });

    const lbCsv = document.getElementById("btn-leaderboard-csv");
    if (lbCsv) {
      lbCsv.onclick = () => downloadLeaderboardCsv(state.leaderboardEntries);
    }

    // Insights → Study deep link for a specific question set
    main().querySelectorAll("[data-study-level]").forEach((a) => {
      a.onclick = (ev) => {
        ev.preventDefault();
        const subjectId = a.getAttribute("data-study-subject");
        const levelId = a.getAttribute("data-study-level");
        const category = a.getAttribute("data-study-category");
        if (subjectId) state.studySubjectId = subjectId;
        if (category) state.studyCategory = category;
        if (levelId) state.studyFocusLevelId = levelId;
        navigate("study");
      };
    });

    main().querySelectorAll("[data-start]").forEach((b) => {
      b.onclick = async () => {
        try {
          await beginLevelSession(b.dataset.start, b.dataset.level);
        } catch (e) {
          toast(e.message, true);
          if (e.status === 402) navigate("pay");
        }
      };
    });

    // Study page: Category + Topic combos (same model as Admin subjects)
    const studyCat = document.getElementById("study-category");
    const studyTopic = document.getElementById("study-topic");
    if (studyCat) {
      studyCat.onchange = () => {
        state.studyCategory = studyCat.value || null;
        state.studySubjectId = null; // pick first topic in new category
        render();
      };
    }
    if (studyTopic) {
      studyTopic.onchange = () => {
        state.studySubjectId = studyTopic.value || null;
        render();
      };
    }

    // Assessment setup: Category + base Topic + Start
    const assessCat = document.getElementById("assess-category");
    const assessTopic = document.getElementById("assess-topic");
    if (assessCat) {
      assessCat.onchange = () => {
        state.assessmentCategory = assessCat.value || null;
        state.assessmentBaseTopic = null;
        state.assessmentSubjectId = null;
        state.assessmentPreview = null;
        render();
      };
    }
    if (assessTopic) {
      assessTopic.onchange = () => {
        // Value is base topic name (e.g. "Arithmetic (Addition)"), not subject_id
        state.assessmentBaseTopic = assessTopic.value || null;
        state.assessmentSubjectId = null;
        state.assessmentPreview = null;
        render();
      };
    }
    const startAssessBtn = document.getElementById("start-assessment");
    if (startAssessBtn) {
      startAssessBtn.onclick = async () => {
        const subjectId = startAssessBtn.dataset.subject;
        if (!subjectId) {
          toast("Pick a topic first.", true);
          return;
        }
        startAssessBtn.disabled = true;
        const prev = startAssessBtn.textContent;
        startAssessBtn.textContent = "Preparing…";
        try {
          showLoading("Preparing your assessment…");
          await beginAssessmentSession(subjectId);
        } catch (e) {
          toast(e.message || String(e), true);
          startAssessBtn.disabled = false;
          startAssessBtn.textContent = prev || "Start Assessment";
          render();
        }
      };
    }
    const assessGoStudy = document.getElementById("assessment-go-study");
    if (assessGoStudy) {
      assessGoStudy.onclick = () => {
        const subjectId = assessGoStudy.dataset.subject;
        const levelId = assessGoStudy.dataset.level;
        if (subjectId) state.studySubjectId = subjectId;
        if (levelId) state.studyFocusLevelId = levelId;
        // Clear assessment session so Study shows level list
        resetStudyState();
        navigate("study");
      };
    }
    const assessRetake = document.getElementById("assessment-retake");
    if (assessRetake) {
      assessRetake.onclick = async () => {
        const subjectId = assessRetake.dataset.subject;
        if (!subjectId) {
          resetStudyState();
          render();
          return;
        }
        assessRetake.disabled = true;
        try {
          showLoading("Preparing your assessment…");
          await beginAssessmentSession(subjectId);
        } catch (e) {
          toast(e.message || String(e), true);
          resetStudyState();
          render();
        }
      };
    }

    bindStudySessionControls();

    const retakeBtn = document.getElementById("retake-set");
    if (retakeBtn) {
      retakeBtn.onclick = async () => {
        const subjectId = retakeBtn.dataset.subject;
        const levelId = retakeBtn.dataset.level;
        if (!subjectId || !levelId) {
          toast("Missing level context.", true);
          return;
        }
        retakeBtn.disabled = true;
        const prev = retakeBtn.textContent;
        retakeBtn.textContent = "Loading…";
        const nextSetBtn = document.getElementById("next-set");
        if (nextSetBtn) nextSetBtn.disabled = true;
        try {
          await beginLevelSession(subjectId, levelId);
        } catch (e) {
          toast(e.message || String(e), true);
          if (e.status === 402) navigate("pay");
          retakeBtn.disabled = false;
          retakeBtn.textContent = prev || "Retake";
          if (nextSetBtn && nextSetBtn.dataset.nextMode !== "done") {
            nextSetBtn.disabled = false;
          }
        }
      };
    }

    const nextSetBtn = document.getElementById("next-set");
    if (nextSetBtn) {
      const subjectId = nextSetBtn.dataset.subject;
      const levelId = nextSetBtn.dataset.level;
      const nextMode = nextSetBtn.dataset.nextMode || "next";
      const prefilledNext = nextSetBtn.dataset.nextLevel || "";

      nextSetBtn.onclick = async () => {
        if (!subjectId || !levelId) {
          toast("Missing level context.", true);
          return;
        }
        if (nextMode === "done" || nextSetBtn.disabled) {
          return;
        }
        nextSetBtn.disabled = true;
        if (retakeBtn) retakeBtn.disabled = true;
        const prevLabel = nextSetBtn.textContent;
        nextSetBtn.textContent = "Loading…";
        try {
          if (nextMode === "retry") {
            await beginLevelSession(subjectId, levelId);
            return;
          }
          let targetId = prefilledNext;
          if (!targetId) {
            const next = await findNextLevel(subjectId, levelId);
            targetId = next && next.level_id;
          }
          if (!targetId) {
            toast("No unfinished sets left — great work!");
            nextSetBtn.textContent = "All sets done ✓";
            const hint = document.querySelector(".results-next-set-hint");
            if (hint) {
              hint.textContent = "You completed every question set — great work!";
            }
            if (retakeBtn) retakeBtn.disabled = false;
            return;
          }
          await beginLevelSession(subjectId, targetId);
        } catch (e) {
          toast(e.message || String(e), true);
          if (e.status === 402) navigate("pay");
          nextSetBtn.disabled = false;
          nextSetBtn.textContent = prevLabel || "Next Set →";
          if (retakeBtn) retakeBtn.disabled = false;
        }
      };
    }

    const endBtn = document.getElementById("end-session");
    if (endBtn) {
      endBtn.onclick = () => {
        resetStudyState();
        render();
      };
    }

    const taskForm = document.getElementById("task-form");
    if (taskForm) {
      taskForm.onsubmit = async (e) => {
        e.preventDefault();
        try {
          await Api.createTask(token(), {
            title: document.getElementById("task-title").value,
            description: document.getElementById("task-desc").value,
          });
          toast("Task added");
          render();
        } catch (err) {
          toast(err.message, true);
        }
      };
    }

    main().querySelectorAll("[data-toggle]").forEach((cb) => {
      cb.onchange = async () => {
        try {
          await Api.updateTask(token(), cb.dataset.toggle, { completed: cb.checked });
          render();
        } catch (err) {
          toast(err.message, true);
        }
      };
    });

    main().querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = async () => {
        try {
          await Api.deleteTask(token(), b.dataset.del);
          toast("Deleted");
          render();
        } catch (err) {
          toast(err.message, true);
        }
      };
    });

    const payForm = document.getElementById("pay-form");
    if (payForm) {
      payForm.onsubmit = async (e) => {
        e.preventDefault();
        try {
          await Api.submitPayment(token(), {
            gcash_reference: document.getElementById("gcash_ref").value.trim(),
            amount_php: parseFloat(document.getElementById("amount").value),
          });
          toast("Payment submitted — pending admin verification");
          navigate("account");
        } catch (err) {
          toast(err.message, true);
        }
      };
    }
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  /** Map Excel sheet / display name to a valid level_id. */
  function slugifyLevelId(name) {
    let s = String(name || "")
      .trim()
      .replace(/\s+/g, "-")
      .replace(/[^a-zA-Z0-9_-]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^[-_]+|[-_]+$/g, "");
    if (!s) s = "level";
    if (!/^[a-zA-Z0-9]/.test(s)) s = `L-${s}`;
    return s.slice(0, 64);
  }

  /** Convert SheetJS rows (array of arrays) to CSV text for the API. */
  function rowsToCsvText(rows) {
    const lines = [];
    for (const row of rows || []) {
      if (!row || !row.length) continue;
      const cells = row.map((c) => {
        if (c === null || c === undefined) return "";
        // Excel numbers must stay as plain digits (no locale commas)
        if (typeof c === "number" && Number.isFinite(c)) {
          return Number.isInteger(c) ? String(c) : String(c);
        }
        return String(c).trim();
      });
      if (cells.every((c) => !c)) continue;
      // Skip header-like rows
      const first = cells[0].toLowerCase();
      if (["operand1", "question", "q", "left", "prompt"].includes(first)) continue;
      // Prefer 5-col arithmetic rows; drop trailing empties that confuse parsers
      let use = cells;
      if (cells.length >= 5) {
        use = cells.slice(0, 5);
      } else if (cells.length >= 2) {
        // keep as question,answer — but skip incomplete operator rows like a,+,b without answer
        if (cells.length === 3 && (cells[1] === "+" || cells[1] === "-" || cells[1] === "*" || cells[1] === "/")) {
          continue;
        }
        use = cells;
      } else {
        continue;
      }
      if (!use[0] || !use[use.length - 1]) continue;
      lines.push(use.join(","));
    }
    return lines.join("\n");
  }

  async function ensureLevelForImport(subjectId, levelId, levelName, order) {
    try {
      await Api.createLevel(token(), subjectId, {
        level_id: levelId,
        name: String(levelName).slice(0, 100),
        description: "",
        order,
        pass_accuracy: 0.8,
        min_questions: 5,
      });
      return { created: true };
    } catch (e) {
      const msg = (e && e.message) || String(e);
      // Level already present — update name/order so re-import keeps sequence consistent
      if (/already exists/i.test(msg)) {
        try {
          await Api.updateLevel(token(), subjectId, levelId, {
            name: String(levelName).slice(0, 100),
            order,
          });
          return { created: false, existing: true, order_updated: true };
        } catch (updErr) {
          // Still allow question import even if order update fails
          return {
            created: false,
            existing: true,
            order_error: (updErr && updErr.message) || String(updErr),
          };
        }
      }
      throw e;
    }
  }

  async function importExcelWorkbook(file, subjectId, { replace = true, existingLevels = [] } = {}) {
    if (typeof XLSX === "undefined") {
      throw new Error("Excel library (SheetJS) failed to load. Refresh the page and try again.");
    }
    const buf = await file.arrayBuffer();
    let workbook;
    try {
      workbook = XLSX.read(buf, { type: "array" });
    } catch (e) {
      throw new Error(
        "Could not read Excel file. Save as .xlsx and try again. (" +
          ((e && e.message) || String(e)) +
          ")"
      );
    }
    if (!workbook.SheetNames || !workbook.SheetNames.length) {
      throw new Error("Excel workbook has no worksheets.");
    }

    // Pre-compute level ids for sheets that will import so we can place this
    // workbook's orders contiguously after levels outside the workbook.
    const planned = [];
    for (let i = 0; i < workbook.SheetNames.length; i++) {
      const sheetName = workbook.SheetNames[i];
      const sheet = workbook.Sheets[sheetName];
      if (!sheet) continue;
      const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: "", raw: false });
      const csv = rowsToCsvText(rows);
      if (!csv.trim()) {
        planned.push({ sheetName, skipped: true, reason: "empty or no valid question rows" });
        continue;
      }
      planned.push({
        sheetName,
        sheet,
        csv,
        levelId: slugifyLevelId(sheetName),
      });
    }
    const importIds = new Set(planned.filter((p) => p.levelId).map((p) => p.levelId));
    const maxOutside = existingLevels
      .filter((lv) => !importIds.has(lv.level_id))
      .reduce((m, lv) => Math.max(m, Number(lv.order) || 0), 0);

    const results = [];
    const logEl = document.getElementById("excel-import-log");
    const totalSheets = workbook.SheetNames.length;
    let importSeq = 0; // 1-based among non-skipped sheets

    for (let i = 0; i < planned.length; i++) {
      const p = planned[i];
      const sheetName = p.sheetName;
      if (logEl) {
        logEl.classList.remove("hidden");
        logEl.textContent = `Importing sheet ${i + 1}/${planned.length}: ${sheetName}…`;
      }
      if (p.skipped) {
        results.push({ sheet: sheetName, skipped: true, reason: p.reason });
        continue;
      }
      importSeq += 1;
      // Contiguous order for this workbook: outsideMax+1, +2, … (stable on re-import)
      const desiredOrder = maxOutside + importSeq;
      const levelId = p.levelId;
      let created = false;
      try {
        const ens = await ensureLevelForImport(subjectId, levelId, sheetName, desiredOrder);
        created = Boolean(ens.created);
      } catch (e) {
        results.push({
          sheet: sheetName,
          level_id: levelId,
          error: e.message || String(e),
        });
        continue;
      }
      try {
        const summary = await Api.uploadQuestionsCsv(
          token(),
          subjectId,
          levelId,
          p.csv,
          replace
        );
        results.push({
          sheet: sheetName,
          level_id: levelId,
          level_name: sheetName,
          created_level: created,
          order: desiredOrder,
          imported: summary.imported,
          question_count: summary.question_count,
          replaced: summary.replaced,
          cleared: summary.cleared,
        });
      } catch (e) {
        results.push({
          sheet: sheetName,
          level_id: levelId,
          error: e.message || String(e),
        });
      }
    }
    return results;
  }

  function init() {
    nav().querySelectorAll("button[data-route]").forEach((b) => {
      b.onclick = () => navigate(b.dataset.route);
    });
    bindProfileMenu();

    if (Auth.isLoggedIn()) {
      setNavVisible(true);
      showLoading("Welcome back…");
      // H1/H2: single fast profile fetch; Home will skipIfFresh
      refreshProfile({ force: true, notices: false })
        .then(() => {
          updateNavProfileAvatar();
          navigate("home");
        })
        .catch(() => {
          render();
        });
    } else {
      render();
    }
  }

  return { init, navigate };
})();

document.addEventListener("DOMContentLoaded", () => App.init());
