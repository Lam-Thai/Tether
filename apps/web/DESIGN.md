---
version: "alpha"
name: "Construtivismo Dinâmico Tech"
description: "Functional and dynamic constructivist landing page for an agile tech startup. Ideal for landing pages, modern websites. AI-ready template."
colors:
  primary: "#FF4136"
  secondary: "#000000"
  tertiary: "#FFFFFF"
  neutral: "#333333"
  surface: "#0074D9"
  accent: "#FFDC00"
typography:
  h1:
    fontFamily: Oswald
    fontSize: 2.5rem
    fontWeight: 700
  body-md:
    fontFamily: Oswald
    fontSize: 1rem
    fontWeight: 400
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.neutral}"
    padding: 12px
---

## Overview

Functional and dynamic constructivist landing page for an agile tech startup. Ideal for landing pages, modern websites. AI-ready template. Russian Constructivism wasn't decoration. It was propaganda machinery — visual systems engineered to move masses. Rodchenko's diagonal compositions shattered the picture plane deliberately. El Lissitzky's Prouns floated geometric forms in impossible space, treating the canvas as an architectural site. These weren't aesthetic choices. They were ideological ones. Every angled line carried velocity. Every red wedge drove a point home.

Fast-forward a century and the same visual DNA powers tech landing pages. The diagonal momentum that once sold revolution now sells sprints. Bold geometric overlaps that communicated collective power now communicate product velocity. There's an honest logic to it — Constructivism was always about building systems, about the machine, about progress through structure. Startups inherited that language because they share the obsession: dismantle the old, construct the new, ship it yesterday.

The difference matters though. Rodchenko served a collective. Your Series A deck serves shareholders. Borrow the energy, respect the source. Use the tension of competing angles to create genuine dynamism — not just slap a red triangle on a hero section and call it disruption.

- Density: 5/10 — Balanced
- Variance: 2/10 — Structured
- Motion: 4/10 — Subtle

- **Style:** Functional, Geometry-Driven, Dynamic
- **Keywords:** tech startup, agile, software development, dynamic, geometric, efficient, innovative, structured, modern, fast-paced
- **Era:** 2026+ Inovação Ágil
- **Light/Dark:** ✓ Full / ✗ No

## Colors

