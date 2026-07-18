/* STEM Study SPA – mobile-first */
const App = (() => {
  const main = () => document.getElementById("main");
  const nav = () => document.getElementById("nav");
  let state = {
    route: "home",
    profile: null,
    session: null,
    qIndex: 0,
    answerStartedAt: 0,
  };

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
  }

  function navigate(route) {
    state.route = route;
    nav().querySelectorAll("button[data-route]").forEach((b) => {
      b.classList.toggle("active", b.dataset.route === route);
    });
    render();
  }

  async function refreshProfile() {
    if (!Auth.isLoggedIn()) {
      state.profile = null;
      return;
    }
    try {
      state.profile = await Api.me(token());
    } catch (e) {
      if (e.status === 401) {
        Auth.signOut();
        state.profile = null;
      }
    }
  }

  /* ---------- Views ---------- */

  function viewAuth() {
    return `
      <div class="card">
        <h1>Welcome</h1>
        <p class="muted">Sign up for a 1‑month free trial. Study math levels at your pace.</p>
        <div class="tabs">
          <button type="button" id="tab-login" class="active">Log in</button>
          <button type="button" id="tab-signup">Sign up</button>
        </div>
        <form id="auth-form" class="stack">
          <div>
            <label for="email">Email</label>
            <input id="email" type="email" autocomplete="username" required />
          </div>
          <div>
            <label for="password">Password</label>
            <input id="password" type="password" autocomplete="current-password" required minlength="8" />
          </div>
          <div id="confirm-wrap" class="hidden">
            <label for="code">Confirmation code</label>
            <input id="code" type="text" inputmode="numeric" />
            <p class="muted">Check your email after sign-up, then enter the code and tap Confirm.</p>
          </div>
          <button class="btn block" type="submit" id="auth-submit">Log in</button>
          <button class="btn secondary block hidden" type="button" id="auth-confirm">Confirm email</button>
        </form>
      </div>`;
  }

  function viewHome() {
    const p = state.profile || {};
    const sub = p.subscription_active
      ? `<span class="badge ok">${p.subscription_status}</span>`
      : `<span class="badge warn">${p.subscription_status || "unknown"}</span>`;
    return `
      <div class="card">
        <h1>Hello${p.email ? `, ${escapeHtml(p.email)}` : ""}</h1>
        <p>Subscription ${sub}</p>
        <p class="muted">Trial ends: ${p.trial_ends_at || "—"} · Access until: ${p.subscription_ends_at || "—"}</p>
        <div class="row" style="margin-top:1rem">
          <button class="btn" type="button" data-go="study">Start studying</button>
          <button class="btn secondary" type="button" data-go="tasks">My tasks</button>
        </div>
      </div>
      <div class="card">
        <h2>How it works</h2>
        <p>1. Pick a Math level (start at Level 1).</p>
        <p>2. Answer questions — accuracy & speed unlock insights.</p>
        <p>3. Complete a level to unlock the next.</p>
      </div>`;
  }

  async function viewStudy() {
    try {
      const subjects = await Api.listSubjects(token());
      const math = (subjects.subjects || []).find((s) => s.subject_id === "math")
        || (subjects.subjects || [])[0];
      if (!math) {
        return `<div class="card"><h1>Study</h1><p class="muted">No subjects yet. Ask an admin to run seed.</p></div>`;
      }
      const levels = await Api.listLevels(token(), math.subject_id);
      const progress = await Api.getProgress(token(), math.subject_id);
      const progMap = Object.fromEntries(
        (progress.progress || []).map((p) => [`${p.subject_id}:${p.level_id}`, p])
      );

      if (state.session) {
        return viewSession();
      }

      const items = (levels.levels || []).map((lv) => {
        const pr = progMap[`${math.subject_id}:${lv.level_id}`];
        const st = pr ? pr.status : "locked?";
        const badge = pr
          ? (pr.status === "completed" ? "ok" : pr.status === "failed" ? "err" : "warn")
          : "";
        return `
          <div class="level-item">
            <div class="grow">
              <div class="title"><strong>${escapeHtml(lv.name)}</strong>
                ${pr ? `<span class="badge ${badge}">${escapeHtml(pr.status)}</span>` : `<span class="badge">new</span>`}
              </div>
              <div class="muted">${lv.question_count || 0} questions · pass ≥ ${Math.round((lv.pass_accuracy || 0.8) * 100)}%</div>
            </div>
            <button class="btn" type="button"
              data-start="${escapeHtml(math.subject_id)}"
              data-level="${escapeHtml(lv.level_id)}">Start</button>
          </div>`;
      }).join("");

      return `
        <div class="card">
          <h1>${escapeHtml(math.name || "Study")}</h1>
          <p class="muted">${escapeHtml(math.description || "")}</p>
          ${items || "<p class='muted'>No levels configured.</p>"}
        </div>`;
    } catch (e) {
      if (e.status === 402) {
        return viewPaywall(e.message);
      }
      return `<div class="card"><h1>Study</h1><p class="muted">${escapeHtml(e.message)}</p></div>`;
    }
  }

  function viewSession() {
    const s = state.session;
    const q = s.questions[state.qIndex];
    if (!q) {
      return `<div class="card"><h1>Done</h1><p>No more questions.</p>
        <button class="btn" type="button" id="end-session">Back to levels</button></div>`;
    }
    const pct = Math.round((state.qIndex / s.questions.length) * 100);
    return `
      <div class="card">
        <div class="row">
          <span class="badge">${state.qIndex + 1} / ${s.questions.length}</span>
          <span class="muted grow" style="text-align:right">${escapeHtml(s.level_id)}</span>
        </div>
        <div class="progress-bar" style="margin:0.75rem 0"><span style="width:${pct}%"></span></div>
        <div class="prompt" id="prompt">${escapeHtml(q.prompt)} = ?</div>
        <form id="answer-form" class="stack">
          <input id="answer" inputmode="decimal" autocomplete="off" placeholder="Your answer" required autofocus />
          <button class="btn block" type="submit">Submit</button>
        </form>
        <div id="feedback" class="muted" style="margin-top:0.75rem;min-height:1.5rem"></div>
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
      const data = await Api.insights(token());
      const prog = (data.progress || []).map((p) =>
        `<li><strong>${escapeHtml(p.level_id)}</strong> — ${escapeHtml(p.status)}
         ${p.best_accuracy != null ? `(${Math.round(p.best_accuracy * 100)}%)` : ""}</li>`
      ).join("");
      return `
        <div class="card">
          <h1>Insights</h1>
          <p>${escapeHtml(data.summary || "")}</p>
          <p class="muted">Completed levels: ${data.levels_completed || 0} ·
            Avg accuracy: ${data.avg_best_accuracy != null ? Math.round(data.avg_best_accuracy * 100) + "%" : "—"}</p>
          <h2>Progress</h2>
          <ul>${prog || "<li class='muted'>No progress yet</li>"}</ul>
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

  async function viewAccount() {
    await refreshProfile();
    const p = state.profile || {};
    let paymentsHtml = "";
    try {
      const pays = await Api.listPayments(token());
      paymentsHtml = (pays.payments || []).map((x) =>
        `<li>${escapeHtml(x.gcash_reference)} — <span class="badge">${escapeHtml(x.status)}</span></li>`
      ).join("") || "<li class='muted'>No payments</li>";
    } catch {
      paymentsHtml = "<li class='muted'>Could not load payments</li>";
    }
    return `
      <div class="card">
        <h1>Account</h1>
        <p>${escapeHtml(p.email || "")}</p>
        <p>Status: <span class="badge">${escapeHtml(p.subscription_status || "")}</span>
          ${p.subscription_active ? '<span class="badge ok">active access</span>' : '<span class="badge warn">inactive</span>'}</p>
        <p class="muted">Ends: ${p.subscription_ends_at || "—"}</p>
        <h2>Payments</h2>
        <ul>${paymentsHtml}</ul>
        ${!p.subscription_active ? `<button class="btn accent" type="button" data-go="pay">Pay with GCash</button>` : ""}
      </div>`;
  }

  /* ---------- Render / bind ---------- */

  async function render() {
    const el = main();
    if (!Auth.isLoggedIn()) {
      setNavVisible(false);
      el.innerHTML = viewAuth();
      bindAuth();
      return;
    }
    setNavVisible(true);
    el.innerHTML = `<div class="card"><p class="muted">Loading…</p></div>`;

    let html;
    switch (state.route) {
      case "study": html = await viewStudy(); break;
      case "tasks": html = await viewTasks(); break;
      case "insights": html = await viewInsights(); break;
      case "account": html = await viewAccount(); break;
      case "pay": html = viewPaywall(); break;
      default: html = viewHome(); break;
    }
    el.innerHTML = html;
    bindView();
  }

  function bindAuth() {
    let mode = "login";
    const tabLogin = document.getElementById("tab-login");
    const tabSignup = document.getElementById("tab-signup");
    const submit = document.getElementById("auth-submit");
    const confirmBtn = document.getElementById("auth-confirm");
    const confirmWrap = document.getElementById("confirm-wrap");

    tabLogin.onclick = () => {
      mode = "login";
      tabLogin.classList.add("active");
      tabSignup.classList.remove("active");
      submit.textContent = "Log in";
      confirmWrap.classList.add("hidden");
      confirmBtn.classList.add("hidden");
    };
    tabSignup.onclick = () => {
      mode = "signup";
      tabSignup.classList.add("active");
      tabLogin.classList.remove("active");
      submit.textContent = "Sign up";
      confirmWrap.classList.remove("hidden");
      confirmBtn.classList.remove("hidden");
    };

    document.getElementById("auth-form").onsubmit = async (e) => {
      e.preventDefault();
      const email = document.getElementById("email").value.trim();
      const password = document.getElementById("password").value;
      try {
        if (mode === "login") {
          await Auth.signIn(email, password);
          await refreshProfile();
          toast("Welcome back!");
          navigate("home");
        } else {
          await Auth.signUp(email, password);
          toast("Check your email for a confirmation code.");
        }
      } catch (err) {
        toast(err.message || String(err), true);
      }
    };

    confirmBtn.onclick = async () => {
      const email = document.getElementById("email").value.trim();
      const code = document.getElementById("code").value.trim();
      try {
        await Auth.confirm(email, code);
        toast("Email confirmed — you can log in.");
        tabLogin.click();
      } catch (err) {
        toast(err.message || String(err), true);
      }
    };
  }

  function bindView() {
    main().querySelectorAll("[data-go]").forEach((b) => {
      b.onclick = () => navigate(b.dataset.go);
    });

    main().querySelectorAll("[data-start]").forEach((b) => {
      b.onclick = async () => {
        try {
          const session = await Api.startSession(token(), {
            subject_id: b.dataset.start,
            level_id: b.dataset.level,
          });
          state.session = session;
          state.qIndex = 0;
          state.answerStartedAt = Date.now();
          render();
        } catch (e) {
          toast(e.message, true);
          if (e.status === 402) navigate("pay");
        }
      };
    });

    const answerForm = document.getElementById("answer-form");
    if (answerForm) {
      state.answerStartedAt = Date.now();
      answerForm.onsubmit = async (e) => {
        e.preventDefault();
        const answer = document.getElementById("answer").value;
        const q = state.session.questions[state.qIndex];
        const elapsed = Math.max(0, Date.now() - state.answerStartedAt);
        try {
          const res = await Api.submitAnswer(token(), state.session.session_id, {
            question_id: q.question_id,
            answer,
            elapsed_ms: elapsed,
          });
          const fb = document.getElementById("feedback");
          fb.textContent = res.correct
            ? "Correct!"
            : `Incorrect. Answer: ${res.expected_answer ?? "—"}`;
          fb.style.color = res.correct ? "var(--ok)" : "var(--danger)";

          if (res.session_complete) {
            const rec = res.recommendation;
            setTimeout(() => {
              state.session = null;
              state.qIndex = 0;
              const actions = (rec && rec.actions) ? rec.actions.join(" ") : "";
              toast(
                res.passed
                  ? `Level passed! ${actions}`
                  : `Not passed (${Math.round((res.accuracy || 0) * 100)}%). ${actions}`
              );
              navigate("insights");
            }, 900);
          } else {
            setTimeout(() => {
              state.qIndex += 1;
              state.answerStartedAt = Date.now();
              render();
            }, 650);
          }
        } catch (err) {
          toast(err.message, true);
        }
      };
    }

    const endBtn = document.getElementById("end-session");
    if (endBtn) {
      endBtn.onclick = () => {
        state.session = null;
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

  function init() {
    nav().querySelectorAll("button[data-route]").forEach((b) => {
      b.onclick = () => navigate(b.dataset.route);
    });
    document.getElementById("btn-logout").onclick = () => {
      Auth.signOut();
      state = { route: "home", profile: null, session: null, qIndex: 0, answerStartedAt: 0 };
      toast("Logged out");
      render();
    };

    if (Auth.isLoggedIn()) {
      refreshProfile().then(() => navigate("home"));
    } else {
      render();
    }
  }

  return { init, navigate };
})();

document.addEventListener("DOMContentLoaded", () => App.init());
