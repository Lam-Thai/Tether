/**
 * Scroll-motion primitives for the marketing page (DESIGN.md, Resolved decision 16).
 *
 * The pure functions turn scroll geometry into progress and steps, so components never do
 * that math inline. `driveWhileVisible` is the one place that listens to scroll: it runs a
 * frame callback on requestAnimationFrame only while its element is on screen and the page
 * is scrolling or still settling, and never touches React state per frame.
 */

const clamp01 = (n: number) => (n < 0 ? 0 : n > 1 ? 1 : n);

/**
 * Progress (0..1) through a pinned section: 0 when its sticky stage first pins at
 * `stickyTop`, 1 when the stage unpins. Sections with no pinned travel stay at 0.
 */
export function pinProgress({
  top,
  height,
  stageHeight,
  stickyTop,
}: {
  top: number;
  height: number;
  stageHeight: number;
  stickyTop: number;
}): number {
  const travel = height - stageHeight;
  if (travel <= 0) return 0;
  return clamp01((stickyTop - top) / travel);
}

/** Which of `count` equal steps a progress value falls in. */
export function stepFor(progress: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(count - 1, Math.floor(clamp01(progress) * count));
}

const smoothstep = (edge0: number, edge1: number, x: number) => {
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
};

/** Frame zoom at the start of its life; it eases to 1 as the next frame takes over. */
const FRAME_ZOOM = 0.14;

/**
 * One layer of a scroll-scrubbed image sequence (frames stacked in order, later on top).
 * Frame `i` sits at progress i/(count-1): it dissolves in over the half-step before that
 * point, so it is fully on by its own position and the frames below never show a gap,
 * and it zooms out slowly for as long as it is visible.
 */
export function frameLayer(progress: number, i: number, count: number): { opacity: number; scale: number } {
  const f = clamp01(progress) * Math.max(0, count - 1);
  const opacity = i === 0 ? 1 : smoothstep(i - 0.75, i - 0.25, f);
  const life = clamp01((f - (i - 0.75)) / 1.75);
  return { opacity, scale: 1 + FRAME_ZOOM * (1 - life) };
}

/** Eases `current` towards `target` by `rate` per frame, snapping once within `epsilon`. */
export function approach(current: number, target: number, rate: number, epsilon = 0.001): number {
  const next = current + (target - current) * rate;
  return Math.abs(target - next) < epsilon ? target : next;
}

/**
 * Smart header: hidden while scrolling down past `threshold`, shown on any scroll up of at
 * least `delta` px, and always shown above the threshold.
 */
export function nextHeaderHidden(
  hidden: boolean,
  lastY: number,
  y: number,
  { threshold, delta }: { threshold: number; delta: number },
): boolean {
  if (y < threshold) return false;
  if (y - lastY > delta) return true;
  if (lastY - y > delta) return false;
  return hidden;
}

const REDUCE = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(REDUCE).matches : false;
}

/**
 * Runs `setup` while motion is allowed and tears it down (its returned cleanup) the moment
 * the visitor turns on reduced motion, setting it up again if they turn it back off.
 * Returns a cleanup for the whole subscription.
 */
export function whileMotionAllowed(setup: () => (() => void) | void): () => void {
  const query = typeof window.matchMedia === "function" ? window.matchMedia(REDUCE) : null;
  let teardown: (() => void) | void;
  const apply = () => {
    teardown?.();
    teardown = query?.matches ? undefined : setup();
  };
  apply();
  query?.addEventListener?.("change", apply);
  return () => {
    query?.removeEventListener?.("change", apply);
    teardown?.();
    teardown = undefined;
  };
}

/**
 * Calls `frame` on animation frames while `el` is on screen: once when it comes into view,
 * then on every scroll or resize, and again each frame for as long as `frame` returns true
 * (still easing). Returns a cleanup function.
 */
export function driveWhileVisible(el: Element, frame: () => boolean): () => void {
  let visible = false;
  let raf = 0;

  const tick = () => {
    raf = 0;
    if (visible && frame()) raf = requestAnimationFrame(tick);
  };
  const kick = () => {
    if (visible && !raf) raf = requestAnimationFrame(tick);
  };
  const setVisible = (next: boolean) => {
    if (next === visible) return;
    visible = next;
    if (visible) {
      window.addEventListener("scroll", kick, { passive: true });
      window.addEventListener("resize", kick);
      kick();
    } else {
      window.removeEventListener("scroll", kick);
      window.removeEventListener("resize", kick);
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    }
  };

  let observer: IntersectionObserver | null = null;
  if (typeof IntersectionObserver === "function") {
    observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (entry) setVisible(entry.isIntersecting);
    });
    observer.observe(el);
  } else {
    setVisible(true);
  }

  return () => {
    observer?.disconnect();
    setVisible(false);
  };
}
