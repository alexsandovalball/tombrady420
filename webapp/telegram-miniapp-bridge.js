(function () {
    'use strict';

    var path = window.location.pathname || '/';
    if (/^\/webapp(?:\/|$)/i.test(path)) return;

    var search = window.location.search || '';
    var hash = window.location.hash || '';
    var markers = search + hash;
    var hasTelegramMarker = /tgWebApp|tg_web_app|tgWebAppData|tgWebAppStartParam|start_param|initData/i.test(markers);
    var hasTelegramObject = false;

    try {
        hasTelegramObject = !!(
            window.Telegram &&
            window.Telegram.WebApp &&
            (window.Telegram.WebApp.initData || window.Telegram.WebApp.initDataUnsafe)
        );
    } catch (_err) {
        hasTelegramObject = false;
    }

    if (!hasTelegramMarker && !hasTelegramObject) return;

    var target = new URL('/webapp/index.html', window.location.origin);
    var current = new URL(window.location.href);
    current.searchParams.forEach(function (value, key) {
        target.searchParams.set(key, value);
    });

    var productMatch = path.match(/(?:^|\/)product\/(\d+)(?:\/|$)/i);
    if (productMatch && !target.searchParams.has('product_id')) {
        target.searchParams.set('product_id', productMatch[1]);
    }

    var segments = path.split('/').filter(Boolean);
    var language = (segments[0] || '').toLowerCase();
    if (/^(en|de|nl|it|es|fr)$/.test(language) && !target.searchParams.has('lang')) {
        target.searchParams.set('lang', language);
    }

    window.location.replace(target.pathname + target.search + hash);
})();
