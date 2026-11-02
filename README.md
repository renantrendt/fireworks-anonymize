# PII redaction with open models

Find personal data (PII) in short English texts, label it, and mask it before the text goes to an LLM, using open models on Fireworks instead of a frontier API. Everything is measured on a frozen 500-row English test set built from the validation split of [ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k) (`data/test_set.jsonl`, locked by `data/test_set.sha256`; see `scripts/build_test_set.py`).

Two lines of work, both on GLM 5.3 Flash:

- **Fine-tuning:** a LoRA student trained on 1,000 rows from the train split.
- **Layered harness:** prompting only, one change at a time, to see how far the stock model gets without training.

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
python3 -m pytest tests/test_eval_models.py -q
```

The tests stub the model APIs, so they run without API keys. `scripts/eval_models.py` needs Python 3.9 or later.

## Data and license

Data: [ai4privacy/pii-masking-300k](https://huggingface.co/datasets/ai4privacy/pii-masking-300k), used under the dataset's license, with credit to AI4Privacy.
