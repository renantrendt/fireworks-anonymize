---
name: offline-anonymize
description: >-
  Anonymize a PDF, DOCX, Outlook MSG, or EML on this computer with one Python
  script. Use when the user says anonymize, scrub names, remove PII, or
  offline anonymization of a contract, email, pdf, or docx. The script is
  scripts/anonymize.py. Stack is Python 3, PyMuPDF, python-docx, extract-msg,
  Tesseract OCR, and a local Ollama model that lists people, companies, and
  CNPJ. The output never prints a real value. Do not ask a cloud chat model to
  rewrite or read the file.
---

# Offline anonymize

The person running this has a local model only. Do the job by running the script. Do not invent a second method. Do not edit the PDF or DOCX by hand. Do not send the file to a cloud model.

## Stack

| Piece | Name | Role |
| --- | --- | --- |
| Script | `scripts/anonymize.py` | Finds secrets and writes the copy. This is the anonymizer. |
| Packages | `scripts/requirements.txt` | `pymupdf`, `python-docx`, `extract-msg`. |
| PDF library | PyMuPDF (`import fitz`) | Reads words, writes fakes, removes signature pictures. |
| DOCX library | python-docx | Replaces text in paragraphs, tables, headers, and footers. |
| Email library | extract-msg + Python `email` | Reads `.msg` and `.eml`, including forwarded emails inside them. |
| OCR | Tesseract (`brew install tesseract tesseract-lang`) | Reads pictures and rendered pages, to find real values the text check cannot see. |
| Tests | `tests/test_anonymize.py` | Fictional documents only. Safe to show a cloud agent. |
| Runtime | Python 3 venv at `~/.local/share/offline-anonymize` | Installed once. No network after that. |
| Identifier | Ollama `qwen3.6:35b-mlx` at `http://127.0.0.1:11434` (needs about 29 GB of memory; a 48 GB Mac) | Lists people, companies, and CNPJ. It does not rewrite the file. |
| Identifier on a 16 GB Mac | Ollama `gemma4:12b-it-qat`, set with `OFFLINE_ANON_MODEL` and `OFFLINE_ANON_CTX=16384` (about 7.7 GB) | Same job. In a comparison on one real case folder it left no names the 35B model found, when run in folder mode. |
| Context model | GLiNER `urchade/gliner_multi_pii-v1` (about 1.2 GB download, 4 GB memory, about 4 s per file), installed with the requirements | Reads each word in its sentence. Finds people, companies, street addresses, and phone numbers the local model and the rules miss. A word only the capitals rule flagged is kept as a name only if GLiNER also sees it inside a name. Same answer every run. `OFFLINE_ANON_GLINER=off` turns it off. |

Before it replaces anything, the script sends the text to that local model. The model returns the exact strings. The script then replaces those strings. Ollama must be running on this Mac. The text never goes to a cloud model.

The text check and OCR only look for values the script found. The second local-model look at the copy is the check for values it missed. If the local model returns no people, no companies, and no CNPJ, the script stops with `LOCAL_EMPTY`.

## One command

`SKILL_DIR` is the folder that contains this file.

First time on a machine:

```bash
python3 -m venv "$HOME/.local/share/offline-anonymize"
"$HOME/.local/share/offline-anonymize/bin/pip" install -r "$SKILL_DIR/scripts/requirements.txt"
brew install tesseract tesseract-lang
```

The first run downloads the GLiNER model (about 1.2 GB) from Hugging Face. After that it runs offline.

Every file:

```bash
"$HOME/.local/share/offline-anonymize/bin/python" "$SKILL_DIR/scripts/anonymize.py" "/full/path/to/input.pdf"
```

DOCX, MSG, and EML use the same command.

A whole folder (recommended for a case with several documents):

```bash
"$HOME/.local/share/offline-anonymize/bin/python" "$SKILL_DIR/scripts/anonymize.py" "/full/path/to/case folder"
```

Folder mode reads every PDF, DOCX, MSG, and EML in the folder (not subfolders) first, builds one map, then writes each copy. The same person gets the same fake in every copy, and a name found in one file is replaced in all of them. A file the script cannot read is skipped and has no copy. A file where the local model lists nothing still gets a copy, ending in `REVIEW`.

