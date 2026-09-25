(function () {
    'use strict';

    var observer = null;

    function activate(video) {
        if (!video || video.dataset.previewActivated === '1') return;
        var source = video.dataset.src || video.dataset.mediaSrc || '';
        if (!source) return;
        video.dataset.previewActivated = '1';
        video.src = source;
        try {
            video.load();
        } catch (_err) {
            // Setting src is enough on browsers that reject an explicit load().
        }
    }

    function prepare(video) {
        if (!video || video.dataset.previewReady === '1') return;
        video.dataset.previewReady = '1';
        video.preload = 'metadata';
        video.addEventListener('loadedmetadata', function () {
            try {
                if (Number.isFinite(video.duration) && video.duration > 0.4) {
                    video.currentTime = Math.min(0.4, video.duration / 3);
                }
            } catch (_err) {}
        }, { once: true });
        video.addEventListener('loadeddata', function () {
            video.classList.add('loaded');
        }, { once: true });

        if (video.src && !video.dataset.src && !video.dataset.mediaSrc) return;
        if (!('IntersectionObserver' in window)) {
            activate(video);
            return;
        }
        if (!observer) {
            observer = new IntersectionObserver(function (entries, currentObserver) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) return;
                    currentObserver.unobserve(entry.target);
                    activate(entry.target);
                });
            }, { rootMargin: '320px 0px', threshold: 0.01 });
        }
        observer.observe(video);
    }

    window.prepareSeoCardVideo = prepare;

    function prepareExisting() {
        document.querySelectorAll('video.seo-card-video').forEach(prepare);
        document.querySelectorAll('img[loading="lazy"]').forEach(function (image) {
            var show = function () { image.classList.add('loaded'); };
            image.addEventListener('load', show, { once: true });
            image.addEventListener('error', show, { once: true });
            if (image.complete) show();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', prepareExisting, { once: true });
    } else {
        prepareExisting();
    }
})();
