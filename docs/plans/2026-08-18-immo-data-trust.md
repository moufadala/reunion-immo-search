# Immo Data Trust Implementation Plan

> For Hermes: execute task by task with TDD. Do not declare the product reliable before the two-run acceptance gate.

Goal: Make every acquisition, transformation, exclusion, deduplication and publication measurable so no silent data loss or duplicate media can reach the public page.

Architecture: Keep the existing Python, SQLite, React and Playwright stack. Add small shared validators and one reconciliation artifact per run. Do not add n8n or another framework.

## Verified baseline — 2026-08-18

- 181 eligible identities become 167 visible cards plus 14 hidden duplicate identities; all 181 remain represented.
- 11 visible listings contain duplicate image bytes under different filenames.
- 1 visible listing has no photo.
- 7 visible descriptions are shorter than 80 characters: 5 Adrezio and 2 Zimo.
- Leboncoin coverage is 35 of 268 active rows, or 13 percent.
- OFIM RSS coverage is 0 of 22 and stale for about 324 hours.
- At least one explicit Citya and Bienici reference duplicate remains visible.
- The run can exit zero despite the two high source-health defects.

## Global acceptance contract

The project is trusted only when:

1. Every run counts source output, normalized rows, database writes, active rows, eligible rows, dedup members and visible cards.
2. Every reduction has a named reason and arithmetic reconciles exactly.
3. Every eligible identity is a visible card or an also_on member.
4. Public galleries contain zero repeated URL and zero repeated content hash within one listing.
5. Required public fields have zero unexplained missing values.
6. Critical sources cannot silently publish a partial successful run.
7. Withdrawal and reappearance depend only on successful source observations.
8. Browser QA exercises cards, both gallery directions, details, sources and movements.
9. Two consecutive scheduled runs pass without event replay or unexplained churn.
10. An injected source failure alerts and causes zero false withdrawal.

## Task 1 — Canonical gallery integrity

Files:
- Create src/photo_gallery.py
- Create tests/test_photo_gallery.py
- Modify scripts/export_feed.py
- Modify scripts/postflight_public_contract.py
- Create tests/test_public_photo_integrity.py

Steps:
1. Write a failing test with identical bytes under two filenames.
2. Add a hard negative with different bytes.
3. Implement stable first-wins content-hash deduplication.
4. Apply it to manifest and legacy fallback galleries.
5. Make postflight block duplicate URL or duplicate SHA-256 content.
6. Run against current real feed: baseline 11 failures, target zero.
7. Do not delete cached photo files.

Done proof: duplicate_url_listings=0, duplicate_content_listings=0, missing_photo_files=0.

## Task 2 — Conservative listing deduplication

Files:
- Modify src/public_feed_dedup.py
- Modify tests/test_public_feed_dedup_business_reference.py
- Create tests/test_public_feed_dedup_citya_bienici_golden.py
- Modify scripts/postflight_public_contract.py

Steps:
1. Add real GES10980017-495 and GES10220016-542 mirror tests.
2. Add hard negatives for different floor, address and reference.
3. Extract stable agency tokens from IDs and URLs.
4. Reuse hard contradictions before merging.
5. Inspect every new group on the real feed.
6. Never auto-hide on metrics alone.

Done proof: known mirrors collapse, hard negatives stay separate, represented identities still equal input.

## Task 3 — End-to-end reconciliation

Files:
- Create src/pipeline_reconciliation.py
- Create scripts/audit_pipeline_reconciliation.py
- Create tests/test_pipeline_reconciliation.py
- Modify scripts/immo_daily_public_refresh.sh
- Produce pipeline_reconciliation.json per run

Counters:
- Per source: attempted, fetched, parsed, rejected, normalized, inserted and updated.
- Database: total, active, inactive, new, disappeared and reappeared.
- Product: active input, policy exclusions, eligible, dedup hidden and visible.
- Fields: missing title, rent, surface, commune, description and photo.
- Identity equation: eligible identities equal visible IDs union also_on IDs.

Gate: fail on any unexplained delta, duplicate ID, mismatch or missing mandatory counter.

## Task 4 — Source acquisition truth

Files:
- Modify src/source_health.py
- Modify scripts/realestate_multi_sources_scraper.py
- Modify source adapter result contracts
- Modify tests/test_source_health.py
- Create tests/test_source_run_completeness.py

Each source manifest records run ID, full/partial/failed status, pages, raw items, unique IDs, expected count when exposed, previous-run comparison, actor dataset ID and truncation/retry signals.

Rules:
- Failed or partial sources never cause withdrawals.
- Critical sources below calibrated floor block publication.
- First noncritical partial run warns; the second consecutive one escalates.
- Leboncoin stays not proven complete until the 13 percent defect is resolved.
- OFIM RSS is repaired or formally deprecated as an OFIM duplicate.

## Task 5 — Description and field fidelity

Files:
- Modify src/description_observability.py
- Modify enrichment scripts
- Modify scripts/export_feed.py
- Create tests/test_public_field_fidelity.py

Rules:
- Store raw and extracted length/hash plus attempt/success timestamps.
- HTTP 200 with empty or truncated text is not complete.
- Preserve an older complete value when a new scrape is empty.
- Distinguish source, stale and synthetic text.
- Retry sparse Adrezio and Zimo only when fuller source text exists.

Done proof: zero empty public descriptions and every short description has an explicit evidence state.

## Task 6 — Lifecycle correctness

Files:
- Modify src/listing_lifecycle.py
- Modify src/listing_history.py
- Modify lifecycle and movement tests.

Cases:
- Two successful source absences before withdrawal unless explicit deletion exists.
- Failed or partial source has no withdrawal effect.
- Product filters, missing photos and dedup hiding have no lifecycle effect.
- Run replay is idempotent.
- Reappearance emits one event.
- Price changes remain tracked.

## Task 7 — Product and browser QA

Files:
- Modify tests/audit_user_search_cases.py
- Modify tests/audit_changes_page_filters.py
- Create tests/audit_public_data_integrity.py

Scenarios:
- Card opens details.
- Next and previous arrows change unique content.
- Gallery arrows never open details.
- Source warnings expose Leboncoin and OFIM state.
- Movements match event windows.
- Browser has zero page, console and network errors.

## Task 8 — Final scheduled-run acceptance

Run A:
- All blocking gates pass.
- Reconciliation equations pass.
- No duplicate photo content.
- Source manifests exist.
- Public browser QA passes.

Run B:
- Same gates pass.
- No replayed event IDs.
- Unchanged listings generate no lifecycle event.
- Unexplained visible-count delta is zero.
- Source coverage stays within bounds.

Failure injection:
- Force one adapter into a controlled failed state.
- Verify alert delivery.
- Verify zero false withdrawals.
- Restore and run the targeted smoke test.

Final artifact: artifacts/immo-data-trust/TIMESTAMP/REPORT.md with JSON evidence, commit hashes, run IDs and rollback instructions.

## Order and estimate

1. Photos and explicit-reference dedup: 2 to 4 hours.
2. Reconciliation artifact: 3 to 5 hours.
3. Source completeness contracts: 4 to 8 hours, depending on Leboncoin.
4. Description and lifecycle hardening: 3 to 5 hours.
5. Browser QA and first run: about 1 hour plus the 45-minute run.
6. Second scheduled run: final verdict only after the next schedule window.

## Stop conditions

- Do not tune downstream parsing while a source is unproven partial.
- Do not auto-merge metric-only suspects.
- Do not call the project trusted after one manual run.
- Do not delete the photo cache during gallery cleanup.
- If Leboncoin stays below its calibrated floor, replace or reconfigure the actor before claiming complete coverage.

