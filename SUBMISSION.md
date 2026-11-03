# PII redaction with a fine-tuned open model on Fireworks

We built a model that finds personal data in text before the text is sent to an outside AI model. We started from rules, then a plain prompt on GLM 5.3 Flash, then a layered prompt harness, then a LoRA fine-tune. All of it was scored on the same 500 locked test texts.

**Headline:** the best prompt harness raises typed F1 from 84.5% to 88.1%. The LoRA fine-tune of GLM 5.3 Flash, with the same plain prompt, reaches **92.7% typed F1** (+8.2 over the plain prompt, +4.6 over the best harness). Wrapped in the full L5 harness it removes 99.0% of personal values and leaves 94.8% of texts with no leak. On our value-only scorer it also beats Kimi K3, a much larger stock model.

Code, data and every scored row are in this repo. The deck: https://claude.ai/artifact/7xvUxa8vTG42FAfF3fEtiw

**Disclosure.** The offline anonymizer in this repo existed before the event; it was built for Brazilian legal documents. It was copied into this repo on the day from earlier private work; everything else below was built on the day. We used AI coding assistants (Claude Code, Cursor) throughout.

## Step 1: The task

Teams want to send support tickets, emails and legal files to an LLM without sending the personal data in them. That means finding every personal value, naming its type, and masking it before the text reaches an outside AI model.

| | |
|---|---|
| The job | Find every personal value in a text and label it with one of 25 types (name, email, passport, street…) |
| Who needs it | Legal, security and compliance teams that route client text through AI models |
| Why rules are not enough | Regex finds 9.8% of personal values; names, addresses and ID numbers have no fixed format |
| Why not a frontier API | The outside provider receives the personal data; with the router, raw text goes only to the company's own Fireworks deployment |
| Why a fine-tuned open model | More accurate, faster, and the company owns the weights |
| The data | ai4privacy/pii-masking-300k, English, synthetic but labeled per value |

How it would run: a router in Claude Code or OpenCode sends the text first to the company's fine-tuned model on Fireworks. The router replaces each value with a placeholder, sends only the masked text to the model the user chose, and puts the real values back in the answer. `scripts/router_demo.py` runs this path on one email (output in `runs/router_demo.txt`). It is one example, not a measurement.

## Step 2: Test set and scoring

- 500 English texts from the validation split, with 3,309 personal values. 51 texts contain none, so over-redaction is measured. Locked with a SHA-256 hash before any model ran.
- Training rows come only from the train split.
- **Typed F1** (headline): the value must be exact and the label right.
- **Leak recall** (privacy): the share of personal values removed from the masked text, whatever label they got.
- Every system: same 500 texts, temperature 0, one saved row per text.

## Step 3: Baseline and harness (prompt layers)

| Layer | Change | Typed F1 | Leak recall | Seconds per text |
|---|---|---|---|---|
| L0 | Rules only (regex) | 17.7% | 9.8% | 0.0 |
| L1 | Plain prompt, GLM 5.3 Flash | 84.5% | 98.8% | 4.0 |
| L2 | + label guide | 86.5% | 97.9% | 5.5 |
| L3 | + 3 worked examples | 87.7% | 98.4% | 2.8 |
| L4 | + grounding and regex | 87.8% | 98.6% | 3.1 |
| L5 | + second pass | 88.1% | 99.0% | 3.8 |

The label guide and the examples are real gains (both statistical tests agree). Grounding, the second pass and the second model are not. A larger model (qwen3p8-max) with the L4 setup scored 87.6%, no better. What prompting could not fix were the ID-number labels: social number, passport and driver's license stayed at 68.5% to 71.9% F1. Full write-up: `docs/layered-harness.md`.

## Step 4: Fine-tune

LoRA on GLM 5.3 Flash, rank 8, 3 epochs, 1,000 rows from the train split with their correct labels. Job settings and timestamps: `runs/finetune-job.json`.

We also built the "import" set from the Fireworks livestream recipe. Kimi K3 labeled the same 1,000 texts, and we kept only the answers that matched the labels: 567 rows, in `data/train_teacher.jsonl`. It is not trained yet.

## Step 5: Results

Every row comes from `scripts/harness.py` and its scorer; the fine-tuned model replaces the stock one. Runs: `data/runs/lora-L1/`, `data/runs/lora-L5/`.

| System | Typed F1 | Leak recall | Seconds per text |
|---|---|---|---|
| Plain prompt, stock (L1) | 84.5% | 98.8% | 4.0 |
| Best harness, stock (L5) | 88.1% | 99.0% | 3.8 |
| **Plain prompt, fine-tuned** | **92.7%** | 98.1% | 2.4 |
| Best harness (L5), fine-tuned | 91.7% | **99.0%** | 3.0 |

The fine-tune gives the best typed F1 on its own. Adding the harness lowers typed F1 by 1 point but removes more values (99.0% leak recall, the same as the stock L5) and leaves more texts fully clean (94.8%, against 90.0% for the fine-tune alone and 94.2% for stock L5). Seconds per text are means over all calls in each run, including retries after rate limits, so they are comparable only with the other harness rows.

Our own scorer (`scripts/score_fireworks.py`, value match only) gives the same order. Rows with every value right and nothing extra: stock 52.8%, 8-example prompt 57.8%, Kimi K3 56.2%, fine-tune 79.0% (77.8% on a second scoring run).

## Step 6: Is it real?

| Comparison | Only before right | Only after right | McNemar p | Bootstrap 95% gain |
|---|---|---|---|---|
| Fine-tune vs stock, rows with every value found and nothing extra (labels ignored; our scorer) | 29 | 160 | < 0.0001 | +21.4 to +31.0 points |
| Same, fine-tune vs 8-example prompt | 30 | 136 | < 0.0001 | +16.6 to +25.8 points |
| Harness scorer: L5 vs L1, values | 48 | 177 | < 0.0001 | +2.3 to +4.9 F1 points |

## Self-assessment against the rubric

| Criterion | Weight | What this project shows | Where it would lose points |
|---|---|---|---|
| Benchmark | 40% | One locked test set; rules → prompt → harness → fine-tune, each scored; McNemar and bootstrap | Synthetic English data; one training run |
| Business value | 25% | A named user and a working router demo | No user interviews; no cost per 1,000 texts yet |
| Technical execution | 20% | Layered harness with each layer tested; fine-tune; teacher-filtered set; latency measured | RL ("consolidate") not run; no hyperparameter sweep |
| Presentation & demo | 10% | Deck with before/after charts; router demo | No video link in this file |
| Documentation | 5% | README, this file, `docs/layered-harness.md`; every scored row saved | Training cost not checked against billing |

## Limitations

- Synthetic English texts under 512 characters. Real legal files are longer and messier.
- Fireworks receives the raw text. Only the company's own model sees personal data; the user's chosen model sees placeholders.
- The router is a script, not yet a Claude Code hook.
- A 20-row smoke check touched the test set before the full runs; no prompt changed afterwards.

## Next

1. Train on the teacher-filtered set, then RL with the same scorer as the reward (import, then consolidate).
2. A frontier comparison (GPT and Claude) on the same 500 texts, with cost per 1,000 texts.
3. Portuguese legal documents, the use case the offline anonymizer was built for.

## Sources

- ai4privacy/pii-masking-300k on Hugging Face
- Fireworks cookbook; the "import → consolidate" livestream slides
- Fireworks: Deploy sample submission (format and rubric weights)
