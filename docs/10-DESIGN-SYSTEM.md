# 10 — Design System

> Light. Premium. Blue. Every visual decision in RootTrace AI, specified.

---

## 1. Design philosophy

RootTrace AI is a tool engineers open when something is broken, often under pressure, sometimes at 3am. The interface must feel **calm, precise, and trustworthy** — a well-lit workshop, not a war room.

| Principle | What it means in practice |
|---|---|
| **Light, always** | White and near-white surfaces. No dark mode in V1. One theme executed perfectly beats two done adequately |
| **Blue is the only brand colour** | Blue for identity and interaction. No orange, no green, no purple, no teal in the brand palette |
| **Colour means status, never decoration** | If something is coloured, it is communicating state. A user should be able to learn "coloured = meaningful" and rely on it |
| **Whitespace is a feature** | Density kills comprehension. Generous spacing is what separates premium from cramped |
| **Depth through subtlety** | Hairline borders and soft, low-opacity shadows. Never heavy drop shadows or thick strokes |
| **Motion clarifies, never entertains** | Animation shows causality and state change. Nothing bounces. Nothing spins for decoration |
| **Type carries the hierarchy** | Weight and size do the structural work so colour doesn't have to |

### On status colours

Semantic status needs red and amber — there is no accessible way to communicate "this failed" without them, and inventing one would be worse design, not better. The rule holds where it matters:

- **Brand, navigation, charts, primary actions, illustration** — blue and neutral only.
- **Status indicators** — a restrained red (danger) and amber (warning) reserved exclusively for state.
- **Success** — a **blue-leaning teal-free green** used sparingly, only for validation-passed states, at low saturation so it reads as "confirmed" rather than "celebration."

No orange. No lime. No purple. No gradient backgrounds.

---

## 2. Colour tokens

```css
:root {
  /* ── Blue: the brand ramp ─────────────────────────────────────── */
  --blue-50:  #EFF6FF;   /* tinted surfaces, selected rows            */
  --blue-100: #DBEAFE;   /* hover on tinted surfaces                  */
  --blue-200: #BFDBFE;   /* borders on tinted surfaces                */
  --blue-300: #93C5FD;   /* disabled primary, chart series 3          */
  --blue-400: #60A5FA;   /* chart series 2                            */
  --blue-500: #3B82F6;   /* chart series 1, icon accents              */
  --blue-600: #2563EB;   /* PRIMARY — buttons, links, focus rings     */
  --blue-700: #1D4ED8;   /* primary hover                             */
  --blue-800: #1E40AF;   /* primary active                            */
  --blue-900: #1E3A8A;   /* deep emphasis text on blue tint           */
  --blue-950: #172554;   /* rare — highest-contrast blue text         */

  /* ── Neutral: cool grey, faint blue undertone ─────────────────── */
  --neutral-0:   #FFFFFF;  /* card, panel, modal surfaces             */
  --neutral-25:  #FCFDFE;  /* elevated surface, subtle lift           */
  --neutral-50:  #F8FAFC;  /* app background                          */
  --neutral-100: #F1F5F9;  /* subtle fill, table header, code bg      */
  --neutral-200: #E2E8F0;  /* BORDERS — the default hairline          */
  --neutral-300: #CBD5E1;  /* stronger border, disabled border        */
  --neutral-400: #94A3B8;  /* placeholder, disabled text, icons-muted */
  --neutral-500: #64748B;  /* secondary text                          */
  --neutral-600: #475569;  /* body text on tinted surfaces            */
  --neutral-700: #334155;  /* BODY TEXT — the default                 */
  --neutral-800: #1E293B;  /* headings                                */
  --neutral-900: #0F172A;  /* strongest text, page titles             */

  /* ── Status: reserved. Never decorative ───────────────────────── */
  --success-50:  #F0FDF4;
  --success-200: #BBF7D0;
  --success-600: #16A34A;   /* validation passed, PR merged           */
  --success-700: #15803D;

  --warning-50:  #FFFBEB;
  --warning-200: #FDE68A;
  --warning-600: #D97706;   /* degraded mode, low confidence, stale   */
  --warning-700: #B45309;

  --danger-50:   #FEF2F2;
  --danger-200:  #FECACA;
  --danger-600:  #DC2626;   /* failed, rejected, P0                   */
  --danger-700:  #B91C1C;

  --info-50:     var(--blue-50);
  --info-600:    var(--blue-600);

  /* ── Semantic aliases — components use THESE, never raw ramps ─── */
  --bg-app:            var(--neutral-50);
  --bg-surface:        var(--neutral-0);
  --bg-surface-raised: var(--neutral-25);
  --bg-subtle:         var(--neutral-100);
  --bg-selected:       var(--blue-50);
  --bg-hover:          var(--neutral-50);

  --text-primary:      var(--neutral-900);
  --text-body:         var(--neutral-700);
  --text-secondary:    var(--neutral-500);
  --text-muted:        var(--neutral-400);
  --text-on-primary:   var(--neutral-0);
  --text-link:         var(--blue-600);

  --border-default:    var(--neutral-200);
  --border-strong:     var(--neutral-300);
  --border-focus:      var(--blue-600);
  --border-selected:   var(--blue-200);

  --action-primary:          var(--blue-600);
  --action-primary-hover:    var(--blue-700);
  --action-primary-active:   var(--blue-800);
  --action-primary-disabled: var(--blue-300);
}
```

