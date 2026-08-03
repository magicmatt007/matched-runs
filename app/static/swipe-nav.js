// Swipe left/right triggers whatever this page's own Previous/Next links
// already point to - same navigation, just also reachable by touch
// gesture, not a separate mechanism. Harmless no-op on any page without
// data-swipe-nav links at all.
//
// Deliberately ignores swipes that start on the map (Leaflet's own
// touch-drag panning) or on an element specifically marked
// .touch-interactive (the elevation/pace/heart-rate charts on the
// activity detail page already use touch-drag themselves, to show a
// value as a finger drags across them) - both would otherwise fight with
// page-level swipe navigation for the same gesture. This is deliberately
// NOT a blanket "any svg" exclusion - other charts (e.g. the training
// log's own distance chart) are also drawn as SVG but have no touch
// interaction of their own, so excluding every SVG wholesale would
// silently block swipe navigation somewhere it has no real conflict.
(function () {
    var nextLink = document.querySelector('a[data-swipe-nav="next"]');
    var prevLink = document.querySelector('a[data-swipe-nav="prev"]');
    if (!nextLink && !prevLink) return;

    var MIN_DISTANCE = 60; // px - avoid triggering on small/accidental movements
    var MAX_VERTICAL_RATIO = 0.5; // vertical movement must stay well below horizontal to count as a swipe, not a scroll

    var startX = null;
    var startY = null;

    function isExcludedTarget(el) {
        return !!(el && el.closest && (el.closest('#map') || el.closest('.touch-interactive')));
    }

    // Passive throughout - this only ever reacts after the fact to where a
    // touch started and ended, never calls preventDefault, so normal
    // scrolling is completely unaffected either way.
    document.addEventListener('touchstart', function (evt) {
        if (!evt.touches || evt.touches.length !== 1 || isExcludedTarget(evt.target)) {
            startX = null;
            return;
        }
        startX = evt.touches[0].clientX;
        startY = evt.touches[0].clientY;
    }, { passive: true });

    document.addEventListener('touchend', function (evt) {
        if (startX === null) return;
        var endTouch = evt.changedTouches && evt.changedTouches[0];
        if (!endTouch) {
            startX = null;
            return;
        }

        var dx = endTouch.clientX - startX;
        var dy = endTouch.clientY - startY;
        startX = null;

        if (Math.abs(dx) < MIN_DISTANCE) return;
        if (Math.abs(dy) > Math.abs(dx) * MAX_VERTICAL_RATIO) return;

        if (dx < 0 && nextLink) {
            window.location.href = nextLink.href;
        } else if (dx > 0 && prevLink) {
            window.location.href = prevLink.href;
        }
    }, { passive: true });
})();
