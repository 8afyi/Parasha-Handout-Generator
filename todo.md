# TODO

## Decisions
* Translation/language-mode options should be implemented in the generator layer and exposed through both the web UI and CLI.
* Language mode should be a first-class generator option with exactly three values for now: `bilingual`, `english`, and `hebrew`; `bilingual` remains the default.
* Single-language modes should render as one full-width text column instead of a two-column table with one side empty.
* Text fetching should request only the languages needed for the selected language mode; `bilingual` fetches both, `english` fetches English only, and `hebrew` fetches Hebrew only.
* The default English translation should remain `The Koren Jerusalem Bible` whenever English is included.
* The web English translation picker should use a curated local list rather than dynamically querying Sefaria for every available version at request time.
* The curated English translation picker should include only versions that cover both Torah and haftarah readings; avoid Chumash-only versions in the first pass.
* The curated English translation options should be shown to end users in this order: `Koren`, `JPS 2023`, `JPS 1985`, `JPS 1917`.
  * `Koren` maps to Sefaria version title `The Koren Jerusalem Bible`.
* `JPS 2023` maps to Sefaria version title `THE JPS TANAKH: Gender-Sensitive Edition`.
* `JPS 1985` maps to Sefaria version title `Tanakh: The Holy Scriptures, published by JPS`.
* `JPS 1917` maps to Sefaria version title `The Holy Scriptures: A New Translation (JPS 1917)`.
* In `hebrew` mode, the English translation picker should be hidden/disabled in the web UI and the generator should ignore the English translation.
* Add a Hebrew text picker with these user-facing options: `Tanach with Nikkud`, `Tanach with Ta'amei Hamikra`, and `Tanach with Text Only`.
  * These labels match exact Sefaria version titles and were verified for Genesis and Isaiah.
* In `english` mode, the Hebrew picker should be hidden/disabled in the web UI and the generator should ignore the Hebrew version.
* The default Hebrew version should remain `Tanach with Nikkud` whenever Hebrew is included.
* Generated filenames should include selected language mode and non-default version choices compactly, while preserving the current filename for default bilingual Koren/Nikkud output.
* Stable internal slugs should be `koren`, `jps-2023`, `jps-1985`, `jps-1917`, `nikkud`, `taamim`, and `text-only`; use them for form values, CLI option values, cache metadata, and filename suffixes.
* The Rashi option should add Rashi commentary for Torah readings only, not for haftarah readings.
* Rashi language should be controlled by the sheet language mode rather than by a separate Rashi-language picker.
  * Exception: when the sheet language mode is `bilingual`, users should be able to choose whether Rashi is `hebrew`, `english`, or `bilingual`.
  * In bilingual sheet mode, the default Rashi language should be `hebrew`.
* Rashi should render at the bottom of each Torah aliyah, grouped by aliyah, not interleaved verse-by-verse.
* Within each aliyah Rashi block, group comments compactly and densely by verse number, with all comments for that verse together.
* Rashi should use one fixed Sefaria edition with no separate Rashi edition picker: `Pentateuch with Rashi's commentary by M. Rosenbaum and A.M. Silbermann, 1929-1934`.
* Rashi should be included for Maftir when Maftir is a Torah reading.
* Generated filenames should include a compact Rashi suffix when Rashi is enabled, such as `_rashi-hebrew`, `_rashi-english`, or `_rashi-bilingual`.
* The generated document's Sources line should list only sources actually used for that sheet, including selected English/Hebrew versions and the Rashi edition when enabled.
* Every Sefaria text request, including Rashi requests, should include `return_format=text_only`; keep the existing HTML-stripping fallback for cached old responses and unexpected API output.
* File expiration should be implemented inside the web server by deleting generated `.odt` and `.pdf` files older than one hour in `PARASHA_OUTPUT_DIR` on startup and around generation, instead of relying only on cron.
  * Retention should be configurable with `PARASHA_OUTPUT_RETENTION_SECONDS`, defaulting to `3600`.
  * Cleanup should leave `.cache/parasha_generator/` and other source caches untouched.
