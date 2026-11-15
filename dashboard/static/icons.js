// Thin-line icons shared by the dashboard and the Controls page (24x24, drawn
// here, no icon library). innerHTML is only ever used for these fixed shapes.
"use strict";

window.FaradayIcons = (function () {
  const ICONS = {
    lock: '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    unlock: '<rect x="5" y="11" width="14" height="9" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 7.6-1.7"/>',
    door: '<path d="M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21"/><path d="M4 21h16"/><path d="M14.5 12.5v.01"/>',
    motion: '<path d="M8.5 8.5a5 5 0 0 0 0 7"/><path d="M5.6 5.6a9 9 0 0 0 0 12.8"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.4 5.6a9 9 0 0 1 0 12.8"/><circle cx="12" cy="12" r="1.3"/>',
    bulb: '<path d="M9.5 18h5"/><path d="M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.2h5c0-.8.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
    camera: '<rect x="3" y="7" width="18" height="13" rx="3"/><path d="M8.5 7l1.4-2.5h4.2L15.5 7"/><circle cx="12" cy="13.5" r="3.4"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>',
    speaker: '<path d="M4 9.5v5h3.5L12 18V6L7.5 9.5H4z"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6"/><path d="M18 6.5a8 8 0 0 1 0 11"/>',
    shield: '<path d="M12 3l7 3v5.2c0 4.6-3 8.2-7 9.8-4-1.6-7-5.2-7-9.8V6l7-3z"/>',
    alert: '<path d="M12 4l8.5 15h-17L12 4z"/><path d="M12 10v4"/><path d="M12 16.8v.01"/>',
    message: '<path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4.5 3.5V6.5a1 1 0 0 1 1-1z"/>',
    rule: '<path d="M5 7h9"/><path d="M5 12h14"/><path d="M5 17h6"/><circle cx="17.5" cy="7" r="1.5"/><circle cx="14.5" cy="17" r="1.5"/>',
    activity: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
    more: '<circle cx="6" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="18" cy="12" r="1.2"/>',
    close: '<path d="M7 7l10 10"/><path d="M17 7L7 17"/>',
    sliders: '<path d="M4 7h10"/><path d="M18 7h2"/><circle cx="16" cy="7" r="2"/><path d="M4 17h4"/><path d="M12 17h8"/><circle cx="10" cy="17" r="2"/>',
    back: '<path d="M14.5 5.5L8 12l6.5 6.5"/>',
    aperture: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3.2"/>',
    ear: '<path d="M7 9a5 5 0 0 1 10 0c0 3-3 4-3 7a2.5 2.5 0 0 1-5 0"/><path d="M10 9.5a2 2 0 0 1 4 0"/>',
  };

  function setIcon(el, name) {
    el.classList.add("ico");
    el.dataset.icon = name;
    el.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  }

  function icon(name) {
    const span = document.createElement("span");
    setIcon(span, name);
    return span;
  }

  return { ICONS, setIcon, icon };
})();