- **Vermelho Vibrante** (#FF4136) — Error states, destructive actions
- **Preto** (#000000) — Dark surface, primary background
- **Branco** (#FFFFFF) — Light surface, card backgrounds
- **Cinza Escuro** (#333333) — Dark surface, primary background
- **Azul Elétrico** (#0074D9) — Secondary accent
- **Amarelo Sol** (#FFDC00) — Warning states, attention indicators
- **Verde Limão** (#2ECC40) — Success states, positive indicators
- **Cinza Claro** (#AAAAAA) — Secondary text, borders, muted elements

## Typography

- **Display / Hero:** Oswald — Weight 700, tight tracking, used for headline impact
- **Body:** Oswald — Weight 400, 16px/1.6 line-height, max 72ch per line
- **UI Labels / Captions:** Oswald — 0.875rem, weight 500, slight letter-spacing
- **Monospace:** JetBrains Mono — Used for code, metadata, and technical values

Scale:
- Hero: clamp(2.5rem, 5vw, 4rem)
- H1: 2.25rem
- H2: 1.5rem
- Body: 1rem / 1.6
- Small: 0.875rem

## Layout

- **Grid:** CSS Grid primary. Max-width containment: 1280px centered with 1.5rem side padding.
- **Spacing rhythm:** Balanced. Base unit: 0.5rem (8px).
- **Section vertical gaps:** clamp(4rem, 8vw, 8rem).
- **Hero layout:** Split-screen (text left, visual right).
- **Feature sections:** Zig-zag alternating text+image rows. No 3-equal-columns.
- **Mobile collapse:** All multi-column layouts collapse below 768px. No horizontal overflow.
- **z-index contract:** base (0) / sticky-nav (100) / overlay (200) / modal (300) / toast (500).

## Elevation & Depth

Layouts assimétricos com forte senso de movimento, formas geométricas proeminentes, tipografia sans-serif condensada e ousada, colagens de fotos em preto e branco sobre fundos coloridos, micro-interações de clique com feedback angular, animações de transição rápidas e lineares.

- **Physics:** Ease-out curves, 200-300ms duration. Smooth and predictable.
- **Entry animations:** Fade + translate-Y (16px → 0) over 420ms ease-out. Staggered cascades for lists: 80ms between items.
- **Hover states:** Subtle color shift + shadow adjustment over 200ms.
- **Page transitions:** Fade only (200ms).
- **Performance:** Only transform and opacity animated. No layout-triggering properties.

## Shapes

Base corner radius: 12px. See rounded tokens in front matter for the full scale.

## Components

- **Primary Button:** Moderately rounded (0.75rem) shape. Accent color fill. Hover: 8% darken + subtle lift shadow. Active: -1px translate tactile press. Font weight 600. No outer glows.
- **Secondary / Ghost Button:** Outline variant. 1.5px border in muted color. Text in primary color. Hover: subtle background fill.
- **Cards:** Moderately rounded (0.75rem) corners. Surface background. Subtle shadow (0 2px 12px rgba(0,0,0,0.06)). 1px border stroke.
- **Inputs:** Label above input. 1px border stroke. Focus ring: 2px accent color offset 2px. Error text below in semantic red. No floating labels.
- **Navigation:** Primary surface background. Active item: accent color indicator. Font weight 500 when active.
- **Skeletons:** Shimmer animation matching component dimensions. No circular spinners.
- **Empty States:** Icon-based composition with descriptive text and action button.

## Do's and Don'ts

- No emojis in UI — use icon system only (Lucide, Heroicons)
- No pure black (#000000) — use off-black or charcoal variants
- No oversaturated accent colors (saturation cap: 80%)
- No 3-column equal-width feature layouts — use zig-zag or asymmetric grid
- No `h-screen` — use `min-h-[100dvh]`
- No AI copywriting clichés: "Elevate", "Seamless", "Unleash", "Next-Gen"
- No broken external image links — use picsum.photos or inline SVG
- No generic lorem ipsum in demos

- Do Layouts assimétricos
- Do Formas geométricas
- Do Tipografia sans-serif ousada
- Do Colagens P&B/coloridas
- Do Micro-interações angulares
- Do Animações rápidas.

## Use Case

Landing pages, Modern websites

---

## Resolved decisions

The spec above is kept verbatim. Where it contradicts itself or fails WCAG AA, these resolutions win. They were recorded in issue #27 and ADR 0002, and the tokens in `app/globals.css` implement them. Contrast ratios are against white unless stated otherwise.

| # | Conflict | Resolution |
|---|---|---|
| 1 | `secondary: #000000` vs "No pure black" | Every "black" renders as off-black `#111111`. `#000000` never appears in CSS (enforced by `tooling/repo-hygiene.test.ts`). |
| 2 | Primary button: front matter red fill + `#333` text vs prose "accent color fill" | Front matter wins: `#FF4136` fill with `#111111` text (~5.5:1). `#333` (~3.6:1) and white (~3.5:1) both fail AA. Hover uses `#EB3C32` (8% darker, still ~4.7:1). |
| 3 | Red is brand primary **and** error / destructive | Error and warning states always pair the color with a Lucide icon and a title (`Callout`, `FormError`), never color alone. Destructive actions use the outline variant with dark-red text, never a red fill. |
| 4 | "Light/Dark: ✓ Full / ✗ No" is ambiguous | Light mode only for now. Every color is a CSS variable on `:root`, so dark mode only has to redefine them. |
| 5 | "Rounded tokens in front matter" don't exist | `sm` 6px (badges, skeleton bars, chips), `md` 12px (buttons, inputs, cards, callouts), `lg` 16px, `full`. |
| 6 | "Saturation cap 80%" vs a palette at 100% saturation | The listed palette values are canonical. The cap applies only to derived tints and shades. |
| 7 | Yellow (~1.3:1), green (~2.1:1) and `#AAAAAA` (~2.3:1) fail as text | Yellow and green are fills with `#111111` text on top. `#AAAAAA` is used only for disabled states. Secondary text is `#666666` (~5.7:1). Blue `#0074D9` (~4.7:1) is the link color. |
| 8 | The spec doesn't mention reduced motion | All entry, hover and shimmer motion turns off under `prefers-reduced-motion: reduce`. |
| 9 | Focus ring "2px accent color" (yellow ~1.3:1 fails WCAG 1.4.11) | The focus ring is blue `#0074D9` on light surfaces and yellow on off-black surfaces (~13.9:1). Always 2px with a 2px offset. |
| 10 | A 1px `#AAAAAA` border fails 3:1 for form controls | Form controls use `#8A8A8A` (~3.4:1). Decorative hairlines (cards, dividers) use `#D4D4D4`. |
| 11 | "Error text in semantic red" (`#FF4136` text ~3.5:1) | Error text uses a derived dark red `#BB231B` (~6.2:1, under the saturation cap). `#FF4136` stays a fill and bar color. |
| 12 | No page background named | Page `#F4F4F4`, cards white with a hairline and `0 2px 12px rgb(17 17 17 / 0.06)`, so card edges read on the page. |
| 13 | "Navigation: primary surface background" is ambiguous | The sidebar is off-black. The active item gets a 4px yellow left marker and weight 500. (Not visible yet: every nav item is a disabled placeholder until its slice lands.) |
| 14 | "Cards: surface background" (`surface` is blue in the front matter) | Cards are white, per "Branco: card backgrounds". Blue is an accent, never a large surface. |
| 15 | "Shimmer" skeletons vs the no-gradient rule | The shimmer is a solid, translucent, skewed bar animated with `transform` only. |

### Where things live

- Tokens, motion keyframes, skeleton shimmer: `app/globals.css`.
- z-index contract: `--z-sticky` 100, `--z-overlay` 200, `--z-modal` 300, `--z-toast` 500 (use `z-(--z-sticky)`).
- Entry animation: add `animate-enter`; stagger siblings with `style={{ "--i": n }}` (80ms steps).
- Brand geometry: `components/ui/construct.tsx` (`Wordmark`, `ConstructField`), plain CSS shapes hidden from assistive tech.
- State panels: `components/ui/callout.tsx`. Placeholders: `components/ui/skeleton.tsx`. Icons: `lucide-react` only.