At the end, the originals move into `originals/`. Copies that came out `CLEAN` move with their reports into `anonymized/`; every other copy (`REVIEW`, `LEAKS`, `OCR_LEAKS`, `ERROR`) goes into `needs-review/`. Share only `anonymized/`. Only the copies this run wrote are moved; an old `ANON-` file stays where it is. Running the folder again reads from `originals/`; the earlier `anonymized/` is kept as `anonymized-previous-<date-time>`, never overwritten. Other files in the folder stay where they are.

Values no detector finds (a foreign address, a phone in a foreign format, a project code name) go in a file named `_extra_terms.txt` in the case folder, one per line. The script replaces them in every file. That file holds real values: it is never processed and must not be shared.

Words that were replaced but are not names (an opening formula such as "SAIBAM", a registry, a country inside a company name) go in `_keep_terms.txt` in the case folder, one per line. They stay as they are in every copy, also inside longer names.

A rerun of the same folder keeps the earlier run's decisions. Each person or company keeps the fake the reader already saw. Only the fakes that fail today's map checks change: when one fake stood for several people, the person with the most forms keeps it and the others get new fakes. The decisions live in `~/.local/share/offline-anonymize/cases/`, readable only by this user, because they hold real values. The run prints `MEMORY reused N fake(s) from earlier runs; M new or changed`.

On a 16 GB Mac, put this before the command: `OFFLINE_ANON_MODEL=gemma4:12b-it-qat OFFLINE_ANON_CTX=16384` (pull the model once with `ollama pull gemma4:12b-it-qat`).

The script writes a new file next to the input. The name is `ANON-` plus the input's name, with the same replacements as the content: `Contrato Maria Souza.pdf` becomes `ANON-Contrato Elena Duarte.pdf`. The leak check reads this name too. PDF and DOCX keep their extension. MSG and EML become `.eml`, because a `.msg` file cannot be written back. It never overwrites the input. Running it again on the same input replaces the earlier `ANON-` copy.

An email copy keeps From, To, Cc, Subject, Date, the plain-text body, forwarded emails, and the attachments the script can clean or check. HTML and links are removed. Attachments (added 2026-10-31; before that every attachment was dropped and a case lost its drafts):

- A PDF or Word attachment is read like a file of its own (its names join the shared map, and a name found only in a draft is replaced everywhere), cleaned with the same writer, and attached again under a cleaned name.
- A picture is read with OCR. It is dropped when a real value is seen in it, or when it is inline and shows fewer than 20 letters (a logo, an icon, a signature banner). A screenshot or scan with clean text stays, inline or not.
- Any other type (a spreadsheet, a zip) is dropped, and the body says so with the reason.
- An attached PDF with no text layer (a scan) sends the copy to `REVIEW`: the detectors cannot read it, and OCR only looks for values already found.

Attachments are written to a private folder under `~/.local/share/offline-anonymize/attachments/` while the run lasts, and that folder is removed when the run ends. The output line `NOTE kept N attachment(s)` counts what came back; the report holds `attachments_kept` and `attachments_removed`.

## How to read the result

The output never contains a real value. Each value is a label and a 4-character hash, such as `PERSON_3 (a1f9) -> Elena`. The same real value gets the same hash and the same fake on this computer, so two runs can be compared.

