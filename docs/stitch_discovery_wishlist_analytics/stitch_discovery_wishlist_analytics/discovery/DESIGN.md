---
name: Discovery
colors:
  surface: '#fff8f4'
  surface-dim: '#e0d9d3'
  surface-bright: '#fff8f4'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#faf2ed'
  surface-container: '#f4ece7'
  surface-container-high: '#efe7e1'
  surface-container-highest: '#e9e1dc'
  on-surface: '#1e1b18'
  on-surface-variant: '#5b4042'
  inverse-surface: '#33302c'
  inverse-on-surface: '#f7efea'
  outline: '#8f6f72'
  outline-variant: '#e3bdc0'
  surface-tint: '#bd0043'
  primary: '#b90041'
  on-primary: '#ffffff'
  primary-container: '#df2457'
  on-primary-container: '#fffbff'
  inverse-primary: '#ffb2ba'
  secondary: '#6d5960'
  on-secondary: '#ffffff'
  secondary-container: '#f3d9e1'
  on-secondary-container: '#715d64'
  tertiary: '#4a579d'
  on-tertiary: '#ffffff'
  tertiary-container: '#6370b7'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffd9dc'
  primary-fixed-dim: '#ffb2ba'
  on-primary-fixed: '#400011'
  on-primary-fixed-variant: '#910031'
  secondary-fixed: '#f6dce4'
  secondary-fixed-dim: '#d9c0c8'
  on-secondary-fixed: '#26171d'
  on-secondary-fixed-variant: '#544248'
  tertiary-fixed: '#dee0ff'
  tertiary-fixed-dim: '#bac3ff'
  on-tertiary-fixed: '#001159'
  on-tertiary-fixed-variant: '#344186'
  background: '#fff8f4'
  on-background: '#1e1b18'
  surface-variant: '#e9e1dc'
typography:
  display:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  number-data:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  sidebar_width: 240px
  drawer_width: 480px
  base_grid: 8px
  container_padding: 32px
  gutter: 24px
  card_padding: 24px
---

## Brand & Style

The design system is engineered for high-utility research analytics, blending the precision of data science tools with the sophisticated aesthetic of a high-end fashion editorial. It targets analysts and stakeholders who require clarity, speed, and evidence-backed insights.

The style is **Minimalist-Professional**. It utilizes a restrained color palette, heavy emphasis on whitespace as a functional tool for cognitive de-cluttering, and razor-sharp execution of layout. The emotional response is one of calm authority—moving away from the chaotic "fast fashion" energy toward a grounded, scholarly environment for retail intelligence. There are no decorative flourishes; every element serves the data.

## Colors

This design system uses a hierarchical color strategy to maintain focus. The **Canvas** color provides a warm, tactile base that reduces eye strain during long sessions. **Primary Accent (Myntra Rose)** is used with extreme discipline, reserved strictly for primary calls to action, brand identity, and active navigation states.

Sentiment and Intent colors are functional, not decorative. They follow a semiotic logic:
- **Sentiment:** Used for qualitative analysis tags and score indicators.
- **Intent:** Uses specific Indigo and Terracotta tones to distinguish between time-based consumer behaviors.
- **Confidence Levels:** High (Solid Green), Thin (Amber), and Decline (Red Outline only) provide immediate visual status without overwhelming the dashboard with solid blocks of saturated color.

## Typography

The design system utilizes **Inter** for its neutral, highly legible grotesque characteristics. To ensure data precision, all numerical values must utilize **Tabular Lining figures** (`tnum`, `lnum`), preventing vertical misalignment in data grids and tables.

- **Scale:** High-contrast between headlines and body text to facilitate skimming.
- **Labels:** Uppercase is permitted only for short labels or secondary metadata to maintain an editorial feel.
- **Weights:** Use Medium (500) for interactive elements and Semibold (600) for hierarchy. Avoid Bold (700) to keep the "light-touch" aesthetic.

## Layout & Spacing

This design system follows an **8px linear grid** with a fluid-fixed hybrid layout model.

1.  **Sidebar:** A fixed 240px navigation area on the far left.
2.  **Main Content:** A fluid area that expands to fill the viewport, utilizing a 12-column grid for dashboard widgets.
3.  **Right Drawer:** A 480px overlay for deep-dive evidence, transcripts, or granular data filters.
4.  **Density:** Maintain "Medium-High" density. Information is packed tightly within components, but components themselves are separated by generous 32px margins to prevent visual noise. 
5.  **Alignment:** All elements must align to the 8px baseline to maintain the rigorous, engineered feel of a professional tool.

## Elevation & Depth

Depth is communicated through **Tonal Layering** rather than shadows. 
- **Level 0 (Canvas):** #F7F4F1. The foundational background.
- **Level 1 (Surface):** #FFFFFF. Used for cards and primary content containers.
- **Level 2 (Inlay):** #F7F4F1 (Nested). Used for code blocks or inset data tables within a card.

**Borders:** Use 1px "Hairline" borders (#E7E1DC) to define all interactive and structural boundaries. Avoid drop shadows entirely; if a floating element (like a dropdown) requires separation, use a subtle 1px border with a 4px blur at 5% opacity to provide just enough lift without breaking the flat editorial aesthetic.

## Shapes

The shape language is controlled and systematic.
- **Cards:** Defined at 12px for a modern, approachable feel that remains professional.
- **Chips/Badges:** 8px radius, creating a distinct "pill-lite" look for metadata without being fully circular.
- **Interactive Elements:** Buttons and Input fields use a tighter 6px radius to signify their "utility" nature compared to "container" shapes.

## Components

- **Buttons:** Primary buttons use #FF3F6C with white text. Secondary buttons are ghost-style with a 1px border. No gradients.
- **Cards:** Must include a 1px border (#E7E1DC). Headers within cards should be separated by a hairline horizontal rule.
- **Chips:** Sentiment chips use low-saturation background tints with high-contrast text. For "Decline" confidence, use a transparent background with a 1px dashed red outline.
- **Data Tables:** No vertical lines. Use subtle horizontal rules. Row hover state should be #F7F4F1. 
- **Navigation:** Active sidebar items use a 4px vertical "Myntra Rose" indicator on the left edge and a #FFE4EC background tint.
- **Input Fields:** 1px border (#E7E1DC). On focus, the border changes to #1A1714, never the primary rose color, to keep the focus on the task.
- **Data Viz:** Charts should use the primary/secondary accent for focus series and neutral greys for background series.