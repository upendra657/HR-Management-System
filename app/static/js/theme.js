/* Light/dark theme.
 *
 * The initial value is set by an inline script in <head> rather than here,
 * because a deferred script runs after first paint and you get a white flash
 * before dark mode applies. This file only handles the toggle afterwards.
 */
(function () {
  "use strict";

  const KEY = "hrms-theme";
  const root = document.documentElement;

  function stored() {
    try {
      return localStorage.getItem(KEY);
    } catch (e) {
      return null; // Private browsing can throw on access.
    }
  }

  function save(value) {
    try {
      localStorage.setItem(KEY, value);
    } catch (e) {
      /* not fatal - the theme just will not persist */
    }
  }

  function apply(theme) {
    root.setAttribute("data-bs-theme", theme);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String(theme === "dark"));
      btn.setAttribute(
        "title",
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
      );
    });
    // Charts and anything else that paints its own colours needs to know.
    window.dispatchEvent(new CustomEvent("themechange", { detail: { theme: theme } }));
  }

  document.addEventListener("DOMContentLoaded", function () {
    apply(root.getAttribute("data-bs-theme") || "light");

    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const next =
          root.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
        apply(next);
        save(next);
      });
    });
  });

  // Follow the OS only while the visitor has not chosen for themselves.
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  if (media.addEventListener) {
    media.addEventListener("change", function (e) {
      if (!stored()) apply(e.matches ? "dark" : "light");
    });
  }
})();
