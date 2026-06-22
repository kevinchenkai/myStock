"use strict";

// 主题切换（跟随系统 / 浅色 / 深色 三态）。多个页面共用。
// 需要页面含 #theme-toggle / #tt-icon / #tt-label；配合 <head> 内防闪烁脚本。
(function () {
  const KEY = "mystock-theme";
  const ORDER = ["system", "light", "dark"];
  const META = {
    system: { icon: "🖥️", label: "跟随系统" },
    light: { icon: "☀️", label: "浅色" },
    dark: { icon: "🌙", label: "深色" },
  };
  const btn = document.getElementById("theme-toggle");
  const iconEl = document.getElementById("tt-icon");
  const labelEl = document.getElementById("tt-label");
  if (!btn) return;

  function read() {
    try {
      const v = localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : "system";
    } catch (e) {
      return "system";
    }
  }

  function apply(mode) {
    const root = document.documentElement;
    if (mode === "system") {
      root.removeAttribute("data-theme");       // 交回 @media 跟随系统
      try { localStorage.removeItem(KEY); } catch (e) {}
    } else {
      root.setAttribute("data-theme", mode);
      try { localStorage.setItem(KEY, mode); } catch (e) {}
    }
    iconEl.textContent = META[mode].icon;
    labelEl.textContent = META[mode].label;
  }

  apply(read());

  btn.addEventListener("click", () => {
    const cur = read();
    const next = ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length];
    apply(next);
  });
})();
