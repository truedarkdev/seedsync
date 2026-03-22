# Subject 21 - User-Facing Conflict Review

Pass date: 2026-03-10
Integration base: `master` @ `242f1345`

This note records the meaningful cross-cutting UX/workflow candidates reviewed for Subject 21 using the `user value`, `faithfulness`, `scope`, and `gateability` rubric from `AGENTS.md`.

## thejuran

- Candidate: terminal presentation suite
  - Source: `ef728cc2`, `6865ea03`, `29e7d5d0`, `42d75b03`, `0bdeef59`, `b7fdff1a`, `d8570982`, `12a05c86`
  - Value: mostly taste / style / flavor
  - Faithfulness: D
  - Scope: global
  - Gateability: somewhat messy
  - Action: maintainer decision needed before optional/adapt/reject is finalized
  - Maintainer input: yes
  - Rationale: the suite is a coherent alternate product personality rather than a narrow fix. It should not become the default. Whether any of it belongs as optional configuration depends on whether this repo wants a global visual-mode system at all.

- Candidate: icon-rail sidebar and prompt/footer shell flavor
  - Source: `f64325e3`, `9dd1d796`, `a32dfad7`, `70153570`
  - Value: mostly taste / style / flavor
  - Faithfulness: C
  - Scope: sectional
  - Gateability: somewhat messy
  - Action: reject for default; final optional/adapt treatment tied to the global visual-mode decision
  - Maintainer input: yes
  - Rationale: the icon rail and terminal-style shell cues are recognizable fork flavor and would noticeably change navigation identity. They are not required for correctness.

- Candidate: status-dot / ASCII-progress / ghost-button dashboard visuals
  - Source: `72699bdf`, `93f1a0f6`, `b9c232c7`
  - Value: mostly taste / style / flavor
  - Faithfulness: D
  - Scope: sectional
  - Gateability: cleanly isolatable
  - Action: reject
  - Maintainer input: no
  - Rationale: these changes restyle the dashboard into the terminal suite rather than improving the original SeedSync presentation.

## rapidcopy

- Candidate: theme toggle and light/dark theme system
  - Source: `58ead058`, `fb4e7db4`
  - Value: useful enhancement
  - Faithfulness: C
  - Scope: global
  - Gateability: cleanly isolatable
  - Action: maintainer decision needed before optional/adapt/reject is finalized
  - Maintainer input: yes
  - Rationale: unlike thejuran's terminal suite, this is a more conventional optional theme system, but it still creates a new global settings model and sets precedent for configurable product identity. It should not be added without explicit maintainer approval.

- Candidate: rebrand assets and RapidCopy naming
  - Source: `08d714e6`, `6d59994d`
  - Value: mostly taste / style / flavor
  - Faithfulness: F
  - Scope: global
  - Gateability: cleanly isolatable
  - Action: reject
  - Maintainer input: no
  - Rationale: this directly replaces SeedSync identity rather than extending it.

- Candidate: path pairs, network mounts, validation UI, self-update, API auth
  - Source: `d1436386`, `0b49f975`, `fc571139`, `936ae4b2`, `9f91d1c4`
  - Value: useful enhancement
  - Faithfulness: mixed
  - Scope: global
  - Gateability: mixed
  - Action: covered elsewhere
  - Maintainer input: no
  - Rationale: these belong to their primary feature or backend subjects rather than Subject 21's cross-cutting UX conflict review.

## Default Drift Review

- Current default still reads as SeedSync overall: light theme, familiar route structure, familiar status iconography, and familiar settings framing remain intact.
- The most obvious drift pressure comes from dashboard workflow and selection features that were integrated earlier, but they still read as functional extensions rather than a replacement product identity.
- The biggest remaining drift risk is not the current default; it is whether Subject 21 introduces a new global visual-mode system. That should be decided explicitly rather than by incremental styling imports.

## Deferred Follow-Up

Subject 21 does not introduce a global visual-mode system now. That is a deliberate deferral, not a rejection of future theming or richer visualization settings.

Maintainer intent for the deferred follow-up:
- the repository should eventually have a theme-selection mechanism and broader settings for how information is visualized
- that later work should begin from a clearly faithful SeedSync baseline rather than from a partially drifted default
- fork flavor should be integrated as coherent user-intent settings, not as raw fork personalities
- future implementation should preserve original SeedSync as the default and expose flavor as optional configuration

Recommended future task shape:
- create a dedicated follow-up subject for global theming and visualization settings
- compare candidate systems against original SeedSync first, not against accumulated integration drift
- start by defining the product model for visualization settings: what belongs in theme, what belongs in density/detail/display modes, and what should remain fixed
- prefer one coherent settings surface over many tiny toggles
- name options by user intent, not by fork source

Fork material to revisit in that future task:
- rapidcopy light/dark theme system: `58ead058`, `fb4e7db4`
  - adds a global `ThemeService`, persists a `theme-preference`, follows system color preference on first load, applies `data-theme` on the document root, and exposes a sidebar light/dark toggle
- rapidcopy theme infrastructure and UI follow-ons: `ad35d2e5`, `7e4157ba`, `ce373a2b`, `528e845b`, `facb52b4`
  - builds out the broader reusable theme lane around `ThemeService`, including first-load FOUC prevention, registration/bootstrap wiring, and focused Settings-page toggle coverage
- thejuran earlier dark-mode styling lane: `9a32aa9f`, `88d6258e`, `3f1d7d2e`, `31e2aae5`
  - captures the pre-terminal overhaul theme-aware CSS-variable migration, dropdown dark-mode cleanup, and later action-bar/sidebar dark-mode polish that could inform a less opinionated optional theme system
- thejuran terminal presentation suite: `ef728cc2`, `6865ea03`, `29e7d5d0`, `42d75b03`, `0bdeef59`, `b7fdff1a`, `d8570982`, `12a05c86`
  - adds a more opinionated terminal-style visual language across shell, dashboard, settings, logs, and about page
- thejuran terminal-overhaul roadmap envelope: `3d2a3a2e` through `cf0a5fcb`
  - marks the bounded milestone range for the full v3.0 Terminal UI Overhaul, including the later phase docs and polish commits that were intentionally skipped as a coherent flavor lane rather than forgotten piecemeal
- thejuran shell flavor details: `f64325e3`, `9dd1d796`, `a32dfad7`, `70153570`
  - adds icon-rail/sidebar shell presentation changes that should only be reconsidered inside a coherent visual-mode project
