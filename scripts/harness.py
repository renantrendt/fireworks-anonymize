#!/usr/bin/env python3
"""PII detection harness around open-weight models on Fireworks.

Layers (each one a switch, so experiments.py can add them one at a time):
  prompt     plain (label names only) or guide (label meanings + rules)
  shots      0-3 few-shot examples from the TRAIN split
  ground     keep only values that occur verbatim in the text
  regex      add EMAIL / IP found by regex (regex wins on those formats)
  passes     2 = send the masked text back: "what PII is still here?"
Output: [{"value","label"}] per text, and the text masked with [LABEL].

Usage:
  export FIREWORKS_API_KEY=...
  python scripts/harness.py --run qwen-full --model qwen3p8-max --limit 20
  python scripts/harness.py --run glm-plain --model glm-5p3-flash --prompt plain --shots 0 --no-ground --no-regex
  python scripts/harness.py --model glm-5p3-flash --text "Call Jane Doe at +44 20 7946 0958"

Predictions (one line per test row, with tokens and latency) and the report
go to data/runs/<run>/. Runs resume where they stopped; errored rows retry.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

FEWSHOT_PATH = ev.DATA_DIR / "fewshot.json"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
MODEL_PREFIX = "accounts/fireworks/models/"

LABEL_GUIDE = {
    "GIVENNAME": "first or middle name of a person (each given name is its own entity)",
    "LASTNAME": "family name / surname of a person (each surname is its own entity)",
    "TITLE": "honorific or title before a name: Mr, Mrs, Dr, Madame, Countess, Chief",
    "SEX": "gender or sex of a person: Male, Female, Feminine, Non-binary",
    "BOD": "date of birth, in any format: 08/02/1963, July/42, 1952-06-29T00:00:00",
    "DATE": "any other calendar date (not a birth date)",
    "TIME": "time of day: 13:47, 7 AM, 7 o'clock",
    "EMAIL": "email address",
    "TEL": "phone number, including country code and separators",
    "USERNAME": "account handle or username",
    "PASS": "password or passcode (may contain unusual symbols; copy it exactly)",
    "IP": "IPv4 or IPv6 address",
    "IDCARD": "national ID card number",
    "PASSPORT": "passport number",
    "DRIVERLICENSE": "driver's license number",
    "SOCIALNUMBER": "social security / national insurance / tax number",
    "CARDISSUER": "payment card issuer: Visa, Mastercard, Diners Club International",
    "BUILDING": "building or house number only, e.g. 22 in '22 Main Road'",
    "STREET": "street name without the number: Main Road, Ludlow Street",
    "SECADDRESS": "secondary address unit: Apt 4, Suite 12, Penthouse 301, Dorm 94",
    "CITY": "city, town, or locality name",
    "STATE": "state, province, or region, including codes like ENG",
    "POSTCODE": "postal or ZIP code",
    "COUNTRY": "country name or country code: United Kingdom, GB",
    "GEOCOORD": "geographic coordinates: [52.98, -2.45]",
}
LABELS = set(LABEL_GUIDE)

# L1 baseline: a reasonable plain prompt, not a weakened one. It names the
# labels and the output format, nothing about what each label means.
PLAIN_PROMPT = """Extract all personally identifiable information (PII) from the user's text.
Use only these labels: {names}.
Output ONLY a JSON array of objects: [{{"value": "...", "label": "..."}}], where value is copied exactly from the text. Output [] if there is no PII."""

GUIDE_PROMPT = """You are a PII extraction engine. Find every piece of personally identifiable information in the user's text.

Labels (use exactly these names):
{guide}

