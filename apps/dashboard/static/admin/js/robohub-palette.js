// Palette picker: remembers the choice in this browser (localStorage) and sets <html data-palette>.
// The attribute is also set by a tiny inline script in <head> so the page never flashes the default colours.
(function () {
  var KEY = "robohub-admin-palette";
  var select = document.getElementById("rh-palette");
  if (!select) return;
  var saved;
  try { saved = localStorage.getItem(KEY); } catch (e) { saved = null; }
  var current = saved || document.documentElement.getAttribute("data-palette");
  if (current && select.querySelector('option[value="' + current + '"]')) select.value = current;
  select.addEventListener("change", function () {
    document.documentElement.setAttribute("data-palette", select.value);
    try { localStorage.setItem(KEY, select.value); } catch (e) { /* private mode: still applies for this page */ }
  });
})();
