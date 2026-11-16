// Small formatting helpers shared by the UI.
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

function formatDate(timestamp) {
  return timestamp ? new Date(timestamp).toISOString().slice(0, 10) : "";
}

function pluralize(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

if (typeof module !== "undefined") {
  module.exports = { escapeHtml, formatDate, pluralize };
}
