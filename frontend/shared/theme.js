/* =====================================================================
 *  主题（浅色 / 深色）——所有页面共用
 *
 *  由启动器窗口顶部的「主题：浅色 / 深色」按钮统一控制：
 *    1) 打开页面时：地址里带 theme= 参数，页面一打开就是正确的颜色；
 *    2) 已经开着的页面：定时读取启动器写的 theme-state.js，一两秒内跟着变。
 *
 *  直接用浏览器打开页面（不经过启动器）时，跟随 theme-state.js，
 *  没有就退回上次的 localStorage 选择，默认深色。
 * ===================================================================== */
(function () {
  var KEY = 'stockPool.theme';

  function pick() {
    var m = location.search.match(/[?&]theme=(light|dark)/);
    if (m) return m[1];
    if (window.__LAUNCHER_THEME__ === 'light' || window.__LAUNCHER_THEME__ === 'dark') {
      return window.__LAUNCHER_THEME__;
    }
    try {
      var s = localStorage.getItem(KEY);
      if (s === 'light' || s === 'dark') return s;
    } catch (e) {}
    return 'dark';
  }

  var cur = null;

  /** 应用主题：改 html[data-theme]，记住选择，并通知页面（图表等可重绘）。 */
  window.__applyTheme = function (t) {
    if (t !== 'light' && t !== 'dark') return;
    if (t === cur) return;
    cur = t;
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem(KEY, t); } catch (e) {}
    try { window.dispatchEvent(new CustomEvent('themechange', { detail: t })); } catch (e) {}
  };

  // 脚本在 head 里同步执行：body 渲染前就设好主题，不会先闪一下深色
  window.__applyTheme(pick());

  // 跟着启动器走：定时加载启动器写的 theme-state.js（文件不存在时静默跳过）
  setInterval(function () {
    var s = document.createElement('script');
    s.src = 'theme-state.js?t=' + Date.now();
    s.onload = s.onerror = function () {
      window.__applyTheme(window.__LAUNCHER_THEME__);
      if (s.parentNode) s.parentNode.removeChild(s);
    };
    document.head.appendChild(s);
  }, 1500);
})();
