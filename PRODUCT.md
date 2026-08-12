# Product

## Register

product

## Users

Engineering teams running Mira on their own infrastructure: pull request authors and reviewers following live reviews, and repository administrators maintaining healthy indexing and model configuration. They need to understand what Mira received, what it is doing, what it found, and whether each background operation is healthy without reading server logs.

## Product Purpose

Mira provides self-hosted, low-noise AI code review with transparent repository context and operational visibility. Success means users can trust the generated review, understand live agent activity, diagnose failed indexing or model calls quickly, and retain control of their code, data, and LLM provider.

## Brand Personality

Focused, transparent, and trustworthy. Technical detail should be presented with the clarity and polish of a dependable engineering tool rather than as raw terminal output or vague AI theater.

## Anti-references

Avoid terminal emulators, raw debug-log streams, decorative AI visualizations, generic metric-card dashboards, and interfaces that hide important activity or errors behind collapsed disclosure controls. Do not make users infer failure from an empty result or leave live-updating content in controls that reset as events arrive.

## Design Principles

- Make agent and indexing activity legible as structured, durable state rather than a log dump.
- Surface failures where users act, with enough context to understand and recover.
- Preserve context and causality so users can understand why each action occurred.
- Keep current state and important findings easy to scan while retaining useful technical evidence.
- Treat real-time updates as enhancement: reconnect gracefully, preserve reading position, and retain complete history.

## Accessibility & Inclusion

Target WCAG AA. Support keyboard navigation, visible focus states, non-color status cues, semantic live updates, and reduced motion. Avoid disruptive announcements and unexpected scroll or disclosure changes while frequent events arrive.