- `MAP` lines: one per value found, as label, hash, and fake.
- `GROUP PERSON_3: Joana | JOANA` lines: each real person or company that appears in more than one form, with every fake it takes. Fakes only. Use them to answer "is Eva the same person as Joana?" without opening the map.
- `MAP_CHECK fake_has_real 0, shared_fake 0, two_fakes 0, company_form 0` counts four map mistakes found by hand in a real case: a fake that contains a real name ("Helena" for "Helen"); one fake for two people or companies ("Irene" for two people); one person with two fakes ("Karen" and "Nina"); a company form that lost a kept word ("Alfa Norte" for "Alfa Brasil"). Each should be 0. A copy that holds one of these names ends in `REVIEW`, with a `MAP_CHECK` line per label.
- `OUTPUT copy 1a2b3c4d .pdf` means the copy was written next to the input as `ANON-<input name>`. The script prints an id, never a file name: a name the detectors missed could still be in it, and a folder path often holds a person's name.
- `MAPFILE` names the real-to-fake map. It lives in `~/.local/share/offline-anonymize/maps/`, readable only by this user. Do not open it, print it, or copy it. Only the person who owns the document may open it.
- `REPORT copy 1a2b3c4d` means the report `ANON-<input name>.<ext>.report.json` sits next to the copy, with the same id inside. It records the script version, the date, counts per kind of value replaced, pictures blanked, attachments kept and removed, and the result of each check. It holds no real value, no hash, and no file name, so it can go with the copy to a buyer or to whoever signs a de-identification letter. It says `pseudonymized`: while the map file exists, the owner can reverse the replacement, so under LGPD (Lei 13.709/2018, arts. 12 and 13) the copy is not anonymized data. The report also states what the script does not change (job title with city, exact dates, salary, unique facts).
- `ERROR ...` means the run stopped. If the copy could not be written, no copy is kept. If a check crashed, the report says `"result": "ERROR"`; do not use that copy. A report that still says `INCOMPLETE` means the run was killed before it finished.
- `NOTE blanked picture ...` means a DOCX picture (often a logo) showed a real value and was replaced by a white picture of the same size.
- `LOOK n` means n lines or paragraphs of the copy lost the color or weight of the original (a black fake in a red heading). The copy is safe; open it and compare before sending.
- `MAP_SYNC n` means n names in the copy do not show the fake the map records, usually after a hand edit. Fix the map or the copy so the owner's key still works.
- `CLEAN` means three checks passed: the text check, OCR of every page and picture, and a second local-model look at the copy. Stop.
- `CLEAN_TEXT_ONLY` means the text check passed but OCR or the second look could not run. Say which one, and do not call the file safe.
- `LEAKS` or `OCR_LEAKS` followed by labels means stop. Show the labels. Do not patch the file yourself.
- `REVIEW n` followed by shapes such as `Xxxxx Xxxxxx` means the local model still sees names in the copy that are not fakes. Stop and show the shapes. The person checks them with `--reveal` in their own terminal.

`--reveal` prints real values. Never use it when a cloud agent (Cursor, Claude Code, ChatGPT) runs the command: everything a command prints goes to that agent's provider.

## Expected look

A fake keeps the color and the weight (bold, italic) of the original text. In a PDF it is written in the built-in font closest to the original (Helvetica, Times, or Courier). In a Word file each run keeps its own format: the fake takes the format of the name it replaces, and the words around it keep theirs.

A fake word is the same size as the original word, and no wider than that word's box. A long name becomes several short words with gaps. Those gaps are the finished result. Do not join them. Do not shrink the font to fit a longer name. Joining the words is what stacked text and broke the signature line.

Handwriting and the QR code are pictures. The script deletes those pictures. It does not read them. The date line above the signature stays.

Country and city words such as Brasil and São Carlos stay. Day numbers such as "01 de maio" stay. A house number after nº or No. is replaced.

## After every run: check the copies and improve the script

The script is not finished. Each run is a chance to find what it still gets wrong. After every run:

1. Give the person the checklist in [references/improvement-loop.md](references/improvement-loop.md) and ask them to open each `ANON-` copy.
2. Ask what is wrong. They describe it without real values: the copy, the page, the checklist number, the shape of the value, a screenshot of the copy.
3. If something is wrong, follow that file: find the cause with counts, shapes, and yes/no only; write a test with invented data; fix the script; run the tests; rerun the folder; ask them to check again.
4. Log the fix in [references/feedback-log.md](references/feedback-log.md) and open a pull request so other users get it.

## Do not

- Do not ask Ollama or any chat model to rewrite the document. The script is the only writer. The local model only lists people, companies, and CNPJ.
- Do not open, print, or render the original file, the map file, or a page of the copy when a cloud agent runs this skill. Report labels and counts only. Improve the script against the fictional documents in `tests/`.
- Do not split a file name on spaces in a shell loop. Real file names hold names. Quote paths and use `find -print0`.
- Do not run a second pass if the first pass printed `CLEAN`.
- Do not use `page.search_for` yourself. It is case-insensitive and it splits one name into several boxes.
- Do not draw a white rectangle over a signature picture. The picture stays on top. The script deletes the picture.
- Do not replace the bare number `01`. That number is also a date.

The mistakes from the first build, and the rule that prevents each one, are in [references/mistakes.md](references/mistakes.md). Read that file only if you are about to change the script.
