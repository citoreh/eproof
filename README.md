# persian_proof: proofreading for Persian ePubs

This tool checks spelling, grammar and typography across a large collection of Persian ePubs. It produces a browsable report and corrected copies of the books with only the safe fixes applied. Your original files are never modified.

## How it works

| Layer | What it catches | Cost | Applied automatically? |
|---|---|---|---|
| **1. Typography rules** | Arabic ي/ك, presentation-form characters, Arabic digits, Latin `, ? ;` after Persian words, spacing around punctuation, «می/نمی» and «ها» written with a space instead of a half-space, broken or dangling ZWNJ | free, fast | **Yes** (safe rules only) |
| | Other likely ZWNJ cases (تر/ترین, خسته ام), repeated words (از از), missing space after full stop, kashida | | No, reported for review |
| | Latin digits and "quotes" in Persian text | | No, reported as house style |
| **2. Corpus spelling** | Words that are rare across *all* your books and one letter away from a very common word (دانشگاح → دانشگاه, مدرصه → مدرسه), and words whose half-space differs from how the rest of the corpus writes them (میگفتند → می‌گفتند) | free | No, reported for review |
| **3. Claude grammar pass** | Real spelling/grammar/wrong-word errors in context; instructed to ignore dialogue in colloquial Persian, poetry, archaic usage and Arabic | paid (Anthropic API) | No, reported for review |

Guard rails:
- Heavily diacritised or Arabic blocks (Quran verses, Arabic quotations) are skipped by the typography rules.
- «می» is only auto-joined when the next word ends like a conjugated verb, so «می کهنه», «جام می» and similar poetic uses of *می* (wine) are left alone.
- Every issue Claude reports is checked against the actual paragraph. Invented or misquoted text is discarded.
- Corrected ePubs are edited only in the text between tags. Markup, CSS, images and structure stay byte-identical.

## Install

```bash
pip install -r requirements.txt        # lxml, rapidfuzz, anthropic
```

Python 3.9+. Works on macOS, Linux and Windows.

## Usage

Put your ePubs in a folder. Subfolders are fine.

```bash
# 1. Typography + corpus spelling (free). Writes ./proof/report/index.html
python -m persian_proof scan ~/Books/epubs -o ./proof

# 2. Write corrected copies (safe fixes only) to ./proof/fixed/, same folder structure
python -m persian_proof fix -o ./proof
```

Open `proof/report/index.html`. Books are ranked by error density, and each book has its own page with every issue in context. Findings can be filtered by category and searched.

### Grammar pass with Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# How many tokens will the whole collection take? (uses the free token-counting endpoint)
python -m persian_proof grammar estimate -o ./proof --price-in <$/M> --price-out <$/M>

# Try it on 3 books right away (synchronous, no waiting) and look at the report
python -m persian_proof grammar pilot -o ./proof --books 3

# Happy with the quality? Send everything as Message Batches (discounted, usually done within hours)
python -m persian_proof grammar submit  -o ./proof
python -m persian_proof grammar status  -o ./proof
python -m persian_proof grammar collect -o ./proof      # re-run until all batches are collected
```

- `--model` picks the model. The default is Claude Haiku 4.5, the cheapest option. Pilot a few books with a Sonnet model as well and compare the quality. Look up current prices at https://www.anthropic.com/pricing (the Batch API is discounted).
- `--only file1.epub file2.epub` limits any grammar command to specific books.
- `--registers registers.csv` gives Claude a genre note per book, for example:
  ```csv
  file,register
  hafez.epub,classical poetry
  novel-x.epub,modern novel with colloquial dialogue
  ```
- `--min-conf 0.7` raises the confidence bar. The default is 0.6.
- `grammar submit --dry-run` writes the requests to a file without sending anything.

## Tuning the spelling check

The first scan writes `proof/spelling_candidates.csv`: every suspicious word form in the corpus, listed once, with its suggested correction and counts. Reviewing this one file is much faster than reviewing book by book.

- Add false positives (names, rare vocabulary) to `proof/whitelist.txt`, one per line, then run `scan` again. Unchanged books are read from cache.
- Pass a dictionary of known-good words with `--dict fa_IR.dic` (Hunspell format or a plain word list). Repeat the flag for several dictionaries.
- Thresholds, with defaults tuned for roughly 1,000 books:
  - `--rare-max 3`: a suspect appears at most this many times in the corpus
  - `--rare-books 2`: ...and in at most this many books
  - `--freq-min 30`: the "correct" form appears at least this many times
  - `--ratio 40`: ...and is at least this many times more common
  - `--min-len 4`: shorter words are ignored

For a small trial collection, lower `--freq-min` and `--ratio`, because there isn't enough text yet for the statistics to be reliable.

## Output folder

```
proof/
  report/index.html          ranked overview of all books
  report/books/<id>.html      one page per book
  report/all_issues.csv       every review item (not the auto-fixes), for Excel/Sheets
  report/summary.csv          per-book counts and error density
  spelling_candidates.csv     corpus-wide list of suspect words
  whitelist.txt               your exceptions
  fixed/                      corrected ePubs (after `fix`)
  books.json, blocks/, issues/, grammar/   working data
```

## Notes

- Auto-fixes are deliberately conservative. Anything that could be a stylistic choice or ambiguous is reported, not changed.
- Grammar suggestions are never applied automatically. They go into the report for an editor to accept or reject.
- If you want a publisher-specific house style (for example, always Persian digits and «» quotes), the `style` rules in `persian_proof/rules.py` can be promoted from `"style"` to `"safe"`.
