# Frozen US/HK trading sessions

Generated with `scripts/ml_experiments/freeze_calendar.py` (PMC 5.1.3 / exchange-calendars 4.11.1), covering 2020–2026. HK closing auction and final-data buffers are described in `sessions.py`.

Version `pmc-5.1.3-xhkg-4.11.1-2020-2026-cas-weather-v2` removes two confirmed full-day closures that the XHKG schedule included:

- 2023-09-01: [HKEX typhoon closure notice](https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en).
- 2023-09-08: [HKEX black rainstorm / extreme conditions closure notice](https://www.hkex.com.hk/news/market-communications/2023/2309083news?sc_lang=en).

Both source notices confirm cancellation of all securities and derivatives sessions. The generator applies the same exclusions so regeneration preserves the correction. This is a targeted correction discovered during historical data validation, not a complete certification of all historical weather events. Missing quote rows alone must never be used to infer a holiday or intraday halt.
