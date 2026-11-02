# offline-anonymize

Replaces people, companies, ID numbers, addresses, phones and emails in legal documents with fake values, on your own computer. By default nothing is sent to a cloud service; if you set `FIREWORKS_API_KEY`, the text goes to the Fireworks AI API instead of the local model.

It reads PDF, Word (.docx), and email (.eml, .msg) files. It writes a copy named `ANON-<file name>` next to each input, plus a report of what it changed and which checks passed.

## What it does

1. **Finds the values.** A local language model (through Ollama) lists people, companies and company tax numbers. A second local model (GLiNER) reads each word in its sentence. Rules find CPF, CNPJ, RG, CEP, phones, emails, links, hashes, passports and register numbers.
2. **Builds one map.** Each real value gets one fake, used everywhere. In folder mode, the same person has the same fake in every file. First-name fakes follow the person's gender when the documents give a clue (Mr./Sra., brasileiro/brasileira, She/Her, Advogado/Advogada, Prezada, Obrigado).
3. **Writes the copy.** Fakes keep the original's font size, color and weight. In PDFs, names in pictures (letterheads, stamps, scans) are painted over where OCR can read them. Email attachments (PDF, Word, pictures) are cleaned the same way and attached again; other types are dropped with the reason in the email body.
4. **Checks the copy.** A text search for every real value, OCR of every page and picture, and a second look by the local model. Each result goes in the report.

## Install (macOS)

```bash
python3 -m venv "$HOME/.local/share/offline-anonymize"
"$HOME/.local/share/offline-anonymize/bin/pip" install -r scripts/requirements.txt
brew install tesseract tesseract-lang ollama
ollama pull qwen3.6:35b-mlx
```

The default model needs about 29 GB of memory (a 48 GB Mac). On a 16 GB Mac use a smaller model; it finds fewer names:

```bash
ollama pull gemma4:12b-it-qat
export OFFLINE_ANON_MODEL=gemma4:12b-it-qat OFFLINE_ANON_CTX=16384
```

The first run downloads the GLiNER model (about 1.2 GB). After that it runs offline. Ollama must be running.

## Run

One file:

```bash
"$HOME/.local/share/offline-anonymize/bin/python" scripts/anonymize.py "/full/path/to/contract.pdf"
```

A folder (recommended for a case with several documents):

```bash
"$HOME/.local/share/offline-anonymize/bin/python" scripts/anonymize.py "/full/path/to/case folder"
```

Folder mode moves the originals into `originals/`, copies that passed every check into `anonymized/`, and the rest into `needs-review/`. **Copy the folder first** if you want to keep its layout.

Two optional files in the case folder:
- `_extra_terms.txt`: values no detector finds (a foreign address, a project code name), one per line. Holds real values; never share it.
- `_keep_terms.txt`: words that were replaced but are not names, one per line.

## Reading the result

The output never prints a real value. Each value shows as a label and a short hash, such as `PERSON_3 (a1f9) -> Elena`.

- `CLEAN`: the text check, OCR and the second look all passed.
- `LEAKS` or `OCR_LEAKS`: a real value is still in the copy. Do not use it.
- `REVIEW`: the local model still sees names it cannot place, or an attachment could not be read. A person must read the copy.
- `MAPFILE`: the real-to-fake map, in `~/.local/share/offline-anonymize/maps/`, readable only by you. Whoever has it can reverse the copy. Never share it.

`--reveal` prints real values. Use it only at your own terminal, never inside an AI agent.

## Tests

```bash
"$HOME/.local/share/offline-anonymize/bin/python" -m pytest tests -q
```

The tests use invented documents and stub the local models, so they run without Ollama.

## Limitations

Read these before you rely on a copy.

- **Tuned on one kind of case.** It was built and corrected against a single real matter: Brazilian corporate and employment documents (contracts, a power of attorney, a legal opinion, email threads) in Portuguese and English. The word lists, ID formats and address rules are Brazilian. Other countries, languages and document types (medical records, court filings, spreadsheets, slides) were not tested.
- **The output is pseudonymized, not anonymized.** Dates, amounts, salaries, job titles, cities and other unique facts are not changed, and the map file can reverse the fakes. Under Brazil's data-protection law (LGPD, arts. 12 and 13) this is still personal data. Someone who knows the matter can often recognize it from the facts alone.
- **The checks can pass while a value remains.** The text check and OCR only look for values the detectors found. In the case it was tuned on, a later comparison against the originals still found missed values after every check had passed: an identity number written without dots, a company register number, a hash written with a typographic ligature, a home address split over lines. Each was fixed, but new documents will have new shapes. **A person who knows the case must read every copy before it is shared.**
- **Scans and pictures are partly covered.** Names in pictures are painted over only where OCR reads them. Handwriting, poor scans and small print can be missed. A PDF with no text layer sends its copy to review.
- **Layout can change.** A fake that is longer than the original word is written smaller. In a PDF, a value in a box too small for any fake is blanked. Word files keep each run's format, but complex layouts were not tested widely.
- **Email copies drop HTML, links and unsupported attachments** (spreadsheets, zip files). Forwarded emails are kept.
- **Gender of fakes depends on clues in the text.** With no clue, a first-name fake can have the wrong gender.
- **Some common words are kept on purpose** (public agencies such as ANVISA, legal template words, languages, countries). A company whose name is one of those words will not be replaced.
- **It needs a large Mac** for the default model, and Ollama, Tesseract and the GLiNER download.

## Fireworks Buildathon results (Nov 2, 2026)

Task: list every personal value in a short English text (ai4privacy/pii-masking-300k). 500 frozen test rows from the validation split; 1,000 training rows from the train split. One fixed prompt (`scripts/pii_prompt.py`).

| System | Rows fully correct | Values missed | Precision | p50 time |
|---|---|---|---|---|
| GLM 5.3 Flash, stock | 52.8% | 10.8% | 0.886 | not measured |
| + 8 examples in prompt | 57.8% | 8.1% | 0.908 | 4.0 s |
| Kimi K3 (teacher), stock | 56.2% | 13.7% | 0.904 | 7.9 s |
| LoRA student (rank 8, 3 epochs, 17 min) | **79.0%** | **2.5%** | **0.961** | **0.9 s** |

Student vs stock: +26 points (bootstrap 95% range +21 to +31), McNemar p < 0.0001. The student also beats its teacher, Kimi K3.
A 20-row smoke check ran on test rows before the full runs; no prompt changed afterwards.

Reproduce:

```bash
python3 scripts/build_train_set.py            # data/train_gold.jsonl
python3 scripts/score_fireworks.py <model> runs/<name>.jsonl [--fewshot 8]
python3 scripts/compare_runs.py runs/<before>.jsonl runs/<after>.jsonl
```

`scripts/build_teacher_set.py` builds `data/train_teacher.jsonl`: Kimi K3's answers kept only when they match the labels (567 of 1,000). Not trained yet.

## More detail

- `SKILL.md`: the full operating notes, written for an AI coding agent that runs the script.
- `references/mistakes.md`: mistakes from the first build and the rule that prevents each.
- `references/feedback-log.md`: each fix that came from checking a copy, with its test.
- `references/improvement-loop.md`: how to report a problem without sharing real values.
