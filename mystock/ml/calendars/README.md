# Frozen US/HK trading sessions

Generated with `scripts/ml_experiments/freeze_calendar.py` (PMC 5.1.3 / exchange-calendars 4.11.1), now covering 2020–2027. HK closing auction and final-data buffers are described in `sessions.py`.

Version `pmc-5.1.3-xhkg-4.11.1-2020-2026-cas-weather-v2` removes two confirmed full-day closures that the XHKG schedule included:

- 2023-09-01: [HKEX typhoon closure notice](https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en).
- 2023-09-08: [HKEX black rainstorm / extreme conditions closure notice](https://www.hkex.com.hk/news/market-communications/2023/2309083news?sc_lang=en).

Both source notices confirm cancellation of all securities and derivatives sessions. The generator applies the same exclusions so regeneration preserves the correction. This is a targeted correction discovered during historical data validation, not a complete certification of all historical weather events. Missing quote rows alone must never be used to infer a holiday or intraday halt.

## 2027 extension (v3)

Version `pmc-5.1.3-xhkg-4.11.1-2020-2027-cas-weather-v3` extends coverage through **2027-12-31**. The 2020–2026 CSV rows remain byte-for-byte unchanged. The 2027 weekday closures and half-days were checked against:

- [NYSE hours and calendars](https://www.nyse.com/trade/hours-calendars): 10 weekday closures; November 26 closes at 13:00 ET. December 31 is a full session.
- [HKEX CT/077/26 (June 1, 2026)](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2026/ce_SEHK_CT_077_2026.pdf): 13 weekday closures; February 5, December 24 and December 31 are half-days.

`test_ml_claude_fixes.py` checks these complete sets. Future emergency closures still require a reviewed calendar update; this extension cannot predict them.

`calendar_days_left()` measures UTC calendar days to the coverage end. Below 60 days, fetch/report log `calendar_expiring` and include warnings in their receipts; after expiry the warning is `calendar_expired`. Data receipts use `<run>.data.json`, separate from publishable training receipts. Queries outside coverage, including a next session beyond the end, fail closed with regeneration instructions.

Regenerate in an isolated environment with the pinned tools above using `python -m scripts.ml_experiments.freeze_calendar --end 2027-12-31`. For a new year, first verify exchange notices, then update the end/version constants and calendar tests together. Runtime Web/ML queries read the CSVs and do not need calendar generator packages. Decision cutoff is 09:30 ET for US and 09:00 Hong Kong time for HK; final-data buffers are 5 and 15 minutes after close respectively.
