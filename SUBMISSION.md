# Offline Anonymize: a fine-tuned PII finder on Fireworks

We fine-tuned GLM 5.3 Flash on Fireworks to list every piece of personal data in a text. On 500 held-out test rows it went from 52.8% to 79.0% of rows fully correct (+26.2 points). McNemar and a bootstrap both say the gain is real. The fine-tune also beats its teacher, Kimi K3 (56.2%), and answers in 0.9 s per document.

Code, data and every scored row are in this repo (`scripts/`, `data/`, `runs/`).

**Disclosure.** The offline anonymizer in this repo existed before the event; it was built for Brazilian legal documents. The test set, training, scoring and router demo were built today. We used Claude Code (an AI coding assistant) throughout.

## Step 1: The task

A lawyer wants to ask Claude or GPT about a client file, but the file holds names, ID numbers, addresses and phone numbers. Privacy law (GDPR in Europe, LGPD in Brazil) limits who may see them. The fix is a router: a company-owned model finds the personal values, the router swaps them for placeholders, and only the masked text reaches the outside model. The router puts the real values back in the answer.

The model that finds the values is the part we measured.

| | |
|---|---|
| The job | List every personal value in a text, with its type (name, email, passport, street…) |
| Who needs it | Legal, security and compliance teams that send client text to AI models |
| Why a stock model is not enough | Stock GLM 5.3 Flash misses 1 personal value in 9 (recall 0.892) |
| Why a fine-tuned open model | More accurate, faster, and the weights belong to the company |
| The data | ai4privacy/pii-masking-300k, English rows. Synthetic but labeled per value |

## Step 2: Baseline

Stock GLM 5.3 Flash: **52.8%** of 500 test rows fully correct. A row passes only when every personal value is found and no normal word is flagged.

- Test set: 500 rows from the validation split, sampled with a fixed seed. 25 labels; 51 rows with no personal data, so wrong flags count. Its SHA-256 hash was committed before any model ran (`data/test_set.sha256`).
- Training rows come from the train split, so no test row is in training.
- Same prompt (`scripts/pii_prompt.py`), temperature 0, for every system.
- One row per test case is saved in `runs/*.jsonl`.

## Step 3: Harness (prompt only)

| Harness | Rows fully correct |
|---|---|
| Stock prompt | 52.8% |
| + 8 solved examples from training | 57.8% (McNemar p = 0.0008) |
| + 16 solved examples | 56.2% |

More examples stopped helping after 8.

## Step 4: Fine-tune

LoRA, rank 8, 3 epochs, on 1,000 training rows with their correct labels. 17 minutes of training on Fireworks.

We also built the "import" set from the Fireworks livestream recipe: Kimi K3 labeled the same 1,000 texts and we kept only its answers that matched the labels (567 rows, `data/train_teacher.jsonl`). It is not trained yet.

## Step 5: Results

| System | Rows fully correct | Values missed | Precision | Bad JSON | Time per doc (p50) |
|---|---|---|---|---|---|
| GLM 5.3 Flash, stock | 52.8% | 10.8% | 0.886 | 9 | not measured |
| + 8 examples in prompt | 57.8% | 8.1% | 0.908 | 4 | 4.0 s |
| Kimi K3 (teacher), stock | 56.2% | 13.7% | 0.904 | 18 | 7.9 s |
| **LoRA student** | **79.0%** | **2.5%** | **0.961** | **0** | **0.9 s** |

A second scoring run of the same student gave 77.8%; the gap is run-to-run noise.

## Step 6: Is it real?

| Comparison | Only before right | Only after right | McNemar p | Bootstrap 95% gain |
|---|---|---|---|---|
| Student vs stock | 29 | 160 | < 0.0001 | +21.4 to +31.0 points |
| Student vs 8-example prompt | 30 | 136 | < 0.0001 | +16.6 to +25.8 points |

Reproduce: `python3 scripts/compare_runs.py runs/base-glm-5p3-flash.jsonl runs/lora-gold.jsonl`

## End-to-end demo

`scripts/router_demo.py` runs one email through the whole path. The student found all 8 values (name, surname, passport, email, phone, building number, street, city). Kimi K3 wrote a reply seeing only placeholders like `[EMAIL_1]`, and the router restored the real values. Output: `runs/router_demo.txt`. This is one example, not a measurement.

## Self-assessment against the rubric

| Criterion | Weight | What this project shows | Where it would lose points |
|---|---|---|---|
| Benchmark | 40% | Same model before and after on 500 held-out rows; +26 points with McNemar and bootstrap; prompt harness and teacher reported | Synthetic English data; one training run; a 20-row smoke check touched the test set before the full runs (no prompt changed after) |
| Business value | 25% | A named user (legal and compliance teams) and a working router demo | No user interviews; no cost-per-document math yet; legal documents not tested |
| Technical execution | 20% | Locked test set, teacher-filtered import set, latency measured, valid JSON on every answer | No hyperparameter sweep; RL ("consolidate") not run; full pipeline (rules + GLiNER + student) not scored |
| Presentation & demo | 10% | Deck with the before/after chart; live router demo | Video [link] |
| Documentation | 5% | README with results and commands; every scored row saved | Training cost not confirmed against billing |

## Limitations

- The data is synthetic English text, under 512 characters. Real legal files are longer and messier.
- Fireworks receives the raw text. Only the company's own model sees personal data; the user's chosen model sees placeholders.
- The router is a script, not yet a Claude Code hook.

## Sources

- ai4privacy/pii-masking-300k on Hugging Face
- Fireworks cookbook and the "import → consolidate" livestream recipe
- Deck: https://claude.ai/artifact/7xvUxa8vTG42FAfF3fEtiw
