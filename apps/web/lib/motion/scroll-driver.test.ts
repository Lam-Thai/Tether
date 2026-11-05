import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  approach,
  driveWhileVisible,
  frameLayer,
  nextHeaderHidden,
  pinProgress,
  prefersReducedMotion,
  stepFor,
} from "@/lib/motion/scroll-driver";

describe("pinProgress", () => {
  const base = { height: 2800, stageHeight: 936, stickyTop: 64 };

  it("is 0 before the stage pins", () => {
    expect(pinProgress({ ...base, top: 64 })).toBe(0);
    expect(pinProgress({ ...base, top: 400 })).toBe(0);
  });

  it("tracks the pinned travel linearly", () => {
    // Travel is height - stageHeight = 1864px; half of it scrolled past the pin point.
    expect(pinProgress({ ...base, top: 64 - 932 })).toBeCloseTo(0.5);
  });

  it("is 1 once the stage unpins", () => {
    expect(pinProgress({ ...base, top: 64 - 1864 })).toBe(1);
    expect(pinProgress({ ...base, top: -9000 })).toBe(1);
  });

  it("is 0 when there is no pinned travel (short section, reduced motion)", () => {
    expect(pinProgress({ top: -200, height: 600, stageHeight: 600, stickyTop: 64 })).toBe(0);
  });
});

describe("stepFor", () => {
  it.each([
    [0, 0],
    [0.32, 0],
    [0.34, 1],
    [0.66, 1],
    [0.67, 2],
    [1, 2],
  ])("progress %s is step %s of 3", (progress, step) => {
    expect(stepFor(progress, 3)).toBe(step);
  });

  it("clamps out-of-range progress and empty counts", () => {
    expect(stepFor(-1, 3)).toBe(0);
    expect(stepFor(2, 3)).toBe(2);
    expect(stepFor(0.5, 0)).toBe(0);
  });
});

describe("frameLayer", () => {
  const n = 5;
  const opacities = (p: number) => Array.from({ length: n }, (_, i) => frameLayer(p, i, n).opacity);

  it("shows only the first frame at the start", () => {
    expect(opacities(0)).toEqual([1, 0, 0, 0, 0]);
  });

  it("shows the last frame on top at the end", () => {
    expect(opacities(1)).toEqual([1, 1, 1, 1, 1]);
  });

  it("has each frame fully on by its own position, so frames never leave a gap", () => {
    for (let i = 1; i < n; i++) {
      expect(frameLayer(i / (n - 1), i, n).opacity).toBe(1);
      expect(frameLayer(i / (n - 1), i + 1, n).opacity).toBe(0);
    }
  });

  it("dissolves the next frame in halfway between positions", () => {
    const mid = frameLayer(0.5 / (n - 1), 1, n).opacity;
    expect(mid).toBeGreaterThan(0.4);
    expect(mid).toBeLessThan(0.6);
  });

  it("zooms each frame out over its life, within 1..1.14", () => {
    const early = frameLayer(0.5 / (n - 1), 1, n).scale;
    const late = frameLayer(1.5 / (n - 1), 1, n).scale;
    expect(early).toBeGreaterThan(late);
    for (let p = 0; p <= 1; p += 0.05) {
      for (let i = 0; i < n; i++) {
        const { scale } = frameLayer(p, i, n);
        expect(scale).toBeGreaterThanOrEqual(1);
        expect(scale).toBeLessThanOrEqual(1.14 + 1e-9);
      }
    }
  });

  it("handles a single frame", () => {
    expect(frameLayer(0.7, 0, 1).opacity).toBe(1);
  });
});

describe("approach", () => {
  it("moves a fraction of the way to the target", () => {
    expect(approach(0, 1, 0.25)).toBeCloseTo(0.25);
  });

  it("snaps to the target once within epsilon", () => {
    expect(approach(0.9995, 1, 0.25)).toBe(1);
  });
});

describe("nextHeaderHidden", () => {
  const opts = { threshold: 480, delta: 6 };

  it("stays visible near the top of the page", () => {
    expect(nextHeaderHidden(false, 100, 300, opts)).toBe(false);
  });

  it("hides when scrolling down past the threshold", () => {
    expect(nextHeaderHidden(false, 600, 700, opts)).toBe(true);
  });

  it("shows again on any real scroll up", () => {
    expect(nextHeaderHidden(true, 900, 850, opts)).toBe(false);
  });

  it("ignores jitter smaller than delta", () => {
    expect(nextHeaderHidden(true, 900, 897, opts)).toBe(true);
    expect(nextHeaderHidden(false, 900, 903, opts)).toBe(false);
  });

  it("shows when back above the threshold", () => {
    expect(nextHeaderHidden(true, 600, 400, opts)).toBe(false);
  });
});

describe("prefersReducedMotion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads the media query", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce") }));
    expect(prefersReducedMotion()).toBe(true);
  });

  it("is false when matchMedia is missing", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(false);
  });
});

describe("driveWhileVisible", () => {
  let ioCallback: ((entries: { isIntersecting: boolean }[]) => void) | null;
  let frames: FrameRequestCallback[];
  const disconnect = vi.fn();

  beforeEach(() => {
    ioCallback = null;
    frames = [];
    disconnect.mockReset();
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(cb: (entries: { isIntersecting: boolean }[]) => void) {
          ioCallback = cb;
        }
        observe() {}
        disconnect() {
          disconnect();
        }
      },
    );
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      frames.push(cb);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames[id - 1] = () => {};
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function flush() {
    const pending = frames;
    frames = [];
    pending.forEach((f) => f(0));
  }

  it("does not run frames until the element is on screen", () => {
    const frame = vi.fn(() => false);
    driveWhileVisible(document.createElement("div"), frame);
    flush();
    expect(frame).not.toHaveBeenCalled();
  });

  it("runs frames while settling, then stops", () => {
    let left = 2;
    const frame = vi.fn(() => --left > 0);
    driveWhileVisible(document.createElement("div"), frame);
    ioCallback?.([{ isIntersecting: true }]);
    flush();
    flush();
    flush();
    expect(frame).toHaveBeenCalledTimes(2);
  });

  it("wakes on scroll while visible and ignores scroll once off screen", () => {
    const frame = vi.fn(() => false);
    driveWhileVisible(document.createElement("div"), frame);
    ioCallback?.([{ isIntersecting: true }]);
    flush();
    window.dispatchEvent(new Event("scroll"));
    flush();
    expect(frame).toHaveBeenCalledTimes(2);

    ioCallback?.([{ isIntersecting: false }]);
    window.dispatchEvent(new Event("scroll"));
    flush();
    expect(frame).toHaveBeenCalledTimes(2);
  });

  it("cleans up the observer and listeners", () => {
    const frame = vi.fn(() => false);
    const stop = driveWhileVisible(document.createElement("div"), frame);
    ioCallback?.([{ isIntersecting: true }]);
    flush();
    stop();
    window.dispatchEvent(new Event("scroll"));
    flush();
    expect(disconnect).toHaveBeenCalled();
    expect(frame).toHaveBeenCalledTimes(1);
  });
});