* The web form should stay on one page with conditional enable/disable behavior for irrelevant options.
* After validation errors or generation failures, the web form should preserve the user's submitted date/options and show an error message.
* The CLI should expose the same feature set with explicit options: `--language-mode`, `--english-version`, `--hebrew-version`, `--rashi`, and `--rashi-language`.
  * CLI choices should use the stable internal slugs.
  * CLI defaults should preserve current behavior: `--language-mode bilingual`, `--english-version koren`, `--hebrew-version nikkud`, no Rashi.
  * For single-language sheet modes, CLI should ignore the irrelevant text-version option.
  * `--rashi-language` should only matter when Rashi is enabled; in single-language sheet modes, Rashi follows the sheet language.
  * In bilingual sheet mode with Rashi enabled, `--rashi-language` should default to `hebrew`.
  * Invalid slug values should fail fast with argparse choices.
* The web result page's Sources blurb should be dynamic and match the generated document's actual sources.
* If a selected Sefaria version returns missing text for a reading, generation should fail with a clear error instead of silently falling back to another version.
* Bilingual rendering can assume English/Hebrew verse counts match; do not build fallback/reconciliation behavior for mismatched counts.
* The generated document should not include a settings summary near the title; selected versions belong in the Sources section.
* The web form should show short option labels only, without explanatory source/license text for each translation.
* Single-language document mode should still use a one-column bordered table/cell layout, preserving current visual structure while using the full page width.
* Each aliyah's Rashi block should be a separate compact table immediately after that aliyah's main text table, with a small Rashi heading only when Rashi exists for that aliyah.
* Bilingual Rashi blocks should use a two-column table with Hebrew and English columns, matching the base bilingual layout.
* Bilingual output should keep the existing column order: Hebrew column first, English column second; use the same order for bilingual Rashi.
* Rashi should be off by default in both the web UI and CLI.
* The web UI should avoid persistent browser storage for now; options reset to defaults on a fresh visit and are only preserved within submitted form/result/error flows.
* Download URLs should remain simple `/download/<filename>` links, with one-hour cleanup as the privacy/lifetime control.
* Hebrew divine-name replacement should apply consistently to all Hebrew output, including base Tanakh text and Hebrew Rashi text.
* Divine-name replacement should be exposed as a web checkbox and CLI option, not only as an environment variable.
  * It should default to enabled in both GUI and CLI, preserving current behavior.
  * CLI should support `--replace-divine-names` and `--no-replace-divine-names`.
  * In English-only output with no Hebrew Rashi, hide/disable the web checkbox and ignore the generator option because there is no Hebrew text to transform.
  * Divine-name replacement state should not affect generated filenames.
  * Sources should not mention whether divine names were replaced.
* Do not add automated tests for this pass; rely on focused manual verification instead.
* Update `readme.md` with the new web/CLI options and cleanup behavior.
  * Document `PARASHA_OUTPUT_RETENTION_SECONDS` in the README environment variable list, but leave it implicit in service examples.
* Do not add a cron job or systemd timer for cleanup in the Debian installer; rely on app-level cleanup.
* Web front-end overhaul should include basic built-in CSS using element-level selectors and no CSS classes.
* Conditional web UI behavior should use a small dependency-free inline JavaScript snippet to toggle `hidden`/`disabled` on irrelevant controls.
* Server-side form parsing should accept and correctly ignore irrelevant fields even when JavaScript is disabled or a crafted request sends them.

## Generator
* Per Sefaria API, set "return_format" to "text_only".
* Have the generated file delete after one hour. I'm OK with this being a cron job.

## Web front-end overhaul
* Allow users to pick the English translation.
* Allow users to pick English only or Hebrew only.
* Allow users to add RASHI.