### Confidence band colours

Confidence is the most important number in the product. It gets its own scale, blue-anchored:

| Band | Range | Fill | Text | Border |
|---|---|---|---|---|
| High | ≥ 0.80 | `--blue-600` | `--neutral-0` | — |
| Medium | 0.60–0.79 | `--blue-100` | `--blue-800` | `--blue-200` |
| Low | 0.40–0.59 | `--warning-50` | `--warning-700` | `--warning-200` |
| Insufficient | < 0.40 | `--neutral-100` | `--neutral-600` | `--neutral-200` |

High confidence is the **strongest blue in the interface**. It is the moment the product delivers its promise, and it should feel like it.

### Severity colours

| Severity | Fill | Text |
|---|---|---|
| P0 | `--danger-600` | `--neutral-0` |
| P1 | `--danger-50` | `--danger-700` |
| P2 | `--warning-50` | `--warning-700` |
| P3 | `--neutral-100` | `--neutral-600` |

### Chart palette — blue-only, ordered

```css
--chart-1: #2563EB;  /* blue-600  */
--chart-2: #60A5FA;  /* blue-400  */
--chart-3: #93C5FD;  /* blue-300  */
--chart-4: #1E40AF;  /* blue-800  */
--chart-5: #BFDBFE;  /* blue-200  */
--chart-grid:  #F1F5F9;
--chart-axis:  #94A3B8;
--chart-label: #64748B;
```

Series are distinguished by lightness, not hue. This is deliberate: it is colour-blind safe by construction, prints legibly in greyscale, and keeps the chart visually quiet.

---

## 3. Typography

```css
:root {
  --font-sans: "Inter var", "Inter", -apple-system, BlinkMacSystemFont,
               "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Fira Code", ui-monospace, monospace;

  --text-xs:   0.75rem;   /* 12px — labels, badges, table meta        */
  --text-sm:   0.875rem;  /* 14px — body default, table cells         */
  --text-base: 1rem;      /* 16px — emphasised body                   */
  --text-lg:   1.125rem;  /* 18px — card titles                       */
  --text-xl:   1.25rem;   /* 20px — section headings                  */
  --text-2xl:  1.5rem;    /* 24px — page titles                       */
  --text-3xl:  1.875rem;  /* 30px — KPI values                        */
  --text-4xl:  2.25rem;   /* 36px — hero metrics                      */

  --leading-tight:  1.25;
  --leading-normal: 1.5;
  --leading-relaxed:1.625;

  --tracking-tight:  -0.02em;   /* large headings and KPI numerals    */
  --tracking-normal: 0;
  --tracking-wide:   0.02em;    /* small caps labels                  */
}
```

| Role | Size | Weight | Colour | Tracking |
|---|---|---|---|---|
| Page title | `2xl` | 600 | `--text-primary` | tight |
| Section heading | `xl` | 600 | `--text-primary` | tight |
| Card title | `lg` | 600 | `--text-primary` | normal |
| Body | `sm` | 400 | `--text-body` | normal |
| Secondary | `sm` | 400 | `--text-secondary` | normal |
| Label / eyebrow | `xs` | 500, uppercase | `--text-secondary` | wide |
| KPI value | `3xl` | 650 | `--text-primary` | tight, **tabular-nums** |
| Code inline | `sm` mono | 400 | `--text-body` on `--bg-subtle` | normal |
| Code block | `sm` mono | 400 | `--text-body` | normal |