Rules:
- Copy each value EXACTLY as it appears in the text (same case, spacing, punctuation). Never normalize or reformat.
- Split names: "Jane Doe" is GIVENNAME "Jane" + LASTNAME "Doe". Split addresses into BUILDING, STREET, SECADDRESS, CITY, STATE, POSTCODE, COUNTRY.
- If the same value appears several times, list it once.
- Do not label generic words, organisations, products, or job roles that are not tied to a person's identity.
- Output ONLY a JSON array of objects: [{{"value": "...", "label": "..."}}]. Output [] if there is no PII. No prose, no markdown."""

SECOND_PASS_NOTE = ("The text below has already been partly anonymized: [LABEL] marks values that were found. "
                    "List only PII that is still visible in plain text.")

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
IPV6_RE = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}(?![\w:])")


@dataclass
class Config:
    model: str = "qwen3p8-max"
    prompt: str = "guide"          # "plain" | "guide"
    shots: int = 3
    ground: bool = True
    regex: bool = True
    passes: int = 1
    llm: bool = True               # False = regex only (L0)
    reasoning_effort: str | None = None
    max_tokens: int = 8192


# ---------------------------------------------------------------- few-shot

def build_fewshot(k: int = 3, seed: int = 0) -> list[dict]:
    """Greedy pick of k English TRAIN rows that together cover the most labels."""
    from datasets import load_dataset
    canonical = json.loads(ev.LABELS_PATH.read_text())["raw_to_canonical"]
    ds = load_dataset("ai4privacy/pii-masking-300k", split="train")
    pool = [r for r in ds.select(range(20000)) if r["language"] == "English"]
    random.Random(seed).shuffle(pool)
    pool = pool[:3000]
    chosen, covered = [], set()
    for _ in range(k):
        best = max(pool, key=lambda r: len({canonical.get(e["label"], e["label"]) for e in r["privacy_mask"]} - covered))
        pool.remove(best)
        ents, seen = [], set()
        for e in sorted(best["privacy_mask"], key=lambda e: e["start"]):
            item = (e["value"], canonical.get(e["label"], e["label"]))
            if item not in seen:
                seen.add(item)
                ents.append({"value": item[0], "label": item[1]})
        covered |= {e["label"] for e in ents}
        chosen.append({"id": best["id"], "text": best["source_text"], "entities": ents})
    FEWSHOT_PATH.write_text(json.dumps(chosen, indent=2, ensure_ascii=False))
    print(f"Wrote {FEWSHOT_PATH}: {k} train rows covering {len(covered)} labels")
    return chosen


def load_fewshot(n: int) -> list[dict]:
    if n <= 0:
        return []
    shots = json.loads(FEWSHOT_PATH.read_text()) if FEWSHOT_PATH.exists() else build_fewshot()
    return shots[:n]


# ---------------------------------------------------------------- model call

class Model:
    def __init__(self, cfg: Config):
        self.name = cfg.model if "/" in cfg.model else MODEL_PREFIX + cfg.model
        self.cfg = cfg
        self.key = os.environ.get("FIREWORKS_API_KEY") or sys.exit("ERROR set FIREWORKS_API_KEY")
        if cfg.prompt == "plain":
            system = PLAIN_PROMPT.format(names=", ".join(LABEL_GUIDE))
        else:
            system = GUIDE_PROMPT.format(guide="\n".join(f"- {k}: {v}" for k, v in LABEL_GUIDE.items()))
        self.base = [{"role": "system", "content": system}]
        for shot in load_fewshot(cfg.shots):
            self.base += [{"role": "user", "content": shot["text"]},
                          {"role": "assistant", "content": json.dumps(shot["entities"], ensure_ascii=False)}]

    def chat(self, text: str, note: str | None = None, retries: int = 4) -> tuple[str, dict]:
        """Returns (answer, usage). Usage counts every attempt, retries included."""
        user = f"{note}\n\n{text}" if note else text
        body = {"model": self.name, "messages": self.base + [{"role": "user", "content": user}],
                "temperature": 0, "max_tokens": self.cfg.max_tokens}
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
        req = urllib.request.Request(FIREWORKS_URL, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    payload = json.loads(resp.read())
                usage["calls"] += 1
                for k in ("prompt_tokens", "completion_tokens"):
                    usage[k] += (payload.get("usage") or {}).get(k, 0)
                content = payload["choices"][0]["message"].get("content") or ""
                # Reasoning models can spend the whole budget thinking and
                # return nothing; an empty answer is a failure, not "no PII".
                if content.strip():
                    return content, usage
                if attempt == retries:
                    raise RuntimeError("empty answer (reasoning used the token budget?)")
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                    raise RuntimeError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == retries:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


def parse_entities(raw: str) -> list[dict]:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    raw = re.sub(r"```(?:json)?", "", raw)
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            label = re.sub(r"\d+$", "", str(item.get("label") or "").strip().upper())
            if value and label in LABELS:
                out.append({"value": value, "label": label})
    return out


# ---------------------------------------------------------------- pipeline

def regex_entities(text: str) -> list[dict]:
    found = [{"value": m.group(0).rstrip("."), "label": "EMAIL"} for m in EMAIL_RE.finditer(text)]
    found += [{"value": m.group(0), "label": "IP"} for rx in (IPV4_RE, IPV6_RE) for m in rx.finditer(text)]
    return found


def ground(entities: list[dict], text: str) -> list[dict]:
    """Keep values that occur in the text; repair case-only differences."""
    kept = []
    lower = text.lower()
    for e in entities:
        if e["value"] in text:
            kept.append(e)
        else:
            i = lower.find(e["value"].lower())
            if i >= 0:
                kept.append({"value": text[i:i + len(e["value"])], "label": e["label"]})
    return kept


def merge(*groups: list[dict]) -> list[dict]:
    """First group wins on a value; later groups only add new values."""
    out, seen = [], set()
    for group in groups:
        for e in group:
            if e["value"] not in seen:
                seen.add(e["value"])
                out.append(e)
    return out


def mask(text: str, entities: list[dict]) -> str:
    labels = {e["value"]: e["label"] for e in entities if e["value"]}
    if not labels:
        return text
    parts = []
    for value in sorted(labels, key=len, reverse=True):
        left = r"(?<!\w)" if re.match(r"\w", value) else ""
        right = r"(?!\w)" if re.search(r"\w$", value) else ""
        parts.append(left + re.escape(value) + right)
    pattern = re.compile("|".join(parts))
    return pattern.sub(lambda m: f"[{labels[m.group(0)]}]", text)


def add_usage(total: dict, more: dict) -> None:
    for k, v in more.items():
        total[k] = total.get(k, 0) + v


def detect(model: Model | None, text: str, cfg: Config) -> tuple[list[dict], dict]:
    usage: dict = {}
    found = regex_entities(text) if cfg.regex else []
    if not cfg.llm:
        return merge(found), usage
    raw, u = model.chat(text)
    add_usage(usage, u)
    llm = parse_entities(raw)
    if cfg.ground:
        llm = ground(llm, text)
    # Regex goes first so it wins on its own formats.
    entities = merge(found, llm)
    for _ in range(cfg.passes - 1):
        raw, u = model.chat(mask(text, entities), SECOND_PASS_NOTE)
        add_usage(usage, u)
        extra = parse_entities(raw)
        entities = merge(entities, ground(extra, text) if cfg.ground else extra)
    return entities, usage


# ---------------------------------------------------------------- run

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="Run name; output goes to data/runs/<run>/")
    p.add_argument("--model", default=Config.model, help="Fireworks model id (short or full)")
    p.add_argument("--prompt", choices=["plain", "guide"], default=Config.prompt)
    p.add_argument("--shots", type=int, default=Config.shots, help="Few-shot examples from the train split (0-3)")
    p.add_argument("--no-ground", action="store_true", help="Keep values even if not in the text")
    p.add_argument("--no-regex", action="store_true", help="Skip the EMAIL/IP regex layer")
    p.add_argument("--no-llm", action="store_true", help="Regex only (L0)")
    p.add_argument("--passes", type=int, default=Config.passes, help="2 = add a 'what is still visible' pass")
    p.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default=None)
    p.add_argument("--max-tokens", type=int, default=Config.max_tokens)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--fresh", action="store_true", help="Delete existing predictions for this run first")
    p.add_argument("--text", help="Run on one text and print the result (no scoring)")
    return p.parse_args()


def config_from_args(args) -> Config:
    return Config(model=args.model, prompt=args.prompt, shots=args.shots, ground=not args.no_ground,
                  regex=not args.no_regex, passes=args.passes, llm=not args.no_llm,
                  reasoning_effort=args.reasoning_effort, max_tokens=args.max_tokens)


def run_row(model: Model | None, row: dict, cfg: Config) -> dict:
    started = time.time()
    record = {"id": row["id"], "backend": model.name if model else "regex", "gliner": "off"}
    try:
        ents, usage = detect(model, row["source_text"], cfg)
        record.update(entities=ents, output_text=mask(row["source_text"], ents),
                      replaced={e["value"]: e["label"] for e in ents}, fakes=[], usage=usage, error=None)
    except Exception as exc:
        record.update(entities=[], output_text=None, replaced={}, fakes=[], usage={},
                      error=f"{type(exc).__name__}: {exc}")
    record["seconds"] = round(time.time() - started, 2)
    return record


def run(cfg: Config, run_name: str, limit: int | None = None, workers: int = 8, fresh: bool = False) -> dict:
    ev.check_frozen()
    model = Model(cfg) if cfg.llm else None
    rows = ev.load_rows(limit)
    run_dir = ev.RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    pred_path = run_dir / "predictions.jsonl"
    if fresh and pred_path.exists():
        pred_path.unlink()
    done = set()
    if pred_path.exists():
        with pred_path.open() as f:
            # Errored rows are retried; the scorer keeps each id's last line.
            done = {r["id"] for r in map(json.loads, f) if not r["error"]}
    todo = [r for r in rows if r["id"] not in done]
    print(f"[{run_name}] model={model.name if model else 'none'}  {cfg}  rows: {len(done)} done, {len(todo)} to go")

    errors = 0
    with pred_path.open("a") as out, ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(run_row, model, r, cfg) for r in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            errors += bool(rec["error"])
            if rec["error"] or i % 50 == 0 or i == len(todo):
                status = "ERROR " + rec["error"] if rec["error"] else f"{len(rec['entities'])} entities"
                print(f"[{run_name}] {len(done) + i}/{len(rows)}  {rec['id']}  {rec['seconds']}s  {status}")
    if errors:
        print(f"[{run_name}] {errors} rows failed; rerun the same command to retry them")

    meta = {"run": run_name, "backend": model.name if model else "regex", "gliner": "off",
            "system": "harness", "config": asdict(cfg), "limit": limit,
            "test_sha256": ev.SHA_PATH.read_text().split()[0]}
    return ev.finish(run_dir, rows, meta)


def main() -> int:
    args = parse_args()
    cfg = config_from_args(args)
    if args.text:
        ents, usage = detect(Model(cfg) if cfg.llm else None, args.text, cfg)
        print(json.dumps(ents, indent=2, ensure_ascii=False))
        print(mask(args.text, ents))
        print(usage)
        return 0
    if not args.run:
        sys.exit("ERROR --run is required (or use --text)")
    run(cfg, args.run, args.limit, args.workers, args.fresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
