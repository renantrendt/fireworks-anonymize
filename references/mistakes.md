# Mistakes from the first build

Each mistake caused a redo. The script `scripts/anonymize.py` is written so these do not happen. Do not "fix" a `CLEAN` file by repeating them.

## 1. The model was asked to rewrite the file

Ollama (`qwen3.6:35b-mlx`) lists people, companies, and CNPJ (the Brazilian company tax number). It does not write the PDF or DOCX. An early run used the model as the only writer. It was slow, mixed its reasoning into the answer, and missed names stored in the file author and in comment authors.

Rule: the local model only returns the list. `scripts/anonymize.py` is the only writer. If that list has no people, no companies, and no CNPJ, do not say the file is safe.

## 2. One long fake was written into every fragment of a name

PyMuPDF `search_for` returns several boxes for one phrase, and it ignores case. Writing the full fake name into each box stacked the words on top of each other ("MARIANA COSTA RIBEIRO" three times in one line).

Rule: replace one word box, once. Longest secret first. Skip a box that already overlaps a replaced box. Match case by comparing the word text, not `search_for` alone.

## 3. A longer name at the same size runs into the next word

A PDF does not wrap. An all-caps name fills its line. A longer fake at size 11 collides with the next words. Shrinking the font made the name look like a different size.

Rule: pick the longest fake that fits the narrowest box of that word at the original size. A short fake leaves a gap. The gap is correct. Shrink the size only for an ID whose digit shape is slightly wider than the source font.

## 4. Helvetica is wider than the file's Calibri, and the embedded Calibri is missing letters

The PDF uses Calibri at size 11.04. That font file inside the PDF does not contain every letter. New text came out as black boxes. Helvetica has the letters, and it is wider, so a same-length word does not fit.

Rule: write with Helvetica at the original size, and choose a shorter word that fits. Do not reuse the embedded subset font.

## 5. Replacing `BRASIL` also replaced the country

`search_for` is case-insensitive, so the company token `BRASIL` also hit `Brasil` in "República Federativa do Brasil".

Rule: keep country and city words (`Brasil`, `São Carlos`). Match the company token by exact case. Words in the keep list in the script stay.

## 6. A comma glued to a word hid the last name

The extractor returns `NAME,` not `NAME`. An exact match missed it, so the real last name stayed.

Rule: strip trailing punctuation when deciding what the word is. The box that gets cleared is still the whole word.

## 7. The day `01` was treated as a house number

`01 de maio` is a date. `nº 01` is a house number. A global replace of `01` would change every date.

Rule: replace a number only when the previous word on that line is `nº`, `No`, or `n`.

## 8. Secrets were buried inside a longer word

The certificate stores `Email:name@domain.com.` as one word, and `(SHA256):` plus the hash as one word. An exact word match did not see the email or the hash, so they survived a "clean" text check that only looked for whole words.

Rule: if the secret string occurs inside a word, replace that whole word. Check emails, hashes, and ids as substrings when verifying.

## 9. The street name was on the next line after "Avenida"

The extractor put `Avenida` and the street word on different lines. A same-line rule missed the street.

Rule: look at the next few words after `Rua` or `Avenida` in reading order, not only on the same extractor line.

## 10. A white box did not hide handwriting or the QR code

Signature names and the QR code are pictures. Painting white happens underneath the picture, so the handwriting stayed. The QR still pointed at the real signed file.

Rule: delete the picture (`apply_redactions(images=1)`). Use a small sliver that does not cover the date line, so the date text stays. Do not delete logos in the top band or the green check marks on the left.

## 11. Covering the stamp also ate the date

The stamp picture's box overlaps "São Carlos/SP, 09 de setembro de 2026". A redaction rectangle on the whole stamp deleted the date.

Rule: the sliver is the bottom 2 points of the picture. The date sits above that. `text=0` is not used on a rectangle that touches the date.

## 12. A second pass broke the signature line into "ira Lima"

A later pass wrote one leftover syllable and the last name into the wrong boxes.

Rule: one pass. If the script prints `CLEAN`, stop. Do not run another replace on the copy.

## 13. Skipping a too-wide fake left the real value in the file

