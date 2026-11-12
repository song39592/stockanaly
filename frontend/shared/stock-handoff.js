/* =====================================================================
 *  个股详情移交（网页 → 原生启动器）
 *
 *  背景：个股详情页（前复权日 K + 入池轨迹 + 消息面时间轴）已迁进**原生启动器**
 *  的「个股分析」标签页（launcher/StockPage.cs），网页版 stock-analysis.html
 *  随之删除（见 docs/changelog.md 3ed9e6f）。
 *
 *  网页与原生窗口是两个进程，**没有命令通道**，网页无法直接调起原生页。
 *  早先网页里留着 window.open('stock-analysis.html?code=…')，那是一个**死链**：
 *  点了只开一个空白页（404），用户既看不到内容也得不到提示 —— 最难查的一类坏。
 *
 *  这里的做法：把代码复制到剪贴板，并明确给出下一步
 *  （切到启动器窗口 → 个股分析 → 顶栏搜索框粘贴代码）。
 *  复制 + 提示的逻辑放这里，两个页面共用一份，不要各写一遍。
 * ===================================================================== */
(function () {
  var HINT = '个股详情已迁入启动器「个股分析」标签页';

  /** 复制到剪贴板：优先用 Clipboard API，退回 execCommand（file:// 下可能不可用）。 */
  function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text).then(function () { return true; },
          function () { return legacyCopy(text); });
      }
    } catch (e) { /* 落到下面的兜底 */ }
    return Promise.resolve(legacyCopy(text));
  }

  function legacyCopy(text) {
    try {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) { return false; }
  }

  function notify(msg, ok) {
    if (typeof window.toast === 'function') { window.toast(msg, ok ? 'ok' : 'err'); return; }
    if (typeof window.alert === 'function') { window.alert(msg); }
  }

  /**
   * 移交到原生启动器的「个股分析」页。
   * @param {string} code 股票代码
   * @returns {Promise<boolean>} 是否已复制到剪贴板
   */
  window.stockHandoff = function (code) {
    if (!code) return Promise.resolve(false);
    code = String(code).trim();
    return copyText(code).then(function (ok) {
      notify(ok
        ? HINT + '：代码 ' + code + ' 已复制，切到启动器窗口「个股分析」页搜索即可打开。'
        : HINT + '：请在启动器窗口「个股分析」页的搜索框输入 ' + code + '。', ok);
      return ok;
    });
  };
})();
