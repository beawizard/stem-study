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

  function signUp(email, password) {
    return new Promise((resolve, reject) => {
      const attributeList = [
        new AmazonCognitoIdentity.CognitoUserAttribute({ Name: "email", Value: email }),
      ];
      getUserPool().signUp(email, password, attributeList, null, (err, result) => {
        if (err) return reject(err);
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

  return { signUp, confirm, signIn, signOut, getIdToken, isLoggedIn, loadSession };
})();