**All numerals in tables, KPIs, and charts use `font-variant-numeric: tabular-nums`.** Numbers that shift horizontally as they update look broken; this single property is the difference between a dashboard that feels engineered and one that doesn't.

---

## 4. Spacing, radius, elevation

```css
:root {
  --space-0-5: 0.125rem;  --space-1: 0.25rem;  --space-1-5: 0.375rem;
  --space-2:   0.5rem;    --space-3: 0.75rem;  --space-4:   1rem;
  --space-5:   1.25rem;   --space-6: 1.5rem;   --space-8:   2rem;
  --space-10:  2.5rem;    --space-12:3rem;     --space-16:  4rem;

  --radius-sm:   0.25rem;   /* badges, small inputs      */
  --radius-md:   0.375rem;  /* buttons, inputs           */
  --radius-lg:   0.5rem;    /* cards, panels             */
  --radius-xl:   0.75rem;   /* modals, large containers  */
  --radius-full: 9999px;    /* pills, avatars            */

  /* Soft, low-opacity, cool-tinted. Never harsh. */
  --shadow-xs: 0 1px 2px 0 rgb(15 23 42 / 0.04);
  --shadow-sm: 0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.04);
  --shadow-md: 0 4px 6px -1px rgb(15 23 42 / 0.07), 0 2px 4px -2px rgb(15 23 42 / 0.04);
  --shadow-lg: 0 10px 15px -3px rgb(15 23 42 / 0.08), 0 4px 6px -4px rgb(15 23 42 / 0.04);
  --shadow-xl: 0 20px 25px -5px rgb(15 23 42 / 0.08), 0 8px 10px -6px rgb(15 23 42 / 0.04);
  --shadow-focus: 0 0 0 3px rgb(37 99 235 / 0.15);
}
```

### Elevation levels

| Level | Use | Border | Shadow |
|---|---|---|---|
| 0 | App background | — | — |
| 1 | Cards, panels, table containers | `1px --border-default` | `--shadow-xs` |
| 2 | Hovered card, dropdown, popover | `1px --border-default` | `--shadow-md` |
| 3 | Modal, command palette | `1px --border-default` | `--shadow-xl` |
| 4 | Toast | `1px --border-default` | `--shadow-lg` |

**Cards always have a hairline border in addition to their shadow.** On a near-white background, shadow alone leaves edges ambiguous; the border is what makes the layout read crisply.

---

## 5. Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│  TOP BAR                                                        64px   │
│  logo · project switcher            ⌘K search · health · avatar        │
├──────────┬─────────────────────────────────────────────────────────────┤
│          │                                                             │
│ SIDEBAR  │  CONTENT                                                    │
│  240px   │  max-width 1440px, centred, 32px horizontal padding         │
│          │                                                             │
│ Overview │  ┌──────────────────────────────────────────────────────┐   │
│ Issues   │  │  Page header — title, subtitle, actions              │   │
│ Investig.│  ├──────────────────────────────────────────────────────┤   │
│ Logs     │  │                                                      │   │
│ Analytics│  │  Content                                             │   │
│ ──────── │  │                                                      │   │
│ Repos    │  └──────────────────────────────────────────────────────┘   │
│ API Keys │                                                             │
│ Settings │                                                             │
└──────────┴─────────────────────────────────────────────────────────────┘
```

| Breakpoint | Width | Behaviour |
|---|---|---|
| `sm` | 640px | Sidebar → bottom tab bar. Tables → stacked cards |
| `md` | 768px | Sidebar → icon rail (64px) |
| `lg` | 1024px | Full sidebar. Two-column detail layouts |
| `xl` | 1280px | Three-column investigation layout |
| `2xl` | 1536px | Max content width reached |

Grid: 12 columns, 24px gutters, 32px page margins.

---

## 6. Component specifications

### 6.1 Button

| Variant | Background | Text | Border | Hover | Use |
|---|---|---|---|---|---|
| Primary | `--blue-600` | white | — | `--blue-700` | The one main action per view |
| Secondary | white | `--neutral-700` | `--border-default` | bg `--neutral-50` | Common actions |
| Ghost | transparent | `--neutral-600` | — | bg `--neutral-100` | Tertiary, icon buttons |
| Danger | white | `--danger-600` | `--danger-200` | bg `--danger-50` | Destructive |
| Link | transparent | `--blue-600` | — | underline | Inline |

```
Sizes    sm  28px  ·  px-3  ·  text-xs   ·  gap-1.5
         md  36px  ·  px-4  ·  text-sm   ·  gap-2     ← default
         lg  44px  ·  px-5  ·  text-base ·  gap-2

