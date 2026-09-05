# End-to-end run on a real release — Saju Today 1.1.17 (2026-09-05)

One pass of all four tools over the author's own app, using the inputs a real release
produces, not fixtures. Model: the default free `nvidia/nemotron-3-super-120b-a12b` via
OpenRouter. Times are wall-clock for one `python -m release_keeper …` call.

| Step | Real input | Result | Time |
|---|---|---|---|
| ① `check` | live Google Play listing (en-US, 3836 chars, pulled from the Play Developer API) | `SHIP_WITH_FIXES` — 1 real yellow (no minimum-age line), 5 passed | raw 0.0 s · agent 7.8 s |
| ③ `notes` | `git log --oneline` for the release range, 45 commits | agent rewrote 8 user-facing entries in plain English; store text **622/500 → blocked by the post-check**, trimmed version offered | 49.5 s |
| ④ `listing --lang ko` | app facts distilled from the live listing (the app has **no Korean listing yet**) | brief → model wrote ko → `listing_check` OK, all fields under cap; CLI re-check agrees | 22–71 s |
| ② `refund` | RevenueCat `CANCELLATION`/`CUSTOMER_SUPPORT` payload in the shape verified during the app's IAP test (anonymized) | class `refund`, revoke entitlement, reply drafted, Play-credentials warning | 3.9 s |

## What the real inputs taught the tools (fixed in this run)

1. **A repo that commits per work session has no `feat:`/`fix:` commits.** All 45 commits in
   the range were `chore(session): … [tlog]`. The first run produced an empty note without
   saying why. Now: `[tlog]` is not treated as noise, the warning names `include_internal`, and
   with it typed-internal commits get a keyword pass where an internal word (report, spec,
   design sample, measurement …) wins over a fix word — "report §9 correction" is a report.
2. **The model does not count.** Asked for a store block under 500 characters it produced 622
   with a confident "under 500 chars" label. The CLI and the web page measure the last fenced
   block and fail (exit 1) with a trimmed-to-whole-bullets version. Same pattern as the
   listing re-check: the deterministic check has the last word.
3. **The model copies drafts.** Given `store_whats_new` in the tool output it pasted the raw
   commit subjects into the store text. The agent-facing JSON now calls them
   `draft_markdown` / `draft_store_whats_new` with a note that they are developer jargon, and
   the prompt asks for a rewrite. Second run: user-language entries, self-written store block.
4. **The model does not translate what it can copy.** An English `refund_line` in the facts
   landed verbatim in the Korean listing, twice, even with a translate instruction. Now the
   brief substitutes the language's default refund line and flags it for confirmation, and
   `listing_check` reds any English sentence inside a ko description (`untranslated_line`).
   The default refund lines were also changed to say "follows the store's refund policy" —
   the tool must not invent a 7-day window the developer never promised.
5. **Korean copy needs its own hype vocabulary.** "한 줄 조언" (one-line advice) and
   "정확한 사주 기반" (accurate Saju-based) passed the first check. Added: affirmative `조언`
   outside the disclaimer = yellow softener; `정확한 사주/운세/풀이/해석` = red claim
   (`정확한 생년월일` stays fine).
6. **Renewal wording varies.** The live listing says "renews monthly until you cancel"; the
   vocabulary only knew "renews automatically" and flagged a false yellow. Added the variants.
7. **Sentences repeated twice** (pricing section + disclosure block) → yellow `duplicate_line`.

## What still needs a human

- ① The live listing genuinely lacks a minimum-age line. The app gates at 13 in-app; the
  store copy should say so.
- ④ "BaZi" was transliterated as "바지" (trousers). Vocabulary checks cannot catch this — a
  native read of any model-written listing stays mandatory, ko included.
- ③ The free model still lists 0 items under "Dropped as internal" and echoes every
  unclassified commit. The deterministic filter now does most of the dropping before the
  model sees the list; the remaining judgment is the developer's.
- ② No real refund event exists yet for this app (Play refunds only reach RevenueCat once the
  Play service account is registered in the RC dashboard — the tool's own warning).

## Reproduce

```bash
python -m release_keeper check   examples/saju_play_listing_live.txt --payments --accounts --category fortune --raw
python -m release_keeper notes   examples/saju_1.1.17_gitlog.txt --version 1.1.17 --include-internal
python -m release_keeper listing examples/app_facts_example.json --lang ko --store play
python -m release_keeper refund  examples/rc_refund_subscription.json --products examples/products.json --app-name "Saju Today"
```
