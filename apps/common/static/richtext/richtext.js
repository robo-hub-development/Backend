// Turns every <textarea data-richtext> into a Quill editor and keeps the textarea (the real form field) in sync.
(function () {
  "use strict";

  var TOOLBAR = [
    [{ header: [2, 3, false] }],
    ["bold", "italic", "underline", "strike"],
    [{ list: "ordered" }, { list: "bullet" }],
    ["blockquote", "link"],
    ["clean"],
  ];

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Descriptions written before the editor existed are plain text with blank lines between paragraphs.
  function toHtml(value) {
    if (/<\/?[a-z][\s\S]*>/i.test(value)) return value;
    return value
      .split(/\n{2,}/)
      .filter(function (part) { return part.trim() !== ""; })
      .map(function (part) { return "<p>" + escapeHtml(part).replace(/\n/g, "<br>") + "</p>"; })
      .join("");
  }

  function init(textarea) {
    if (textarea.dataset.richtextReady || textarea.name.indexOf("__prefix__") !== -1) return;
    textarea.dataset.richtextReady = "1";

    var holder = document.createElement("div");
    holder.className = "rh-richtext";
    var editor = document.createElement("div");
    holder.appendChild(editor);
    textarea.insertAdjacentElement("afterend", holder);
    textarea.style.display = "none";

    var quill = new Quill(editor, { theme: "snow", modules: { toolbar: TOOLBAR }, placeholder: "Write here…" });
    quill.clipboard.dangerouslyPasteHTML(toHtml(textarea.value));

    function sync() {
      textarea.value = quill.getText().trim() === "" ? "" : quill.getSemanticHTML();
    }
    quill.on("text-change", sync);
    textarea.form && textarea.form.addEventListener("submit", sync);
  }

  function initAll(root) {
    (root || document).querySelectorAll("textarea[data-richtext]").forEach(init);
  }

  document.addEventListener("DOMContentLoaded", function () { initAll(); });
  // Inline rows added with "Add another" get their editor too.
  document.addEventListener("formset:added", function (event) { initAll(event.target); });
})();
