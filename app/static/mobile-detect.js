// Home Assistant's ingress renders this page inside an iframe, and some
// browser/webview combinations don't evaluate CSS @media queries against
// the phone's actual screen width in that context - only against whatever
// width the iframe itself reports, which can differ from the real device
// width. This measures the actual rendered width directly and sets a class
// CSS can key off instead, which is immune to that iframe-viewport quirk.
// Harmless when accessed directly too (docker compose), since the plain
// @media breakpoint already covers that case there.
//
// This lives in its own file rather than an inline <script> block
// specifically because Home Assistant's frontend may apply a
// Content-Security-Policy to ingress iframes that blocks inline scripts -
// an external same-origin file like this is far more likely to be allowed.
(function () {
    function updateMobileClass() {
        var w = document.documentElement.clientWidth || window.innerWidth || 0;
        document.documentElement.classList.toggle('is-mobile', w > 0 && w <= 640);
    }
    updateMobileClass();
    window.addEventListener('resize', updateMobileClass);
    window.addEventListener('orientationchange', updateMobileClass);
})();
