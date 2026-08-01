// Activity times are stored and sent from the server as UTC (GPS devices
// and Strava's API both report timestamps in UTC) - this converts them to
// whatever timezone the viewer's own browser is actually in, rather than
// showing a fixed server-side assumption that may not match the viewer at
// all (a self-hosted app can easily be viewed from a different timezone
// than the server itself is in, or than any one fixed zone the server
// might have guessed).
//
// Elements carry the raw UTC time in a data attribute and initially show
// a UTC-formatted fallback (in case JS is unavailable) - this upgrades
// them to the browser's local time on load.
(function () {
    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function formatLocalDate(date) {
        return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
    }

    function formatLocalDateTime(date) {
        return formatLocalDate(date) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
    }

    function applyLocalTimes() {
        document.querySelectorAll('[data-utc-date]').forEach(function (el) {
            var iso = el.getAttribute('data-utc-date');
            if (!iso) return;
            var date = new Date(iso);
            if (isNaN(date.getTime())) return;
            el.textContent = formatLocalDate(date);
        });

        document.querySelectorAll('[data-utc-datetime]').forEach(function (el) {
            var iso = el.getAttribute('data-utc-datetime');
            if (!iso) return;
            var date = new Date(iso);
            if (isNaN(date.getTime())) return;
            el.textContent = formatLocalDateTime(date);
        });
    }

    // Exposed for scripts that build their own dynamic content referencing
    // a date (e.g. a chart tooltip generated in JS rather than a static
    // DOM element data-utc-* alone can upgrade) - same conversion, just
    // callable directly instead of only running against the DOM.
    window.matchedRunsFormatLocalDate = function (iso) {
        var date = new Date(iso);
        return isNaN(date.getTime()) ? iso : formatLocalDate(date);
    };
    window.matchedRunsFormatLocalDateTime = function (iso) {
        var date = new Date(iso);
        return isNaN(date.getTime()) ? iso : formatLocalDateTime(date);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyLocalTimes);
    } else {
        applyLocalTimes();
    }
})();
