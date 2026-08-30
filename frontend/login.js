"use strict";

const form = document.getElementById("login-form");
const btn = document.getElementById("login-btn");
const errorBox = document.getElementById("login-error");
const passcode = document.getElementById("passcode");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.add("hidden");
  btn.disabled = true;
  btn.textContent = "Signing in…";

  try {
    const fd = new FormData();
    fd.append("passcode", passcode.value);
    const res = await fetch("/api/login", { method: "POST", body: fd });
    if (res.ok) {
      window.location.href = "/";
      return;
    }
    let detail = "Sign-in failed. Please try again.";
    try {
      const data = await res.json();
      if (data && typeof data.detail === "string") detail = data.detail;
    } catch {
      /* ignore */
    }
    errorBox.textContent = detail;
    errorBox.classList.remove("hidden");
    passcode.value = "";
    passcode.focus();
  } catch {
    errorBox.textContent = "Network error. Please try again.";
    errorBox.classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "Sign in";
  }
});
