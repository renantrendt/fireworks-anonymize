# offline-anonymize: the document anonymizer

This is the original tool in this repository (`scripts/anonymize.py`). It is separate from the PII benchmark work described in the main [README](../README.md).

Replace names, companies, ID numbers, addresses, phones and emails in legal
documents with fake values. Two backends:

- **Local** (default): a model on your own Mac through Ollama. Nothing leaves
  the computer.
- **Fireworks AI**: set `FIREWORKS_API_KEY` and the same detectors run through
  the Fireworks API instead — no local model to install, but the document text
  goes to Fireworks' cloud.

Reads PDF, Word (.docx) and email (.eml, .msg). Writes a copy named
`ANON-<file name>` next to each input, plus a report of what changed and
which checks passed.

## Quick start (Fireworks)

```bash
python3 -m venv "$HOME/.local/share/offline-anonymize"
"$HOME/.local/share/offline-anonymize/bin/pip" install -r scripts/requirements.txt
brew install tesseract tesseract-lang
export FIREWORKS_API_KEY="fw_..."   # https://fireworks.ai/account/api-keys

"$HOME/.local/share/offline-anonymize/bin/python" scripts/anonymize.py "/full/path/to/contract.pdf"
```

## Quick start (fully local)

```bash
# same venv and tesseract as above, then:
brew install ollama
ollama pull qwen3.6:35b-mlx         # needs ~29 GB memory (48 GB Mac)

"$HOME/.local/share/offline-anonymize/bin/python" scripts/anonymize.py "/full/path/to/contract.pdf"
```

On a 16 GB Mac use a smaller model instead:

```bash
ollama pull gemma4:12b-it-qat
export OFFLINE_ANON_MODEL=gemma4:12b-it-qat OFFLINE_ANON_CTX=16384
```

Either way, the first run downloads the GLiNER model (~1.2 GB). After that,
the local path runs offline.

## What it does

1. **Finds the values.** A language model (local or Fireworks) lists people,
   companies and company tax numbers. GLiNER reads each word in its sentence.
   Rules find CPF, CNPJ, RG, CEP, phones, emails, links, hashes, passports
   and register numbers.
2. **Builds one map.** Each real value gets one fake, used everywhere. In
   folder mode the same person has the same fake in every file. First-name
   fakes follow the person's gender when the text gives a clue.
3. **Writes the copy.** Fakes keep the original's font size, color and weight.
   Names in pictures are painted over where OCR can read them. Email
   attachments are cleaned the same way and attached again.
4. **Checks the copy.** Text search for every real value, OCR of every page
   and picture, and a second look by the model. Results go in the report.

## Folder mode

```bash
"$HOME/.local/share/offline-anonymize/bin/python" scripts/anonymize.py "/full/path/to/case folder"
```

Reads every PDF, DOCX, MSG and EML in the folder, builds one shared map, then
writes each copy. Originals move to `originals/`, clean copies to
`anonymized/`, the rest to `needs-review/`. **Copy the folder first** if you
want to keep its layout.

Two optional files in the case folder:

- `_extra_terms.txt`: values no detector finds, one per line. Holds real
  values; never share it.
- `_keep_terms.txt`: words that were replaced but are not names, one per line.

## Reading the result

The output never prints a real value. Each value shows as a label and a short
hash, such as `PERSON_3 (a1f9) -> Elena`.

| Status | Meaning |
| --- | --- |
| `CLEAN` | Text check, OCR and second look all passed |
| `LEAKS` / `OCR_LEAKS` | A real value is still in the copy. Do not use it |
| `REVIEW` | The model still sees names it cannot place, or an attachment could not be read |
| `MAPFILE` | The real-to-fake map, in `~/.local/share/offline-anonymize/maps/`, readable only by you. Never share it |

`--reveal` prints real values. Use it only at your own terminal, never inside
an AI agent.

## Tests

```bash
"$HOME/.local/share/offline-anonymize/bin/python" -m pytest tests -q
```

The tests use invented documents and stub the models, so they run without
Ollama or a Fireworks key.

## Limitations

Read these before you rely on a copy.

- **Tuned on one kind of case.** Built and corrected against a single real
  matter: Brazilian corporate and employment documents in Portuguese and
  English. The word lists, ID formats and address rules are Brazilian. Other
  countries, languages and document types were not tested.
- **The output is pseudonymized, not anonymized.** Dates, amounts, salaries,
  job titles, cities and other unique facts are not changed, and the map file
  can reverse the fakes. Under Brazil's LGPD (arts. 12 and 13) this is still
  personal data.
- **The checks can pass while a value remains.** The checks only look for
  values the detectors found. **A person who knows the case must read every
  copy before it is shared.**
- **Scans and pictures are partly covered.** Names in pictures are painted
  over only where OCR reads them. Handwriting and poor scans can be missed.
- **Layout can change.** A fake longer than the original word is written
  smaller; a value in a too-small box is blanked.
- **Email copies drop HTML, links and unsupported attachments.**
- **Gender of fakes depends on clues in the text.** With no clue, a fake can
  have the wrong gender.
- **The Fireworks backend is not offline.** Document text goes to Fireworks'
  API. Use the local path when the documents may not leave the computer.
- **The local path needs a large Mac** for the default model, plus Ollama,
  Tesseract and the GLiNER download.

## More detail

- `../SKILL.md`: the full operating notes, written for an AI coding agent that
  runs the script.
- `../references/mistakes.md`: mistakes from the first build and the rule that
  prevents each.
- `../references/feedback-log.md`: each fix that came from checking a copy, with
  its test.
- `../references/improvement-loop.md`: how to report a problem without sharing
  real values.
