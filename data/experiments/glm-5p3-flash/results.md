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

## Per-label F1: L1 baseline vs best (L5)

| Label | n | L1 F1 | L5 F1 | Δ |
|---|---|---|---|---|
| DRIVERLICENSE | 147 | 70.3% | 68.5% | -1.8 |
| PASSPORT | 138 | 74.8% | 71.8% | -3.0 |
| SOCIALNUMBER | 140 | 77.6% | 71.9% | -5.7 |
| IDCARD | 155 | 69.1% | 76.3% | +7.2 |
| PASS | 110 | 80.7% | 79.3% | -1.4 |
| GIVENNAME | 165 | 76.4% | 83.3% | +6.9 |
| LASTNAME | 181 | 77.5% | 85.3% | +7.9 |
| DATE | 104 | 75.0% | 86.2% | +11.2 |
| USERNAME | 190 | 84.0% | 87.2% | +3.2 |
| COUNTRY | 107 | 88.1% | 88.8% | +0.7 |
| TEL | 122 | 86.1% | 89.1% | +3.0 |
| TITLE | 106 | 82.9% | 91.2% | +8.2 |
| BOD | 157 | 84.9% | 91.6% | +6.7 |
| POSTCODE | 144 | 91.4% | 92.1% | +0.7 |
| CITY | 152 | 94.3% | 93.4% | -0.9 |
| TIME | 263 | 90.9% | 94.1% | +3.1 |
| IP | 151 | 94.3% | 94.4% | +0.0 |
| STREET | 148 | 84.1% | 94.5% | +10.5 |
| SEX | 100 | 94.3% | 94.9% | +0.6 |
| BUILDING | 153 | 87.8% | 96.1% | +8.4 |
| GEOCOORD | 19 | 80.0% | 97.4% | +17.4 |
| SECADDRESS | 58 | 86.0% | 97.4% | +11.5 |
| EMAIL | 171 | 98.0% | 98.3% | +0.3 |
| STATE | 127 | 96.0% | 98.8% | +2.8 |
| CARDISSUER (low n) | 1 | 40.0% | 100.0% | +60.0 |
