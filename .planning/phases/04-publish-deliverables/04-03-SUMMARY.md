---
phase: 04-publish-deliverables
plan: 03
subsystem: deliverables
tags: [evidence, publish, archive, submission]
requires: [04-02]
key-files:
  created: [docs/DATA_ACCESS.md]
  modified: [README.md, SUBMISSION.md, tests/test_docs.py]
key-decisions:
  - "Gate passed with an honest blocked history: 20 consecutive hourly scheduler runs on the host, all blocked (403 wbaas), 0 observations, every integrity counter 0"
  - "The employer confirmed that product data must come from Wildberries search and accepted the current result for review, asking for a short note on data access; that note is docs/DATA_ACCESS.md"
  - "Final approval: edit — the user asked for a UI redesign first (quick task 261017-niw), then approved the texts and the time figure ≈ 8 ч"
requirements-completed: [DOC-01, DOC-02, DOC-03]
duration: 2026-10-17 10:30–20:05 МСК (with the quick task)
completed: 2026-10-17
---

# Phase 4 Plan 03: Live evidence, final texts, publish, archive and message — Summary

**The repository is published with the live link (https://194-226-98-141.sslip.io), the verbatim host probe, real run statistics, the data-access note and the confirmed time figure. The source archive and the ready-to-send message sit next to the assignment.**

## Task 1: live evidence (commit 27b1e8b)

- The gate held: the read-only host report showed a scheduler streak of 11, then 18 and finally 20 (19:56 МСК). Every run was `blocked`, there were 0 observations, and all four integrity counters were 0.
- README:
  - the «Работающий интерфейс» line now holds the live URL;
  - «С сервера развёртывания» has the host probe JSON verbatim and a run-statistics table, refreshed to 20 runs at publish time;
  - «Ограничения» gained the host result.
- SUBMISSION: the live URL and one line with the outcome. tests/test_docs.py guards against a leftover marker and a missing live link.

## Task 2: user decision

- **2026-10-17 morning.** The user asked the employer whether product data must come from WB search. The employer said yes, and also said they would review the current result with a short note on the data-access methods tried.
- **Request for a redesign.** The user then asked for a UI redesign with a demo mode before sending (quick task 261017-niw, deployed to the web service only).
- **Final approval** (~19:56 МСК): the texts as shown and «около 8 часов».

## Task 3: publish, archive, message

- The time figure in SUBMISSION.md is ≈ 8 ч: 5 h development, 1 h deployment and access checks, 2 h new UI and demo mode.
- `main` is pushed to the existing public origin U-Steph/wb-serp-monitor without a force-push. Before the push the history secret scan was clean and no SSH target appears in tracked files.
- `../wb-serp-monitor.zip` is a `git archive` of the final HEAD. `../ОТВЕТ_РАБОТОДАТЕЛЮ.md` holds the approved Telegram text.

## Deviations

- The plan expected real product data. WB blocks plain clients from every network tried, so the evidence is the honest blocked history plus `docs/DATA_ACCESS.md`, and the UI offers a clearly labelled demo mode on a separate synthetic database (quick task 261017-niw).

## Self-Check: PASSED