Radius   --radius-md
Focus    outline: none; box-shadow: var(--shadow-focus)
Disabled opacity .5; cursor not-allowed
Loading  spinner replaces the leading icon; label persists; width does not change
```

Never more than one Primary button visible in a view. If two actions feel equally primary, the hierarchy is wrong.

### 6.2 Card

```css
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
  padding: var(--space-6);
}
.card--interactive:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
  transition: all 150ms cubic-bezier(0.4, 0, 0.2, 1);
}
```

### 6.3 Badge

```
Height 20px · px-2 · text-xs · weight 500 · radius-full
Optional 6px leading dot for status badges
```

| Kind | Example | Fill / Text |
|---|---|---|
| Confidence | `0.85 high` | Per §2 confidence table |
| Severity | `P1` | Per §2 severity table |
| Status | `awaiting review` | `--blue-50` / `--blue-700` |
| Neutral | `python` | `--neutral-100` / `--neutral-600` |
| Count | `1,247` | `--neutral-100` / `--neutral-700`, tabular-nums |

### 6.4 Table

```
Header    bg --neutral-50 · text-xs · 500 · uppercase · tracking-wide
          · --text-secondary · h 40px · sticky
Row       h 52px · border-bottom 1px --border-default
Hover     bg --neutral-50
Selected  bg --blue-50 · 2px left border --blue-600
Cell      px-4 · text-sm · --text-body
Numeric   right-aligned · tabular-nums
Empty     centred illustration + one-line explanation + one action
Loading   skeleton rows matching real row height — never a spinner over a table
```

Row height 52px is deliberate. 40px is dense enough to feel cramped when each row carries a badge, a sparkline, and a timestamp; 52 gives them room to breathe without wasting vertical space.

### 6.5 Pipeline stage node — the signature component

The live pipeline viewer is the most distinctive thing in the product. It must feel alive without being noisy.

```
┌─────────────────────────────────────────────────────────┐
│  ●  Validate                              41.2s   ⌄     │   ← collapsed
│     All 9 gates passed · 47/47 tests                    │
└─────────────────────────────────────────────────────────┘
```

| State | Dot | Border | Background | Text |
|---|---|---|---|---|
| Pending | `--neutral-300` hollow | `--border-default` | white | `--text-muted` |
| Running | `--blue-600` + pulse ring | `--blue-200` | `--blue-50` | `--text-primary` |
| Completed | `--blue-600` filled + check | `--border-default` | white | `--text-body` |
| Failed | `--danger-600` filled | `--danger-200` | `--danger-50` | `--danger-700` |
| Terminal | `--warning-600` filled | `--warning-200` | `--warning-50` | `--warning-700` |
| Skipped | `--neutral-300` | `--border-default` | `--neutral-50` | `--text-muted` |

```css
@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0   rgb(37 99 235 / 0.35); }
  70%  { box-shadow: 0 0 0 8px rgb(37 99 235 / 0);    }
  100% { box-shadow: 0 0 0 0   rgb(37 99 235 / 0);    }
}
.stage-dot--running { animation: pulse-ring 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
```

Connectors between stages are 2px vertical lines: `--neutral-200` when pending, `--blue-600` once traversed. The line "fills" downward over 400ms as a stage completes — the single piece of motion in the product that exists partly for delight, and it earns its place because it communicates real progress.

### 6.6 Diff viewer

Monaco in diff mode, restyled to the light palette:

```
Added line      bg #F0FDF4  ·  left border 3px --success-600  ·  gutter +
Removed line    bg #FEF2F2  ·  left border 3px --danger-600   ·  gutter −
Context         bg white    ·  --text-body
Line numbers    --text-muted · --bg-subtle · tabular-nums · right-aligned
Highlighted     bg --blue-50 (when linked from an evidence citation)
Font            --font-mono · 13px · line-height 20px
```

Green and red appear here only. Diff colouring is a universal convention, and overriding it with blue would make the product harder to use in service of a rule that exists to make it easier.

### 6.7 Evidence citation

Every AI claim renders with an attached citation chip:

```
┌────────────────────────────────────────────────────────────┐
│  tax_amount is assigned from TaxClient.get_rate() with     │
│  no None check.                                            │
│                                                            │
│  📄 services/checkout.py:136-139                     ↗     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 136   def calculate_total(cart, user):               │  │
│  │ 137       base_price = cart.subtotal()               │  │
│  │ 138 ▸     tax_amount = self.tax_client.get_rate(...)  │  │
│  │ 139       subtotal = base_price + tax_amount         │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

```
Container   --bg-subtle · radius-md · border-left 3px --blue-600 · p-3
Path        --font-mono · text-xs · --blue-700 · clickable → opens in context
Excerpt     --font-mono · text-xs · max 6 lines · expandable
Cited line  bg --blue-50 · ▸ marker in the gutter
```

This component is the visual embodiment of principle P2 (`00` §4). It should appear everywhere a claim is made.

### 6.8 Confidence meter

```
   Confidence                                    0.84  high
   ████████████████████████████████████░░░░░░░░
   ┆                                    ┆
   0                                   0.85          1.0

   ▸ Sandbox validation      30%  ████████████████████  0.95
   ▸ Independent review      20%  █████████████░░░░░░░  0.67
   ▸ Retrieval quality       15%  █████████████████░░░  0.86
   ▸ Evidence binding        15%  ████████████████████  1.00
   ▸ Model self-assessment   10%  █████████████████░░░  0.88
   ▸ Historical accuracy     10%  ██████████░░░░░░░░░░  0.50
```

```
Track    h 8px · radius-full · bg --neutral-200
Fill     h 8px · radius-full · gradient --blue-500 → --blue-600
Value    text-2xl · 650 · tabular-nums · --text-primary
Band     badge per §2
Rows     expandable; each shows weight, bar, and raw score
```

The breakdown is expanded by default. The number alone is a black box; the breakdown is the argument for trusting it.

### 6.9 Sandbox console

```
Container  bg --neutral-900 · radius-lg · --font-mono · 12.5px · p-4
Text       #E2E8F0
stdout     #E2E8F0
stderr     #FCA5A5
PASSED     #86EFAC
FAILED     #FCA5A5
Timestamp  #64748B
Header     bar with gate name, duration, copy button, wrap toggle
Behaviour  auto-scroll while streaming; pauses on manual scroll;
           "jump to latest" pill appears when paused
```

This is the one deliberately dark surface in the product. A terminal that isn't dark reads as fake, and the whole point of showing the transcript is that it is the real thing.

### 6.10 Empty states

Every list has a designed empty state. Never a blank panel.

```
       ┌──────────────┐
       │   ◯ ◯ ◯      │      simple line illustration,
       │      │       │      --neutral-300 strokes, blue accent
       └──────────────┘

       No investigations yet

       Investigations start automatically when an error
       matching your severity threshold arrives.

       [ Send a test event ]   [ Read the setup guide ]
```

---

## 7. Motion

```css
:root {
  --ease-out:    cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
  --duration-instant: 75ms;
  --duration-fast:    150ms;
  --duration-normal:  250ms;
  --duration-slow:    400ms;
}
```

| Interaction | Duration | Easing |
|---|---|---|
| Hover, focus | 150ms | `ease-out` |
| Dropdown, popover | 150ms | `ease-out` |
| Modal enter | 250ms | `ease-out` |
| Modal exit | 150ms | `ease-in-out` |
| Stage transition | 400ms | `ease-out` |
| Connector fill | 400ms | `ease-out` |
| Skeleton shimmer | 1.5s loop | linear |
| Toast enter/exit | 250ms / 150ms | `ease-out` |

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

**Never:** bounce, spring overshoot, rotation for decoration, parallax, auto-playing anything.

---

## 8. Accessibility

Target: **WCAG 2.1 AA**, with AAA on body text where achievable.

| Requirement | Implementation |
|---|---|
| Contrast — body | `--neutral-700` on white = 10.7:1 (AAA) |
| Contrast — secondary | `--neutral-500` on white = 5.9:1 (AA) |
| Contrast — primary button | white on `--blue-600` = 5.2:1 (AA) |
| Contrast — links | `--blue-600` on white = 5.2:1 (AA) |
| Focus visible | 3px `--shadow-focus` ring on every interactive element. Never `outline: none` without a replacement |
| Keyboard | Every action reachable. Logical tab order. Focus trapped in modals and returned on close |
| Status not colour-alone | Every status badge pairs colour with an icon and a text label |
| Charts | Series distinguished by lightness and direct labels, not hue alone |
| Screen readers | Semantic HTML; `aria-live="polite"` on the pipeline viewer so stage changes are announced |
| Motion | `prefers-reduced-motion` respected globally |
| Touch targets | Minimum 44×44px on mobile |
| Zoom | Usable to 200% without horizontal scroll |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `⌘K` / `Ctrl+K` | Command palette |
| `g` then `o` | Overview |
| `g` then `i` | Issues |
| `g` then `v` | Investigations |
| `g` then `l` | Logs |
| `/` | Focus search |
| `j` / `k` | Next / previous row |
| `Enter` | Open focused row |
| `Esc` | Close overlay, clear selection |
| `?` | Shortcut help |

---

## 9. Tailwind configuration

```js
// apps/web/tailwind.config.ts
export default {
  theme: {
    extend: {
      colors: {
        blue:    { 50:'#EFF6FF',100:'#DBEAFE',200:'#BFDBFE',300:'#93C5FD',
                   400:'#60A5FA',500:'#3B82F6',600:'#2563EB',700:'#1D4ED8',
                   800:'#1E40AF',900:'#1E3A8A',950:'#172554' },
        neutral: { 0:'#FFFFFF', 25:'#FCFDFE', 50:'#F8FAFC',100:'#F1F5F9',
                   200:'#E2E8F0',300:'#CBD5E1',400:'#94A3B8',500:'#64748B',
                   600:'#475569',700:'#334155',800:'#1E293B',900:'#0F172A' },
        success: { 50:'#F0FDF4',200:'#BBF7D0',600:'#16A34A',700:'#15803D' },
        warning: { 50:'#FFFBEB',200:'#FDE68A',600:'#D97706',700:'#B45309' },
        danger:  { 50:'#FEF2F2',200:'#FECACA',600:'#DC2626',700:'#B91C1C' },
      },
      fontFamily: {
        sans: ['Inter var','Inter','system-ui','sans-serif'],
        mono: ['JetBrains Mono','SF Mono','ui-monospace','monospace'],
      },
      boxShadow: {
        xs:'0 1px 2px 0 rgb(15 23 42 / 0.04)',
        sm:'0 1px 3px 0 rgb(15 23 42 / 0.06), 0 1px 2px -1px rgb(15 23 42 / 0.04)',
        md:'0 4px 6px -1px rgb(15 23 42 / 0.07), 0 2px 4px -2px rgb(15 23 42 / 0.04)',
        lg:'0 10px 15px -3px rgb(15 23 42 / 0.08), 0 4px 6px -4px rgb(15 23 42 / 0.04)',
        xl:'0 20px 25px -5px rgb(15 23 42 / 0.08), 0 8px 10px -6px rgb(15 23 42 / 0.04)',
        focus:'0 0 0 3px rgb(37 99 235 / 0.15)',
      },
      animation: { 'pulse-ring':'pulse-ring 2s cubic-bezier(0.4,0,0.6,1) infinite' },
    },
  },
} satisfies Config;
```

---

## 10. Design review checklist

Before any UI ships:

- [ ] No colour outside the token set
- [ ] No orange, purple, teal, or gradient backgrounds
- [ ] Green and red appear **only** in diffs and status indicators
- [ ] Every status conveys meaning without relying on colour alone
- [ ] All numerals use `tabular-nums`
- [ ] Every interactive element has a visible focus ring
- [ ] Every list has a designed empty state
- [ ] Loading uses skeletons matching real content dimensions, not spinners
- [ ] Exactly one Primary button per view
- [ ] Contrast verified against WCAG AA
- [ ] Keyboard-only navigation completes the flow
- [ ] `prefers-reduced-motion` honoured
- [ ] Layout holds from 320px to 2560px
- [ ] Every AI claim in view carries an evidence citation

---

*Next: [`11-SECURITY.md`](./11-SECURITY.md)*
