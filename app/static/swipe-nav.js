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
    function init() {
        var nextLink = document.querySelector('a[data-swipe-nav="next"]');
        var prevLink = document.querySelector('a[data-swipe-nav="prev"]');
        if (!nextLink && !prevLink) return;

        var MIN_DISTANCE = 60; // px - avoid triggering on small/accidental movements
        var MAX_VERTICAL_RATIO = 0.5; // vertical movement must stay well below horizontal to count as a swipe, not a scroll

        var startX = null;
        var startY = null;

        function isExcludedTarget(el) {
            if (!el) return false;
            if (el.closest && (el.closest('#map') || el.closest('.touch-interactive'))) return true;

            // Any ancestor that's actually horizontally scrollable right
            // now (its content is genuinely wider than it is) is assumed
            // to have its own horizontal touch behavior that would
            // conflict with swipe navigation - e.g. the responsive table
            // wrapper on narrow screens, where scrolling the table
            // sideways was triggering page navigation at the same time.
            // General rather than a hardcoded list of specific classes,
            // so a future horizontally-scrollable element doesn't need
            // its own separate fix the same way this one did.
            var node = el;
            while (node && node !== document.body && node.nodeType === 1) {
                if (typeof node.scrollWidth === 'number' && node.scrollWidth > node.clientWidth + 1) {
                    return true;
                }
                node = node.parentElement;
            }
            return false;
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
    }

    // This script is loaded from <head>, which runs before the page's
    // actual content (including the very links this looks for) exists in
    // the DOM at all - querying for them immediately, the way this
    // originally did, always found nothing and silently did nothing on
    // every single page load, regardless of which page or whether the
    // links were really there further down. Same fix as local-time.js
    // already uses for the same reason.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
