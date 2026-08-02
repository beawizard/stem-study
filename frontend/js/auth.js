/* Cognito auth helpers (amazon-cognito-identity-js via CDN) */
const Auth = (() => {
  const STORAGE_KEY = "stem_session";

  function poolData() {
    const c = window.STEM_CONFIG || {};
    if (!c.userPoolId || !c.userPoolClientId) {
      throw new Error("Cognito not configured. Set userPoolId and userPoolClientId.");
    }
    return {
      UserPoolId: c.userPoolId,
      ClientId: c.userPoolClientId,
    };
  }

  function getUserPool() {
    return new AmazonCognitoIdentity.CognitoUserPool(poolData());
  }

  function saveSession(tokens) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens));
  }

  function clearSession() {
    localStorage.removeItem(STORAGE_KEY);
  }

  function loadSession() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch {
      return null;
    }
  }

  function getIdToken() {
    const s = loadSession();
    return s && s.idToken;
  }

  /**
   * Self-sign-up into the Cognito user pool as an ordinary learner.
   * Does not add the user to the "admin" group (admins are provisioned separately).
   * @param {string} email
   * @param {string} password
   * @param {{ nickname?: string }} [opts]
   */
  function signUp(email, password, opts = {}) {
    return new Promise((resolve, reject) => {
      const attributeList = [
        new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "email", Value: email }),
      ];
      const nick = String(opts.nickname || "").trim();
      if (nick) {
        // Standard Cognito attributes (pool already has nickname + name as optional)
        attributeList.push(
          new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "nickname", Value: nick })
        );
        attributeList.push(
          new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "name", Value: nick })
        );
      }
      getUserPool().signUp(email, password, attributeList, null, (err, result) => {
        if (err) return reject(err);
        // result.user is in the pool only; no group membership is assigned here.
        resolve(result);
      });
    });
  }

  function confirm(email, code) {
    return new Promise((resolve, reject) => {
      const user = new AmazonCognitoIdentity.CognitoUser({
        Username: email,
        Pool: getUserPool(),
      });
      user.confirmRegistration(code, true, (err, result) => {
        if (err) return reject(err);
        resolve(result);
      });
    });
  }

  function signIn(email, password) {
    return new Promise((resolve, reject) => {
      const user = new AmazonCognitoIdentity.CognitoUser({
        Username: email,
        Pool: getUserPool(),
      });
      const authDetails = new AmazonCognitoIdentity.AuthenticationDetails({
        Username: email,
        Password: password,
      });
      user.authenticateUser(authDetails, {
        onSuccess(session) {
          const tokens = {
            idToken: session.getIdToken().getJwtToken(),
            accessToken: session.getAccessToken().getJwtToken(),
            refreshToken: session.getRefreshToken().getToken(),
            email,
          };
          saveSession(tokens);
          resolve(tokens);
        },
        onFailure: reject,
      });
    });
  }

  /** Step 1: email a password-reset code (Cognito ForgotPassword). */
  function forgotPassword(email) {
    return new Promise((resolve, reject) => {
      const user = new AmazonCognitoIdentity.CognitoUser({
        Username: String(email || "").trim().toLowerCase(),
        Pool: getUserPool(),
      });
      user.forgotPassword({
        onSuccess(data) {
          resolve(data || { delivery: "code_sent" });
        },
        onFailure: reject,
        // Library calls this instead of onSuccess when defined; still means code was sent.
        inputVerificationCode(data) {
          resolve(data || { delivery: "code_sent" });
        },
      });
    });
  }

  /** Step 2: confirm code + new password. */
  function confirmForgotPassword(email, code, newPassword) {
    return new Promise((resolve, reject) => {
      const user = new AmazonCognitoIdentity.CognitoUser({
        Username: String(email || "").trim().toLowerCase(),
        Pool: getUserPool(),
      });
      user.confirmPassword(String(code || "").trim(), newPassword, {
        onSuccess: resolve,
        onFailure: reject,
      });
    });
  }

  function signOut() {
    clearSession();
    try {
      const pool = getUserPool();
      const user = pool.getCurrentUser();
      if (user) user.signOut();
    } catch (_) { /* config missing offline */ }
  }

  function isLoggedIn() {
    return Boolean(getIdToken());
  }

  function decodeJwtPayload(jwt) {
    if (!jwt || typeof jwt !== "string") return null;
    try {
      const part = jwt.split(".")[1];
      if (!part) return null;
      const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
      const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
      return JSON.parse(atob(padded));
    } catch {
      return null;
    }
  }

  /** Cognito groups from the ID token (e.g. ["admin"]). */
  function getGroups() {
    const claims = decodeJwtPayload(getIdToken());
    if (!claims) return [];
    const g = claims["cognito:groups"] || claims.groups || [];
    if (Array.isArray(g)) return g.map(String);
    if (typeof g === "string") {
      return g
        .replace(/[\[\]"]/g, "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return [];
  }

  function isAdmin() {
    const groups = getGroups();
    return groups.includes("admin") || groups.includes("admins");
  }

  /** Nickname / name from ID token claims (if Cognito included them). */
  function getNicknameFromToken() {
    const claims = decodeJwtPayload(getIdToken());
    if (!claims) return "";
    const nick = claims.nickname || claims.name || claims.preferred_username || "";
    return String(nick).trim();
  }

  return {
    signUp,
    confirm,
    signIn,
    forgotPassword,
    confirmForgotPassword,
    signOut,
    getIdToken,
    isLoggedIn,
    loadSession,
    getGroups,
    isAdmin,
    getNicknameFromToken,
  };
})();