The document code and the hashes were left unchanged because the fake did not fit the box. The copy still identified the signed original.

Rule: never skip. Use a shorter fake that fits (8 hex characters for a document code, a short run of zeros for a hash).

## 14. Comment authors and the file creator are outside the body

The Word file stored a lawyer name in `docProps/core.xml` (`dc:creator`) and in `word/comments.xml` (`w:author`). A body-text replace did not touch them.

Rule: read those two XML parts, treat each name word as a secret, then replace inside every `word/*.xml` and `docProps/*.xml` string.

## 15. Saying done after a text search

A text search said the file was clean while the stamp pictures still showed real emails and handwriting.

Rule: the script's `CLEAN` line is the check for text. Picture removal is part of the same run, before that check. A person who can see the page should look at page 1, the signature page, and the certificate page once. The model does not get a second editing pass from that look.

## 16. The command output sent real names to the cloud

The script printed every real value in `MAP` and `LEAKS` lines. The skill said to leave them in the local terminal. When Cursor or Claude Code runs the command, its output is part of the agent's input, so the names went to that agent's provider. The build session also opened the original contract and looked at rendered pages of it.

Rule: output shows a label, a keyed 4-character hash, and the fake. The map goes to an owner-only file outside the project. `--reveal` exists for a person at a local terminal. A cloud agent improves the script against the fictional files in `tests/` and sees only counts and labels from real files.

## 17. Every person got the same fake in a DOCX

`fit_choice` seeded the pick by the option list and the box width, not by the real word. In a DOCX every box has the same placeholder width, so every first name became the same fake, and every company the same company. The copy no longer showed who did what.

Rule: seed the pick by the real word with a local secret. Track fakes already used in the file. Prefer a fake with the same first letter (the rule from `app/src/lib/pii/twin.mjs` in the monorepo).

## 18. A logo showed the company name after the text was clean

The DOCX had a picture with the company name in it. The text check printed clean. OCR of the copy's pictures found it.

Rule: OCR every DOCX picture. A picture that shows a real value is replaced by a white picture of the same pixel size. OCR every PDF page and report `OCR_LEAKS` by page.

## 19. A forwarded email was dropped without an error

Python's `email` library reports a forwarded email (`message/rfc822`) as multipart. A loop that skipped multipart parts skipped the whole forwarded email, and the leak check passed because the text was gone.

Rule: check for `message/rfc822` before checking `is_multipart()`, and keep a test that the forwarded email is still in the copy.

## 20. Header words and the placeholder domain were taken for secrets

`From` sat next to a real name, so the name-pair rule took it for a first name. The domain label of `juridico@example.org` was mapped to `example`, the same word, and the leak check flagged it.

Rule: header words and greetings are in `KEEP`. Domain labels that start with `example` are skipped.

## 21. The fake did not look like the text it replaced

The PDF writer drew every fake in black regular Helvetica. A red bold heading "<company> BRAZIL" came out as a black fake next to a red "BRAZIL". The Word writer joined a paragraph's text into its first run, so the words after a name took the name's format.

Rule: the PDF fake takes the color and weight of the span under its box. In Word, `replace_in_pieces` puts each fake in the run where its name starts and leaves every other run's text in place. The `LOOK` check compares the copy with the original.

## 22. One fake for several people, and one person with several fakes

The pools held 46 first names and 30 company words. A 26-file case had about 100 people and 90 companies, so fakes were reused: one fake stood for up to 5 real words. Reuse was also case-sensitive, so "OBSIDIANA" and "Obsidiana" went to two companies. A family whose capitals form did not fit a PDF box got a second fake.

Rule: pools of 160, 129, 98, and 22 words with no word in two pools; reuse is checked case-blind; when every option is used, a new word is blended from two options; a family always shares one fake. `check_map` counts what is left, and a copy holding such a name ends in `REVIEW`.

## 23. A company read as a person

The capitals rule calls a capitalized word a person. The counterparty company in capitals became "Irene". A word GLiNER read only inside a company name is now a company word, and a word found both as a person and as a company takes a company fake.

## What the script keeps

Law words, role words (`EMPREGADORA`, `CLT`), the country, the city, and dates. The keep list is the `KEEP` set in `scripts/anonymize.py`.
