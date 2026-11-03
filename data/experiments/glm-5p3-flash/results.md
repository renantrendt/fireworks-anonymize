# PII layer experiments: glm-5p3-flash

Test set: 500 rows, 3309 gold entities (sha256 8b8a0849d8eb...). Temperature 0 everywhere.

## Results

| Layer | What changed | Labeled P | Labeled R | **Labeled F1** | Leak recall | Clean rows | Δ F1 vs L1 | Δ F1 vs prev | Sec/row | Calls/row | Tokens in/out per row |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L0 | Regex only (EMAIL, IP), no model | 98.2% | 9.7% | **17.7%** | 9.8% | 12.0% | -66.8 |  | 0.0 | 0.00 | 0 / 0 |
| L1 | Baseline: plain prompt | 81.7% | 87.4% | **84.5%** | 98.8% | 94.0% |  | +66.8 | 4.0 | 1.00 | 256 / 555 |
| L2 | + label guide | 84.1% | 89.1% | **86.5%** | 97.9% | 90.2% | +2.0 | +2.0 | 5.5 | 1.01 | 712 / 786 |
| L3 | + 3 few-shot examples (train split) | 85.0% | 90.5% | **87.7%** | 98.4% | 91.6% | +3.2 | +1.2 | 2.8 | 1.00 | 1913 / 414 |
| L4 | + grounding + regex merge | 85.0% | 90.8% | **87.8%** | 98.6% | 92.8% | +3.3 | +0.1 | 3.1 | 1.00 | 1913 / 428 |
| L5 | + second pass on masked text | 85.1% | 91.3% | **88.1%** | 99.0% | 94.2% | +3.6 | +0.3 | 3.8 | 2.00 | 3838 / 517 |
| L6 | LoRA fine-tune, plain prompt | 91.8% | 93.7% | **92.7%** | 98.1% | 90.0% | +8.3 | +4.6 | 1.7 | 1.00 | 256 / 119 |
| L6+H | LoRA fine-tune + harness (L2-L5) | 89.9% | 93.6% | **91.7%** | 99.0% | 94.8% | +7.2 | -1.0 | 2.4 | 2.00 | 3838 / 125 |

## Is each step real?

McNemar: per gold entity, found with the right label (pass) or not. Bootstrap: 5,000 resamples of the test rows, 95% CI of the labeled-F1 gap in points. A step counts as real only when both agree.

| Comparison | Entities gained | Entities lost | McNemar p | F1 gap | 95% CI | Verdict |
|---|---|---|---|---|---|---|
| L1 vs L0 | 2572 | 1 | <1e-10 | +66.8 | [+63.8, +69.8] | real gain |
| L2 vs L1 | 136 | 81 | 2.3e-04 | +2.0 | [+0.8, +3.3] | real gain |
| L3 vs L1 | 174 | 71 | <1e-10 | +3.2 | [+1.9, +4.5] | real gain |
| L3 vs L2 | 104 | 56 | 1.8e-04 | +1.2 | [+0.1, +2.1] | real gain |
| L4 vs L1 | 174 | 63 | <1e-10 | +3.3 | [+2.0, +4.6] | real gain |
| L4 vs L3 | 41 | 33 | 0.416 | +0.1 | [-0.6, +0.8] | could be luck |
| L5 vs L1 | 177 | 48 | <1e-10 | +3.6 | [+2.3, +4.9] | real gain |
| L5 vs L4 | 48 | 30 | 0.054 | +0.3 | [-0.4, +1.1] | could be luck |
| L6 vs L1 | 303 | 96 | <1e-10 | +8.3 | [+6.4, +10.2] | real gain |
| L6 vs L5 | 180 | 102 | 4.0e-06 | +4.6 | [+3.0, +6.4] | real gain |
| L6+H vs L1 | 286 | 81 | <1e-10 | +7.2 | [+5.7, +8.9] | real gain |
| L6+H vs L6 | 93 | 95 | 0.942 | -1.0 | [-2.4, +0.3] | could be luck |
| L6+H vs L5 | 145 | 69 | 2.2e-07 | +3.6 | [+2.4, +4.9] | real gain |

## Per-label F1: L1 baseline vs best (L6)

| Label | n | L1 F1 | L6 F1 | Δ |
|---|---|---|---|---|
| PASS | 110 | 80.7% | 74.9% | -5.8 |
| DRIVERLICENSE | 147 | 70.3% | 84.5% | +14.2 |
| IDCARD | 155 | 69.1% | 84.8% | +15.7 |
| PASSPORT | 138 | 74.8% | 86.7% | +11.9 |
| DATE | 104 | 75.0% | 87.9% | +12.9 |
| GIVENNAME | 165 | 76.4% | 88.4% | +12.0 |
| LASTNAME | 181 | 77.5% | 89.2% | +11.8 |
| USERNAME | 190 | 84.0% | 91.1% | +7.1 |
| GEOCOORD | 19 | 80.0% | 92.3% | +12.3 |
| TITLE | 106 | 82.9% | 92.8% | +9.9 |
| BOD | 157 | 84.9% | 92.9% | +8.0 |
| COUNTRY | 107 | 88.1% | 94.3% | +6.3 |
| SOCIALNUMBER | 140 | 77.6% | 95.0% | +17.4 |
| CITY | 152 | 94.3% | 95.1% | +0.8 |
| TIME | 263 | 90.9% | 95.7% | +4.8 |
| EMAIL | 171 | 98.0% | 96.4% | -1.6 |
| TEL | 122 | 86.1% | 96.6% | +10.5 |
| SECADDRESS | 58 | 86.0% | 97.3% | +11.4 |
| BUILDING | 153 | 87.8% | 97.4% | +9.6 |
| POSTCODE | 144 | 91.4% | 97.6% | +6.1 |
| STREET | 148 | 84.1% | 98.3% | +14.2 |
| STATE | 127 | 96.0% | 98.8% | +2.8 |
| SEX | 100 | 94.3% | 99.0% | +4.7 |
| IP | 151 | 94.3% | 99.0% | +4.7 |
| CARDISSUER (low n) | 1 | 40.0% | 100.0% | +60.0 |
