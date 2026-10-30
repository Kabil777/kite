# Rust ranking plugin provenance

This is **Flea matching + KITE ranking**, not a port of oma-cast's JavaScript ranking policy. The user explicitly chose this alternative after the JavaScript license check. No JavaScript source is included or needed at runtime, build time or in this plugin's checks.

## Vendored matcher

Source: <https://github.com/typecraft-dev/oma-cast>, commit `c12be627c166c06980c195b69b1de1031a416215`, under `search/src/backend/`.

That package declares `license = "MIT"` in its Cargo manifest. Its matcher originated in <https://github.com/thisisgm/flea>, base commit `7c866397b`, plus the upstream pre-release changes described in the preserved [Flea provenance](FLEA-PROVENANCE.md).

All five files below are copied **unchanged** from that oma-cast snapshot. `searchmatch.rs` already has the upstream adapter's removal of its unavailable test-module reference; KITE makes no further changes.

| File | SHA-256 |
| --- | --- |
| `src/backend/mod.rs` | `18c8859aca943b092bc1c6826ddd1ff56941b3018526cc1aa1739154e109544e` |
| `src/backend/searchascii.rs` | `a923431abd51f8ed93ad463a895fe1db518771b07b1b196cc6fcfb7b987b8c4c` |
| `src/backend/searchbits.rs` | `b7842ff21afbe14065556b82760b4c4185798cb1c8d41e01ed4f7d4896c2b027` |
| `src/backend/searchmatch.rs` | `6d27a8a03a307725776e852adc25aef47a7055242ac0295d62a83c0b19e34c82` |
| `src/backend/searchtypo.rs` | `9edefcf793c1f02e6513c776e59103d4f84e41e6c46d198c591395a5b781470e` |

The original MIT notice, copyright (c) 2026 GM, is retained in [LICENSE-FLEA](LICENSE-FLEA), SHA-256 `d2a2191fc6fa3fb43cd6dfe0a7b156ded8f4a5e23c498ef1111788c37ec4f0c4`. Preserve it and these provenance files in distributed packages. `FLEA-PROVENANCE.md` describes the upstream helper's original protocol, not KITE's adapter protocol.

## KITE adapter and policy

`src/main.rs` implements KITE's existing bounded JSON-RPC worker contract. `src/policy.rs` adapts KITE's own `main/ranking.py` ordering/history semantics to the licensed native scores, while preserving the original Python policy as the default/fallback.

Matcher scores and thresholding follow the licensed matcher: exact, prefix, word, subsequence and listed typo tiers; shadow typos are omitted. Low/medium share native thresholds; high excludes typo-tier scores. KITE's aliases, learned terms, 17-day expiry, 10-day frecency half-life, kind priorities, normalized alphabetical/ID tie breaks, and keyword-admission-only policy remain. Empty-query favorites are pinned in configured order, as KITE's default frontend does. Native matching is intentionally not score-for-score equivalent to Python fuzzy matching.

No `Ranker.js` code is copied or translated. The inspected snapshot has no root license covering that JavaScript; GitHub's repository license endpoint also returned HTTP 404 during inspection. This is a scope boundary, not an assertion about permissions that a maintainer may grant separately. The new adapter/policy is part of KITE's source; the vendored MIT grant applies to the matcher, not automatically to the rest of KITE.

Third-party Rust dependencies (`serde`, `serde_json`, `unicode-normalization` and transitive dependencies) are resolved in `Cargo.lock` and retain their own licenses. `unicode-normalization` provides Unicode NFD/combining-class handling that Rust's standard library does not supply; it is needed for KITE-compatible accent folding.
