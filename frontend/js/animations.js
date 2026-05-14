/* animations.js — micro-interaction layer.
 *
 * Handles:
 *   • Scroll-reveal (`.reveal` → `.reveal--in`) via IntersectionObserver
 *   • Cursor-follow spotlight on `.spotlight` cards
 *   • Material-style ripple on `.btn-view` and `.btn-ask-ai`
 *   • Animated number count-up on the KPI / section-total values
 *   • A tiny `Anim` namespace exposed on window so render.js can reset
 *     reveals and re-trigger count-ups when the dashboard re-renders.
 *
 * Pure JS, no deps, respects `prefers-reduced-motion`.
 */
(function () {
  "use strict";

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── Scroll-reveal ────────────────────────────────────────────────────────
  // We set up one IO at boot; render.js re-registers its KPIs/sections by
  // calling Anim.observeReveals() after re-rendering.
  let io = null;
  function ensureObserver() {
    if (io || reduced) return io;
    io = new IntersectionObserver((entries) => {
      for (const ent of entries) {
        if (ent.isIntersecting) {
          ent.target.classList.add("reveal--in");
          io.unobserve(ent.target);
        }
      }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.05 });
    return io;
  }

  function observeReveals(root = document) {
    const obs = ensureObserver();
    if (!obs) {
      // Reduced motion → just mark them all as in.
      root.querySelectorAll(".reveal").forEach(el => el.classList.add("reveal--in"));
      return;
    }
    let i = 0;
    root.querySelectorAll(".reveal:not(.reveal--in)").forEach(el => {
      // If a stagger index is requested via `data-stagger`, assign one based
      // on document order so each batch animates in a satisfying wave.
      if (el.dataset.stagger != null && !el.style.getPropertyValue("--i")) {
        el.style.setProperty("--i", i++);
      }
      obs.observe(el);
    });
  }

  // ── Cursor-follow spotlight ─────────────────────────────────────────────
  function bindSpotlight(root = document) {
    if (reduced) return;
    root.querySelectorAll(".spotlight").forEach(el => {
      if (el._spotBound) return;
      el._spotBound = true;
      el.addEventListener("pointermove", (e) => {
        const r = el.getBoundingClientRect();
        const mx = ((e.clientX - r.left) / r.width)  * 100;
        const my = ((e.clientY - r.top)  / r.height) * 100;
        el.style.setProperty("--mx", mx + "%");
        el.style.setProperty("--my", my + "%");
      }, { passive: true });
      el.addEventListener("pointerleave", () => {
        el.style.setProperty("--mx", "50%");
        el.style.setProperty("--my", "50%");
      });
    });
  }

  // ── Ripple ──────────────────────────────────────────────────────────────
  function bindRipples() {
    document.addEventListener("pointerdown", (e) => {
      const target = e.target.closest(".btn-view, .btn-ask-ai, .chat-chip, .chat-send");
      if (!target || target.disabled) return;
      const r = target.getBoundingClientRect();
      const dot = document.createElement("span");
      dot.className = "ripple";
      dot.style.left = (e.clientX - r.left) + "px";
      dot.style.top  = (e.clientY - r.top)  + "px";
      target.appendChild(dot);
      setTimeout(() => dot.remove(), 700);
    }, { passive: true });
  }

  // ── Number count-up ─────────────────────────────────────────────────────
  // animateCount(el, fromText, toText): if both ends parse to numbers, tween
  // them; otherwise just paint the final text. Keeps the formatted suffix
  // (e.g. "Cr", "L", "₹") intact at display time.
  function _parseNumeric(s) {
    if (s == null) return { value: NaN, prefix: "", suffix: "" };
    const txt = String(s);
    const m = txt.match(/^([^\d-]*)(-?[\d,]*\.?\d+)\s*([^\d]*)$/);
    if (!m) return { value: NaN, prefix: "", suffix: "" };
    const value = parseFloat(m[2].replace(/,/g, ""));
    return { value, prefix: m[1] || "", suffix: m[3] || "" };
  }

  function _formatNumber(value, suffix) {
    // Keep the same ordinal granularity as the final text — e.g. for
    // "₹12.34 Cr" we tween 0..12.34 and re-append " Cr". For plain integers
    // we use locale grouping.
    if (suffix && /Cr|L/.test(suffix)) {
      return value.toFixed(2);
    }
    if (Number.isInteger(value)) {
      return Math.round(value).toLocaleString("en-IN");
    }
    return value.toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
  }

  function animateCount(el, finalText, opts = {}) {
    if (!el) return;
    const dur = opts.duration ?? 900;
    const target = _parseNumeric(finalText);
    if (reduced || !Number.isFinite(target.value)) {
      el.textContent = finalText;
      return;
    }
    el.classList.remove("counting");
    void el.offsetWidth;          // restart animation
    el.classList.add("counting");

    const start = performance.now();
    const startVal = 0;
    const endVal = target.value;
    function step(now) {
      const t = Math.min(1, (now - start) / dur);
      // ease-out-expo
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      const v = startVal + (endVal - startVal) * eased;
      el.textContent = target.prefix + _formatNumber(v, target.suffix) + (target.suffix ? " " + target.suffix.trim() : "");
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = finalText;     // snap to the canonical formatting
    }
    requestAnimationFrame(step);
  }

  // Convenience: set + animate.
  function setCount(id, finalText, opts) {
    const el = document.getElementById(id);
    animateCount(el, finalText, opts);
  }

  // ── Page-load stagger — auto-assign --i indices to .stage-item children
  // so we don't have to hand-write CSS variables for every element.
  function tagStageItems() {
    document.querySelectorAll(".stage").forEach(stage => {
      let i = 0;
      stage.querySelectorAll(":scope > .stage-item").forEach(child => {
        if (!child.style.getPropertyValue("--i")) {
          child.style.setProperty("--i", i++);
        }
      });
    });
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  function init() {
    tagStageItems();
    bindRipples();
    bindSpotlight();
    observeReveals();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }

  window.Anim = {
    observeReveals,
    bindSpotlight,
    animateCount,
    setCount,
    reduced,
  };
})();
