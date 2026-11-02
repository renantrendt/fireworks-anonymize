# offline-anonymize

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

## Evaluating models

`scripts/eval_models.py` measures how well different models find PII,
using the frozen 500-row English test set in `data/` (built from
ai4privacy/pii-masking-300k; see `scripts/build_test_set.py`). Three
providers: Fireworks (`FIREWORKS_API_KEY`), OpenAI (`OPENAI_API_KEY`) and
Anthropic (`ANTHROPIC_API_KEY`).

```bash
export FIREWORKS_API_KEY="fw_..."   # and/or OPENAI_API_KEY, ANTHROPIC_API_KEY
python3 scripts/eval_models.py --list-models        # what your keys can call
python3 scripts/eval_models.py --limit 20           # smoke run
python3 scripts/eval_models.py --by-label           # full run, per-label detail
python3 scripts/eval_models.py --concurrency 16 \
    --models "accounts/fireworks/models/glm-5p3-flash,openai:gpt-5-mini,anthropic:claude-haiku-4-5"
```

Model ids route by name (`gpt-*`/`o1`/`o3`/`o4` → OpenAI, `claude-*` →
Anthropic, anything else → Fireworks); an explicit `openai:` /
`anthropic:` / `fireworks:` prefix overrides that.

Two scores per model: **strict** (value and label must both match gold) and
**value-only** (the PII was found, label ignored — what matters for
anonymization). Per-model raw predictions land in `results/preds-*.jsonl`;
summary metrics in `results/eval-*.json`.

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

## Layered harness experiments (prompting only)

Before fine-tuning, how far does a harness around the stock model get? We added one change at a time to GLM 5.3 Flash on the same 500 frozen test rows and tested each step with McNemar and a bootstrap interval. These runs use their own prompt and scoring (typed F1 on exact values, and leak recall: the share of PII values fully masked), so the numbers are not directly comparable with the table above.

| Layer | Change | Typed F1 | Leak recall | Seconds per text |
|---|---|---|---|---|
| L0 | Regex only (emails, IPs) | 17.7% | 9.8% | 0.0 |
| L1 | Plain prompt (baseline) | 84.5% | 98.8% | 4.0 |
| L2 | + label guide | 86.5% | 97.9% | 5.5 |
| L3 | + 3 worked examples | 87.7% | 98.4% | 2.8 |
| L4 | + grounding and regex merge | 87.8% | 98.6% | 3.1 |
| L5 | + second pass on the masked text | **88.1%** | **99.0%** | 3.8 |

L5 vs L1: +3.6 points, McNemar p < 0.0001, bootstrap 95% range +2.3 to +4.9. The label guide and the examples are real gains; grounding and the second pass are too small to separate from luck. The weakest labels after prompting are ID numbers (passport, social number, driver's license), which is what a fine-tune should target.

Full write-up with architecture diagrams, per-step significance tests, charts and how to reproduce: [docs/layered-harness.md](docs/layered-harness.md).

```bash
export FIREWORKS_API_KEY="fw_..."
python3 scripts/experiments.py --model glm-5p3-flash   # runs L0-L5, writes data/experiments/glm-5p3-flash/results.md
python3 scripts/charts.py --prefix glm-5p3-flash
```

## More detail

- `SKILL.md`: the full operating notes, written for an AI coding agent that
  runs the script.
- `references/mistakes.md`: mistakes from the first build and the rule that
  prevents each.
- `references/feedback-log.md`: each fix that came from checking a copy, with
  its test.
- `references/improvement-loop.md`: how to report a problem without sharing
  real values.
