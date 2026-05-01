
// Dark/light toggle
(function () {
  const toggle = document.querySelector("[data-theme-toggle]");
  const root = document.documentElement;
  let theme = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  root.setAttribute("data-theme", theme);

  function setIcon(t) {
    toggle.innerHTML = t === "dark"
      ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`
      : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
    toggle.setAttribute("aria-label", "Switch to " + (t === "dark" ? "light" : "dark") + " mode");
  }

  setIcon(theme);
  if (toggle) {
    toggle.addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", theme);
      setIcon(theme);
    });
  }
})();

// File drop UI
const fileDrop = document.getElementById("fileDrop");
const fileInput = document.getElementById("config_file");
const fileLabel = document.getElementById("fileLabel");

if (fileInput) {
  fileInput.addEventListener("change", () => {
    const name = fileInput.files[0] ? fileInput.files[0].name : "Drag & drop or <u>browse</u>";
    fileLabel.innerHTML = fileInput.files[0] ? "📄 " + name : name;
  });
}

if (fileDrop) {
  fileDrop.addEventListener("dragover", (e) => { e.preventDefault(); fileDrop.classList.add("active"); });
  fileDrop.addEventListener("dragleave", () => fileDrop.classList.remove("active"));
  fileDrop.addEventListener("drop", (e) => {
    e.preventDefault();
    fileDrop.classList.remove("active");
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      fileLabel.innerHTML = "📄 " + e.dataTransfer.files[0].name;
    }
  });
}

// Copy to clipboard
function copyConfig() {
  const pre = document.getElementById("outputConfig");
  if (!pre) return;
  navigator.clipboard.writeText(pre.innerText).then(() => {
    const btn = document.querySelector("[onclick=\'copyConfig()\']");
    if (btn) {
      const original = btn.innerHTML;
      btn.innerHTML = "✓ Copied!";
      setTimeout(() => { btn.innerHTML = original; }, 2000);
    }
  });
}

// Scroll to output after form submit
window.addEventListener("DOMContentLoaded", () => {
  const output = document.querySelector(".output-section");
  if (output) output.scrollIntoView({ behavior: "smooth", block: "start" });
});
