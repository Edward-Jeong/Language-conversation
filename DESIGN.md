---
version: "1.0"
name: "Language Conversation"
description: "A calm, mobile-first daily English practice room for one Korean-speaking learner."
colors:
  primary: "#173D32"
  secondary: "#4F665E"
  accent: "#26745A"
  accent-soft: "#E3EFE8"
  background: "#F6F1E7"
  surface: "#FFFDF7"
  surface-raised: "#FFFFFF"
  text: "#1C2A25"
  text-muted: "#65736D"
  border: "#D9D5C9"
  success: "#26745A"
  warning: "#A46018"
  danger: "#A53B32"
typography:
  heading-lg:
    fontFamily: "ui-serif, Georgia, serif"
    fontSize: "2rem"
    fontWeight: 650
    lineHeight: 1.16
  heading-md:
    fontFamily: "ui-serif, Georgia, serif"
    fontSize: "1.45rem"
    fontWeight: 650
    lineHeight: 1.25
  body-md:
    fontFamily: "Pretendard, Inter, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  label-sm:
    fontFamily: "Pretendard, Inter, system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 700
    lineHeight: 1.4
rounded:
  sm: "8px"
  md: "14px"
  lg: "22px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "13px 16px"
  input-default:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "13px 14px"
  card-default:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    padding: "20px"
---

# Language Conversation Design System

## Overview

Language Conversation is a private daily practice room for a Korean-speaking adult who can answer briefly in English but needs support building sentences and continuing a conversation. It should feel like a well-used workbook beside a quiet cup of tea: focused, forgiving, and dependable. The experience must never imply a public social feed, a score-driven course, or guaranteed fluency.

The core task is a hands-free turn loop on a phone. Current listening, speaking, processing, paused, offline, mock, and unavailable states must be readable at a glance and announced in text. Feedback is compact: at most one needed correction and one clearly optional alternative.

## Colors

Warm paper surfaces reduce glare while deep botanical green carries navigation and action. Use semantic tokens consistently. Warning, danger, success, recording, and selection always include text or an icon/shape so color is not the only cue. Mock and real provider modes use labeled badges.

## Typography

Serif headings give lesson prompts a workbook voice; Korean UI copy and controls use a system sans stack for clarity. English prompts may use the serif face at a slightly larger size. Body copy stays at 16px or above so mobile browsers do not zoom form inputs.

## Layout

- Support 360–430 CSS px without horizontal scrolling, including safe-area insets and enlarged text.
- Use one fluid column with a 720px maximum content width. At 880px and above, home/status content may use a two-column grid.
- Phones use persistent bottom navigation with five labeled destinations. Desktop moves the same navigation into the page header.
- Conversation puts question, media state, primary action, transcript, and feedback in that order. The next question is absent from the DOM during required retry or transfer preparation.
- Tap targets are at least 44px high. Fixed controls account for iOS safe areas.

## Elevation & Depth

Hierarchy comes from warm canvas contrast, thin borders, and spacing. Cards use a small soft shadow only when raised above the paper. Dialogs use a dim solid overlay; no glass effects or gradients.

## Shapes

Cards use the large radius, fields and buttons the medium radius, and compact status badges the full radius. Avoid decorative pill clusters; pills are reserved for status and metadata.

## Components

- Primary buttons are solid green. Secondary controls use the paper surface and border. Destructive actions use danger text/border and require a typed or explicit confirmation.
- Disabled controls retain readable labels and explain unavailable provider or session state nearby.
- Inputs show visible labels, validation copy, and a strong green focus ring.
- Status rows pair a small geometric indicator with plain-language Korean state text.
- Loading, empty, offline, permission-denied, provider-unavailable, and retryable errors each provide a concrete recovery action.
- Hints disclose H1 → H2 → H3 only after the server records exposure. Full examples are never pre-rendered while a transfer response is being collected.

## Accessibility

- Target WCAG 2.2 AA contrast, semantic landmarks and headings, keyboard operation, visible focus, and polite live regions for media-state changes.
- Do not automatically resume microphone capture after backgrounding, device change, offline transition, or page restoration. Resume requires a visible user action.
- Honor reduced motion and avoid countdown pressure. The quiet hint offer appears after eight seconds without forcing disclosure.

## Motion

Use only short opacity/position transitions for cards and a restrained listening pulse. Disable both under `prefers-reduced-motion`. No motion may delay stop, pause, or correction actions.

## Data Visualization

Growth information uses labeled ratios and short evidence rows rather than scores or decorative charts. Always show the denominator for independent-answer rates and avoid claims about general learning effectiveness.

## Do's and Don'ts

### Do

- Keep the current voice and learning states independently visible.
- Make recovery from mic, network, server, or speech-output failure explicit.
- Distinguish required correction from an optional natural alternative.
- Preserve a clear text-input fallback alongside the automatic voice loop.

### Don't

- Do not expose a pending next question during retry.
- Do not label mock output as real AI or browser voices as guaranteed on-device.
- Do not use streak pressure, proficiency promises, or punitive skip language.
- Do not cache audio, transcripts, feedback, history, or API responses in the service worker.
