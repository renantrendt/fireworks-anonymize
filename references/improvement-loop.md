# Improving the pipeline without seeing the documents

The person who owns the documents finds the problems. The agent fixes the script. The agent never sees a real value, even while it debugs. Every fix used this loop on 2026-10-20 and 2026-10-21.

## 1. After every run, ask the person to check each copy

Give them this checklist. They open each `ANON-` copy, never the original, next to the original if they want to compare.

1. **A real name is still visible.** It can be in the text, a header, a signature picture, a logo, a table, or the file name.
2. **The same person or company has two different fakes.** Compare the signature page with the body, the PDF with the email, capitals with normal case.
3. **An ordinary word was replaced.** A role, a city, a legal term, or "S/A" became a fake.
4. **The layout broke.** Text overlaps, the font size changed, a line moved, a signature line is missing.
5. **A file was skipped or has `REVIEW` / `LEAKS`.** Something the script could not decide or could not fix.
6. **Anything else that looks wrong.**

## 2. How the person reports a problem

Ask them for, without any real value:

- the copy (`ANON-` file name, or the copy id from the output), and the page or paragraph;
- the number from the checklist;
- the shape of the value, if useful: "a company name, two words", "a 3-letter word", "a name in capitals";
- a screenshot of the copy only, never of the original.

If they paste or type a real value, do not repeat it, do not save it, and ask them to describe it by shape instead.

## 3. Find the cause with counts, shapes, and yes/no only

The agent may run small diagnostic scripts on the originals and the map file on this computer, if every printed line is one of:

- a count ("reference names still visible: 3");
- a label and hash (`PERSON_6 (bcf7)`);
- a shape (`Xxxxxxxxx X/X`, digits as `d`);
- a yes/no ("real word only inside an email address: True");
- a measurement (box width, font size, memory, seconds).

Filter the script's output through a pattern that allows only those line formats, and count the lines it hides. Questions that solved real problems:

| Problem | Question that found the cause |
|---|---|
| OCR saw a name the text check missed | Is the value in the copy's text in another case or without accents? |
| Same signer, two fakes | Do the two real words differ only in case, in accents, or by one letter? |
| A fake did not fit a PDF box | Which file's run found this value, and was it measured in the PDF? |
| A value stayed after "CLEAN" | Is the real word in the copy only in lowercase, or only inside an email address? |
| The local model found fewer names | Run the same file twice: is it run-to-run variation or a code change? |

## 4. Reproduce it with invented data, then fix

1. Write a test in `tests/test_anonymize.py` that rebuilds the pattern with invented names and numbers. The test must fail before the fix.
2. Fix `scripts/anonymize.py`.
3. Run all tests: `~/.local/share/offline-anonymize/bin/python -m pytest <skill folder>/tests -q`.
4. If a rule was a mistake that could come back, add it to `references/mistakes.md`.

## 5. Rerun and ask the person to check again

Rerun the whole case folder, show only the result lines, and ask the person to check the same spot and the checklist again. Repeat until they say the copies are right.

## 6. Record the fix and share it

- Add one line to `references/feedback-log.md`: the date, the checklist number, the cause in one sentence, the test name. No real value.
- Open a pull request so the other people using this skill get the fix. If the person does not use git, the agent does it for them; their GitHub account needs access to the repository.

## What never changes

- The agent never opens, prints, renders, or copies an original, the map file, or a page of an original.
- The agent never uses `--reveal`.
- Diagnostic copies of originals in a scratch folder are deleted when the check ends.
