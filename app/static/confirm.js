// Attaches a confirm() dialog to any form with a data-confirm attribute,
// via addEventListener rather than an inline onclick/onsubmit handler.
// Inline event handlers can be blocked by a Content-Security-Policy the
// same way inline <script> blocks can (relevant under Home Assistant's
// ingress iframe) - if that silently failed here, a destructive action
// like replacing the whole database would submit with NO confirmation at
// all, which is worse than not having a confirmation dialog in the first
// place. This external file is far more likely to actually run.
(function () {
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (form && form.hasAttribute && form.hasAttribute('data-confirm')) {
            if (!window.confirm(form.getAttribute('data-confirm'))) {
                e.preventDefault();
            }
        }
    }, true);
})();
