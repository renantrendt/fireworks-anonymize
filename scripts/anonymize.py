#!/usr/bin/env python3
"""Anonymize a PDF, DOCX, MSG, or EML on this computer. No cloud model.

One pass. The original file is never overwritten.
A fake word is chosen so it fits the original box at the original font size.
The same real word always gets the same fake word on this computer.

The output never prints a real value. Each value is shown as a label such as
PERSON_1, plus a 4-character hash. The full map (real -> fake) is saved to
~/.local/share/offline-anonymize/maps/ with owner-only permissions. --reveal
prints real values; use it only in a terminal that a person reads, never in
a cloud agent.

Usage:
  python anonymize.py INPUT.pdf
  python anonymize.py INPUT.docx -o /path/out.docx
  python anonymize.py INPUT.msg            # writes ANON-xxxxxxxx.eml
  python anonymize.py INPUT.pdf --dry-run
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
CPF_RE = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
RG_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}")
# Also the older form with a dot (13.416-253). Real case 2026-10-31.
CEP_RE = re.compile(r"(?<![\d.])(?:\d{5}|\d{2}\.\d{3})-\d{3}(?![\d-])")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# A PDF's text can write "ff" inside a hash as the ligature ﬀ (U+FB00); the
# hash then reads as two short runs. Real case 2026-10-31: the SHA-512 of a
# signed contract stayed in the copy for that reason.
HEX_RE = re.compile(r"\b[0-9a-fA-F\ufb00-\ufb04]{64,128}\b")
HOST_RE = re.compile(r"\b[\w.-]+\.virtua\.com\.br\b", re.I)
ANY_HOST_RE = re.compile(r"(?<![\w.@-])(?:[a-z0-9-]+\.){2,}[a-z]{2,}(?![\w-])", re.I)
# +55 (16) 99999-9999, (16) 3333-4444, 16 99999 9999. Needs 10+ digits.
PHONE_RE = re.compile(r"(?<![\w/.-])(?:\+\d{1,3}[\s.-]?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}(?![\w/-])")
URL_RE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I)
# Law-firm document-management numbers. Anyone at the firm can open the real
# case from them. Real case 2026-10-21: "GED-DOCS.FID1263742" stayed in 63
# email subjects, iManage numbers such as "(2755099.1)" in 6 file names, and
# "KL/Ref.: 1992/2" in the opinion. Group 1 is the value that gets a fake.
DOC_ID_RES = (
    re.compile(r"\b(FID\d{5,})\b"),
    re.compile(r"\((\d{6,8}\.\d{1,2})\)"),
    re.compile(r"\b[A-Z]{1,4}/Ref\.?:?\s*(\d+/\d+)"),
    # The letters before "/Ref" are the firm's initials. Real case
    # 2026-10-31: they stayed in three copies and named the firm.
    re.compile(r"\b([A-Z]{2,4})(?=/Ref\b)"),
)
STREET_RE = re.compile(
    r"\b(?:Rua|Avenida|Av\.|Alameda|Travessa|Rodovia)\s+"
    r"((?:[A-ZÀ-Þ][^\W\d_]+[ \t]*\n?[ \t]*(?:d[aeo]s?[ \t]+)?){1,4})"
    r"(?:,?\s*(?:n[º°o]\.?\s*)?(\d{1,5})(?![\d./-]))?"
    r"(?:[,\s]*(?:apto|apartamento|apt)\.?\s*(\d{1,5})(?![\d./-]))?", re.I)
# Identity and registration numbers written without the dots the other
# detectors expect. Real case 2026-10-31: an RG after "n°" (the degree sign,
# not the ordinal º), a Danish company number after "Commercial Register
# No." and an employee number all stayed in the copies. Group 1 gets a fake
# of the same shape.
ID_NUMBER_RES = (
    re.compile(r"\bRG\s*(?:n[º°o]\.?|No\.?|n\.)?\s*:?\s*(\d{6,10})(?!\d|\.\d)"),
    # Passports: "passaporte dinamarquês de número 209744210". Real case 2026-10-31.
    re.compile(r"\b(?:passaporte|passport)\b[^\d\n]{0,40}?(\d{6,10})(?!\d|\.\d)", re.I),
    re.compile(r"\b(?:CVR(?:-nr)?|Commercial Register|Business Register|Registration|Registro Comercial|Registro Empresarial)"
               r"\s*(?:No\.?|nº|n°|number)?\s*[:.]?\s*(\d{6,12})(?!\d|\.\d)", re.I),
    re.compile(r"\bsob o n[º°]\.?\s*(\d{6,12})(?!\d|\.\d)", re.I),
    re.compile(r"\b(?:employee|matr[ií]cula)\s*(?:No\.?|nº|n°)?\s*[:.]?\s*(\d{4,10})(?!\d|\.\d)", re.I),
    # Geographic coordinates: an e-signature log writes the signer's
    # latitude and longitude ("-23.5505199, -46.6333094"). Real case
    # 2026-10-31. Money and percentages have two decimals, never five.
    re.compile(r"(?<![\d.\w])(-?\d{1,3}\.\d{5,})(?!\d|\.\d)"),
)
# Public law and government sites stay.
KEEP_URL_HOSTS = ("gov.br", "planalto.gov.br", "jus.br")

# A smaller Mac sets these: OFFLINE_ANON_MODEL=qwen3:8b OFFLINE_ANON_CTX=16384.
# To use the Fireworks AI API instead of a local Ollama model, set
# FIREWORKS_API_KEY (and optionally FIREWORKS_MODEL). Text leaves this
# computer when the Fireworks backend is used.
LOCAL_MODEL = os.environ.get("OFFLINE_ANON_MODEL") or "qwen3.6:35b-mlx"
LOCAL_CTX = int(os.environ.get("OFFLINE_ANON_CTX") or 32768)
FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY") or ""
FIREWORKS_MODEL = os.environ.get("FIREWORKS_MODEL") or "accounts/fireworks/models/qwen3-235b-a22b"
FIREWORKS_URL = os.environ.get("FIREWORKS_URL") or "https://api.fireworks.ai/inference/v1/chat/completions"


def llm_generate(prompt: str, max_tokens: int, timeout: int = 300) -> str:
    """One text completion. Fireworks AI when FIREWORKS_API_KEY is set,
    otherwise the local Ollama model."""
    if FIREWORKS_API_KEY:
        body = json.dumps({
            "model": FIREWORKS_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(
            FIREWORKS_URL,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + FIREWORKS_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    body = json.dumps({
        "model": LOCAL_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": LOCAL_CTX},
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("response") or ""


def llm_backend_name() -> str:
    """Which model answered, for the report."""
    return FIREWORKS_MODEL if FIREWORKS_API_KEY else LOCAL_MODEL
LAST_LOCAL_COUNTS: dict[str, int] = {}

EMAIL_TYPES = {".msg", ".eml"}
KNOWN_ATTACHMENT_TYPES = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".zip", ".eml",
    ".msg", ".ics", ".vcf", ".html", ".htm", ".xml", ".json", ".rtf", ".odt",
}


def attachment_type(name: str, ctype: str = "") -> str:
    """The extension only when it is a known type. A file name such as
    "CV maria.souza" would otherwise print a real name."""
    ext = Path(name).suffix.lower()
    if ext in KNOWN_ATTACHMENT_TYPES:
        return ext
    if ctype and re.fullmatch(r"[a-z]+/[a-z0-9.+-]+", ctype) and not name:
        return ctype
    return "other"
SUPPORTED = {".pdf", ".docx"} | EMAIL_TYPES
DOCUMENT_ATTACHMENT_TYPES = {".pdf", ".docx"}
PICTURE_ATTACHMENT_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}
CTYPE_EXT = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/bmp": ".bmp",
    "image/tiff": ".tif", "message/rfc822": ".eml",
}
# An inline picture (a logo, an icon, a signature banner) is dropped unless
# OCR reads at least this many letters in it: then it is a screenshot or a
# scan that carries content, and it stays when it shows no real value.
MIN_PICTURE_LETTERS = 20


class Attachment:
    """A file attached to an email. Owner 2026-10-31: every attachment was
    dropped before, and a case lost its drafts, so now:

    kind "document" (PDF, Word): cleaned like a file of its own, with the
    shared map, and attached again under a cleaned name.
    kind "picture": read with OCR; kept when it shows no real value and
    (if inline) carries text; dropped otherwise.
    kind "other" (a spreadsheet, a zip): the script cannot clean it, so it
    is dropped and the body says so."""

    def __init__(self, name: str, ctype: str, data: bytes, inline: bool):
        self.name = name or ""
        self.ctype = (ctype or "").lower()
        self.data = bytes(data or b"")
        self.inline = inline
        ext = Path(self.name).suffix.lower()
        if ext not in KNOWN_ATTACHMENT_TYPES:
            ext = CTYPE_EXT.get(self.ctype, "")
        self.ext = ext
        self.kind = ("document" if ext in DOCUMENT_ATTACHMENT_TYPES
                     else "picture" if ext in PICTURE_ATTACHMENT_TYPES else "other")
        self.path: Path | None = None

    @property
    def label(self) -> str:
        return self.ext or attachment_type(self.name, self.ctype)


_ATTACH_DIR: list = [None]
_ATTACH_PATHS: dict[str, Path] = {}
# Emails whose attachment the detectors could not read (a scan with no text
# layer, a damaged file): the copy is written but must be read by a person.
REVIEW_ATTACHMENTS: set = set()
LOCAL_SUBFILE: list = [False]


def attachment_dir() -> Path:
    """A private folder for attachments while a run lasts. It holds real
    values, so it sits with the maps (owner-only) and is removed at exit."""
    if _ATTACH_DIR[0] is None:
        import atexit
        import tempfile
        root = anon_home() / "attachments"
        root.mkdir(exist_ok=True)
        os.chmod(root, 0o700)
        d = Path(tempfile.mkdtemp(prefix="run-", dir=root))
        _ATTACH_DIR[0] = d
        atexit.register(shutil.rmtree, d, True)
    return _ATTACH_DIR[0]


def attachment_path(att: Attachment) -> Path:
    """The attachment written to the private folder, once per content. It
    keeps its own name: a name in it must be found and replaced too."""
    if att.path is not None and att.path.exists():
        return att.path
    key = hashlib.sha256(att.data).hexdigest()[:16]
    path = _ATTACH_PATHS.get(key)
    if path is None or not path.exists():
        sub = attachment_dir() / key
        sub.mkdir(exist_ok=True)
        stem = Path(Path(att.name).name).stem[:80] or "attachment"
        path = sub / (stem + att.ext)
        path.write_bytes(att.data)
        os.chmod(path, 0o600)
        _ATTACH_PATHS[key] = path
    att.path = path
    return path


def attachment_text(att: Attachment) -> str:
    """The text of a document attachment, for the detectors and the checks."""
    if att.kind != "document":
        return ""
    try:
        return extract_plain(attachment_path(att))
    except Exception:
        return ""


def picture_png(data: bytes, ext: str) -> bytes | None:
    if ext == ".png":
        return data
    try:
        import fitz
        return fitz.Pixmap(data).tobytes("png")
    except Exception:
        return None

# Role words and law words. These stay. Names are not in this set.
KEEP = {
    "empregador", "empregadora", "empregado", "empregada", "employer", "employee",
    "ltda", "clt", "fgts", "ctps", "cpf", "cnpj", "rg", "cep", "ssp",
    "brasil", "brazil", "brazilian", "brasileira", "brasileiro",
    "carlos", "paulo", "são", "sao",
    "contrato", "contract", "trabalho", "work", "prazo", "period",
    "individual", "employment", "indefinite", "indeterminado",
    "partes", "parte", "parties", "party", "assinou", "assinaturas",
    "testemunha", "testemunhas", "witness", "witnesses",
    "documento", "email", "hash", "original", "porta", "date_atom",
    "gerente", "técnica", "tecnica", "technical", "manager",
    "amil", "d4sign", "zucchetti", "icp", "lgpd", "gdpr", "ccpa", "cpra", "fcpa",
    "hereby", "whereof", "witness", "resolve", "resolves", "resolved",
    "cnpj/mf", "mf", "on", "ntp",
    # Company legal forms. They say what kind of company it is, not which.
    "s/a", "s.a", "s.a.", "sa", "eireli", "me", "epp", "ss", "slu", "ltda.",
    "a/s", "aps", "gmbh",
    "considerando", "whereas", "resolvem",
    "admissão", "admissao", "admission", "função", "funcao", "function",
    "unicidade", "unity", "contratual", "contractual",
    "remuneração", "remuneracao", "remuneration",
    "jornada", "hours", "working", "gestão", "gestao", "management",
    "local", "workplace", "mobility", "mobilidade",
    "viagem", "travel", "despesas", "expenses", "reembolso", "reimbursement",
    "instrumentos", "instruments", "benefícios", "beneficios", "benefits",
    "confidencialidade", "confidentiality", "deveres", "duties",
    "propriedade", "property", "intelectual", "intellectual",
    "proteção", "protecao", "protection", "dados", "data", "pessoais", "personal",
    "aliciamento", "soluções", "solucoes", "controvérsias", "controversias",
    "dispute", "resolution", "disposições", "disposicoes", "provisions",
    "gerais", "general", "cargo", "position", "primeira", "segunda", "terceira",
    "quarta", "quinta", "sexta", "sétima", "setima", "oitava", "nona", "décima",
    "decima", "clause", "cláusula", "clausula",
    # Email headers and greetings. They sit next to names, so the pair rule
    # would otherwise take them for a first name.
    "from", "to", "cc", "bcc", "subject", "sent", "date", "de", "para", "assunto",
    "enviado", "enviada", "encaminhada", "mensagem", "forwarded", "message",
    "prezado", "prezada", "prezados", "caro", "cara", "olá", "ola", "oi",
    "bom", "boa", "dia", "tarde", "noite", "att", "atenciosamente", "obrigado",
    "obrigada", "abraço", "abraços", "abs", "dear", "hi", "hello", "thanks",
    "regards", "best", "cheers", "segue", "seguem", "anexo",
}

# Public bodies, registries, tax and ID acronyms, and the fixed words of deed
# and email templates. Real case 2026-10-21 (26 files): the local model and the
# capitals rule took these for names, so a legal opinion read "the Brazilian
# Health Regulatory Agency (Delta)" and a power of attorney "SAIBAM TODOS
# ELENA ESTE INSTRUMENTO". None of them names a person or a company. They are
# dropped from the hits in any role and kept inside longer names. Lowercase.
PUBLIC_TERMS = {
    "anvisa", "anm", "inpi", "siscomex", "radar", "cnae", "abnt", "nbr", "crq", "nire",
    "drei", "jucesp", "rfb", "inss", "irpf", "irrf", "conama", "ibama", "redesim",
    "cpf", "cnpj", "rg", "denmark", "dinamarca",
    "quantos", "virem", "presents", "testemunho", "revogação", "revogacao",
    "substabelecimento", "contracted", "contractor", "grantor", "outorgante",
    "consultoria", "consultancy", "caution", "xxx", "xxxx", "he/him", "she/her", "they/them", "poa",
    # Checked against a case's originals 2026-10-31: these also got fakes.
    "grantee", "covenanted", "bpto", "dbe", "ddhs", "latam", "contratantes", "outorgantes",
    # Languages. "translated into Portuguese" became "translated into Prado".
    "portuguese", "português", "portugues", "english", "inglês", "ingles", "spanish",
    "espanhol", "danish", "dinamarquês", "dinamarques",
}
KEEP |= PUBLIC_TERMS
# "ANA" is the national water agency and also the first name Ana. It is kept
# only in capitals, and only when the documents also spell out the agency.
ANA_AGENCY_RE = re.compile(
    r"Ag[eê]ncia Nacional de [AÁ]guas|National Water(?: and Sanitation)? Agency", re.I)

# Fakes. Each real word picks from the options that fit its narrowest box,
# preferring the same first letter (the twin rule from the monorepo's
# app/src/lib/pii/twin.mjs). Two real words never share a fake in one file
# while an unused option still fits.
# Fake pools. Real run 2026-10-21 (26 files, about 100 people and 90
# companies): with 46 first names and 30 company words the pools ran out,
# one fake stood for up to 5 different real words, and two companies shared
# "IPE". No word appears in two pools, and no company word is a direction
# or a trade word ("Norte", "Quimica") that reads as part of a real name.
FIRSTS = [
    "Elena", "Maia", "Lara", "Nina", "Vera", "Eva", "Ana", "Lia", "Ivo", "Teo",
    "Bruno", "Caio", "Davi", "Igor", "Joana", "Karen", "Lucas", "Marta", "Otto",
    "Paula", "Renata", "Sofia", "Tiago", "Ugo", "Vitor", "Wagner", "Yara", "Zoe",
    "Alice", "Beatriz", "Clara", "Diego", "Fabio", "Gabriel", "Helena", "Irene",
    "Hugo", "Gil", "Rui", "Noa", "Edu", "Fred", "Olga", "Nara", "Mauro", "Kauan",
    "Adriana", "Afonso", "Alana", "Aline", "Amanda", "Andre", "Angela", "Arthur",
    "Augusto", "Bento", "Bianca", "Bruna", "Carla", "Cecilia", "Celso", "Cintia",
    "Daniela", "Debora", "Denise", "Dora", "Douglas", "Edson", "Elias", "Elisa",
    "Emilia", "Enzo", "Erica", "Estela", "Fausto", "Flavia", "Gisele", "Glauco",
    "Heitor", "Hilda", "Iara", "Ines", "Iris", "Isaac", "Jaime", "Janete", "Jonas",
    "Jorge", "Julia", "Julio", "Laura", "Leandro", "Leila", "Lilian", "Lorena",
    "Luana", "Luiza", "Manuel", "Marcel", "Marina", "Mateus", "Milena", "Miriam",
    "Murilo", "Nadia", "Nelson", "Noemi", "Odete", "Osmar", "Otavio", "Pietro",
    "Raul", "Regina", "Ricardo", "Rita", "Roberto", "Rodrigo", "Ruth", "Samuel",
    "Sandra", "Saulo", "Selma", "Silvia", "Simone", "Tania", "Telma", "Teresa",
    "Thais", "Ulisses", "Valter", "Vanessa", "Vilma", "Wilson", "Yuri", "Zelia",
    "Abel", "Ada", "Ari", "Bia", "Cid", "Ema", "Eli", "Ian", "Ilda", "Leo", "Mia",
    "Noe", "Alba", "Aldo", "Ciro", "Dino", "Iago", "Ivan", "Joel", "Lino", "Luan",
    "Mara", "Tito", "Tais", "Rafael",
]
# Gender of each first-name fake. A man must not read as "Mr. Joana": real
# case 2026-10-21, two men (called Mr. and Sr. in the originals) got the
# fakes Joana and Maia. Names in neither set (Noa, Ari, Eli) fit anyone.
MALE_FIRSTS = {
    "Ivo", "Teo", "Bruno", "Caio", "Davi", "Igor", "Lucas", "Otto", "Tiago", "Ugo", "Vitor",
    "Wagner", "Diego", "Fabio", "Gabriel", "Hugo", "Gil", "Rui", "Edu", "Fred", "Mauro",
    "Kauan", "Afonso", "Andre", "Arthur", "Augusto", "Bento", "Celso", "Douglas", "Edson",
    "Elias", "Enzo", "Fausto", "Glauco", "Heitor", "Isaac", "Jaime", "Jonas", "Jorge", "Julio",
    "Leandro", "Manuel", "Marcel", "Mateus", "Murilo", "Nelson", "Osmar", "Otavio", "Pietro",
    "Raul", "Ricardo", "Roberto", "Rodrigo", "Samuel", "Saulo", "Ulisses", "Valter", "Wilson",
    "Yuri", "Abel", "Cid", "Ian", "Leo", "Noe", "Aldo", "Ciro", "Dino", "Iago", "Ivan", "Joel",
    "Lino", "Luan", "Tito", "Rafael",
}
FEMALE_FIRSTS = {
    "Elena", "Maia", "Lara", "Nina", "Vera", "Eva", "Ana", "Lia", "Joana", "Karen", "Marta",
    "Paula", "Renata", "Sofia", "Yara", "Zoe", "Alice", "Beatriz", "Clara", "Helena", "Irene",
    "Olga", "Nara", "Adriana", "Alana", "Aline", "Amanda", "Angela", "Bianca", "Bruna", "Carla",
    "Cecilia", "Cintia", "Daniela", "Debora", "Denise", "Dora", "Elisa", "Emilia", "Erica",
    "Estela", "Flavia", "Gisele", "Hilda", "Iara", "Ines", "Iris", "Janete", "Julia", "Laura",
    "Leila", "Lilian", "Lorena", "Luana", "Luiza", "Marina", "Milena", "Miriam", "Nadia",
    "Noemi", "Odete", "Regina", "Rita", "Ruth", "Sandra", "Selma", "Silvia", "Simone", "Tania",
    "Telma", "Teresa", "Thais", "Vanessa", "Vilma", "Zelia", "Ada", "Bia", "Ema", "Ilda", "Mia",
    "Alba", "Mara", "Tais",
}
LASTS = [
    "Ferreira", "Carvalho", "Duarte", "Mendes", "Lima", "Melo", "Dias", "Reis",
    "Leal", "Paz", "Luz", "Alves", "Barros", "Costa", "Farias", "Gomes",
    "Henriques", "Jardim", "Kehl", "Nunes", "Oliva", "Pires", "Queiroz", "Rocha",
    "Teles", "Uchoa", "Vidal", "Xavier", "Zanetti", "Brito", "Cunha",
    "Esteves", "Freitas", "Goes", "Ito", "Moura", "Neves", "Prado", "Sales",
    "Abreu", "Aguiar", "Amaral", "Andrade", "Antunes", "Aragao", "Assis", "Azevedo",
    "Bastos", "Batista", "Bezerra", "Borges", "Braga", "Bueno", "Cabral", "Caldas",
    "Campelo", "Cardoso", "Castro", "Coelho", "Correia", "Couto", "Cruz", "Dantas",
    "Diniz", "Dorneles", "Fagundes", "Falcao", "Faria", "Fonseca", "Fontes", "Franco",
    "Galvao", "Garcia", "Godoy", "Guedes", "Lacerda", "Lopes", "Macedo", "Machado",
    "Magalhaes", "Marques", "Martins", "Matos", "Medeiros", "Meireles", "Miranda",
    "Morais", "Nogueira", "Novaes", "Pacheco", "Paiva", "Peixoto",
    "Pedrosa", "Pimentel", "Pinheiro", "Quintela", "Rangel", "Rebelo", "Resende",
    "Ribas", "Ribeiro", "Sampaio", "Santana", "Seixas", "Simoes", "Soares", "Tavares",
    "Toledo", "Valente", "Valadares", "Vasconcelos", "Veloso", "Viana", "Vieira",
    "Vilela", "Zago", "Ruas", "Lins", "Leme", "Mota", "Paes", "Pena", "Rios", "Sena",
    "Vaz", "Lago", "Maciel",
]
ORGS = [
    "Verdequim", "Alfa", "Boreal", "Cobalto", "Delta", "Estrela", "Fenix", "Granito",
    "Horizonte", "Ipe", "Jacaranda", "Lumen", "Mirante", "Nimbus", "Orion", "Prisma",
    "Quartzo", "Rubi", "Safira", "Topazio", "Umbra", "Vetor", "Zenite", "Atlas",
    "Albatroz", "Aldebaran", "Altair", "Andromeda", "Antares", "Aracati", "Arpoador",
    "Avante", "Bambu", "Basalto", "Berilo", "Bromelia", "Cajuina", "Calcita", "Canopus",
    "Carmim", "Cedro", "Cinabre", "Citrino", "Condor", "Coral", "Crisalida", "Ebano",
    "Equinox", "Esmeril", "Feldspato", "Galena", "Gavea", "Girassol", "Grauna", "Helix",
    "Jade", "Jaspe", "Jatoba", "Kappa", "Lazuli", "Lince", "Magnolia", "Malaquita",
    "Mandacaru", "Marfim", "Meridiano", "Mica", "Netuno", "Obsidiana", "Onix", "Opala",
    "Pampa", "Pegaso", "Peroba", "Polaris", "Pulsar", "Quasar", "Rigel", "Saturno",
    "Selene", "Sequoia", "Sirius", "Solaris", "Tangara", "Tucano", "Turmalina", "Urano",
    "Vega", "Zircon", "Eon", "Ion", "Neo", "Vox", "Axis", "Beta", "Gama", "Zeta", "Omega",
]
PLACES = [
    "Lemos", "Central", "Norte", "Leste", "Nova", "Aurora", "Bosque", "Oeste",
    "Alvorada", "Arvoredo", "Cascata", "Colina", "Encosta", "Esplanada", "Floresta",
    "Granja", "Primavera", "Recanto", "Remanso", "Ventura", "Vertente", "Pomar",
]

STREET_PREFIX = {"rua", "avenida", "av", "av.", "alameda", "travessa"}
TITLE_PREFIX = {"sr", "sr.", "sra", "sra.", "mr", "mr.", "mrs", "mrs.", "dr", "dr.", "dra", "dra."}
CONNECTORS = {"de", "da", "do", "dos", "das", "e", "and"}


def core_token(raw: str) -> str:
    return raw.strip(".,;:()[]\"'`")


def stable_index(key: str, n: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def anon_home() -> Path:
    """Local folder for the secret and the maps. Never inside a project."""
    home = Path(os.environ.get("OFFLINE_ANON_HOME") or Path.home() / ".local/share/offline-anonymize")
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    return home


_SECRET: bytes | None = None


def local_secret() -> bytes:
    """Random key made once per computer. Makes fakes and hashes unguessable."""
    global _SECRET
    if _SECRET is None:
        path = anon_home() / "secret"
        if not path.exists():
            path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
        _SECRET = path.read_bytes()
    return _SECRET


def keyed(token: str) -> str:
    return hmac.new(local_secret(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def seed_index(token: str, n: int) -> int:
    return int(keyed(token)[:8], 16) % n


def short_hash(token: str) -> str:
    """4 characters. Lets a person match two runs without seeing the value."""
    return keyed("label:" + token)[:4]


def mask_shape(value: str, salt: str = "") -> str:
    """Keep punctuation and the letter/digit pattern. Every digit and letter
    comes from a keyed hash of the real value, so the fake cannot be turned
    back into the real value without this computer's secret."""
    stream = ""
    counter = 0
    out = []
    for ch in value:
        if not ch.isalnum() or not ch.isascii():
            out.append(ch)
            continue
        while len(stream) < 2:
            stream += keyed(f"shape:{counter}:{value}")
            counter += 1
        byte = int(stream[:2], 16)
        stream = stream[2:]
        if ch.isdigit():
            out.append(str(byte % 10))
        elif ch.isupper():
            out.append(chr(ord("A") + byte % 26))
        else:
            out.append(chr(ord("a") + byte % 26))
    masked = "".join(out)
    if masked == value and any(c.isalnum() for c in value):
        return mask_shape(value + "#", salt)[: len(value)]
    return masked


def text_width(text: str, size: float, fontname: str = "helv") -> float:
    import fitz
    return fitz.get_text_length(text, fontname=fontname, fontsize=size)


# Real name words of the current map, accent- and case-blind. A fake that
# contains one is never picked. Real run 2026-10-21: the fake "Helena"
# contained the real first name "Helen", and the check passed it.
REAL_NAME_FOLDS: set[str] = set()


def contains_real_name(fake: str, own=()) -> bool:
    """True when the fake holds its own real name ("Helena" for "Helen",
    "Adriana" for "Ana", 3+ letters) or any real name of 4+ letters. Another
    person's 3-letter name inside a fake ("Ana" in "Joana") is allowed: it
    reveals nothing, and forbidding it renamed people the lawyer had read."""
    folded = strip_accents(fake).casefold()
    if any(real in folded for real in REAL_NAME_FOLDS):
        return True
    own_words = {strip_accents(w).casefold() for t in own for w in re.findall(r"[^\W\d_]+", t)}
    return any(len(o) >= 3 and o in folded for o in own_words)


def blend_fakes(shown: list[str], used: set[str], token: str) -> list[str]:
    """Every option is taken: make an unused word from the front of one
    option and the back of another ("Verdequim" + "Lumen" -> "Verdmen"), so
    two people never share a fake. Seeded by the real word."""
    n = len(shown)
    start = seed_index(token or shown[0], n)
    for k in range(n * n):
        x = shown[(start + k) % n]
        y = shown[(start + k * 7 + 3) % n]
        blend = x[: max(2, len(x) // 2)] + (y[len(y) // 2:].lower() if not x.isupper() else y[len(y) // 2:].upper())
        if blend.casefold() not in used and not contains_real_name(blend, [token]):
            return [blend]
    return shown


def fit_choice(options: list[str], width: float, size: float, upper: bool,
               token: str = "", taken: set | None = None) -> str:
    """An option that fits the box at this size. Never a longer word.

    The pick is seeded by the real word, so two different people do not get
    the same fake. Same first letter is preferred when one fits.
    """
    taken = taken if taken is not None else set()
    shown = [(opt.upper() if upper else opt) for opt in options]
    shown = [o for o in shown if not contains_real_name(o, [token])] or shown
    fits = [o for o in shown if text_width(o, size) <= width + 0.6]
    # Case-blind: "OBSIDIANA" for one company and "Obsidiana" for another
    # read as one company.
    used = {t.casefold() for t in taken}
    if not fits:
        # Last resort: the shortest unused option, still at this size.
        return min([o for o in shown if o.casefold() not in used] or shown, key=len)
    # Real run 2026-10-21: every fitting option was taken, so two companies
    # got the same fake ("IPE"). An unused wider option is better: the PDF
    # writer shrinks it a little, and the two stay apart.
    wider = sorted((o for o in shown if o.casefold() not in used), key=lambda o: text_width(o, size))
    free = [o for o in fits if o.casefold() not in used] or wider[:1] or blend_fakes(shown, used, token)
    initial = token[:1].upper()
    same = [o for o in free if o[:1].upper() == initial]
    pool = same or free
    # Longest fitting words first, so the line keeps its look.
    pool = sorted(pool, key=lambda o: (-len(o), o))
    longest = [o for o in pool if len(o) == len(pool[0])]
    return longest[seed_index(token or options[0], len(longest))]


def choose_fake(token: str, kind: str, width: float, size: float, taken: set | None = None) -> str:
    upper = token.isupper() and token.isalpha()
    if kind == "email":
        local = "a" + str(seed_index(token, 90) + 10)
        fake = f"{local}@example.com"
        if text_width(fake, size) <= width + 0.6:
            return fake
        return "a@example.com"
    if kind == "ip":
        for fake in ("203.0.113.10", "203.0.113.4", "198.51.100.8"):
            if text_width(fake, size) <= width + 0.6:
                return fake
        return "203.0.113.4"
    if kind == "host":
        return "mail.example"
    if kind == "url":
        return "https://example.com"
    if kind in {"cpf", "cnpj", "rg", "cep", "phone"}:
        # Keep letters such as "CNPJ" and the punctuation. Change every digit.
        masked = mask_shape(token)
        return "".join(m if c.isdigit() else c for c, m in zip(token, masked))
    if kind in {"uuid", "hex", "number"}:
        fake = mask_shape(token)
        if kind == "uuid" and text_width(fake, size) > width + 0.6:
            short = keyed(token)[:8]
            fake = short if text_width(short, size) <= width + 0.6 else short[:4]
        if kind == "hex":
            # A full hash is wider than the line in Helvetica. Write a short marker.
            fake = "0" * 12
            while text_width(fake, size) > width + 0.6 and len(fake) > 4:
                fake = fake[:-1]
        if text_width(fake, size) > width + 0.6 and kind != "hex":
            # Digits of the same shape can be slightly wider than the source font.
            # Shrink is allowed only here, and the caller applies it.
            return fake
        return fake
    if kind == "org":
        return fit_choice(ORGS, width, size, upper or token.isupper(), token, taken)
    if kind == "place":
        return fit_choice(PLACES, width, size, False, token, taken)
    if kind == "last":
        return fit_choice(LASTS, width, size, upper, token, taken)
    return fit_choice(firsts_for(token), width, size, upper, token, taken)


# Real first-name word (accent-free, casefold) -> "m" or "f", learned from the
# documents by learn_genders. Empty means any first-name fake fits.
GENDERS: dict[str, str] = {}
MALE_TITLES = r"Mr|Sr|Senhor|Dr|Doutor"
FEMALE_TITLES = r"Ms|Mrs|Miss|Sra|Srta|Senhora|Dra|Doutora|Dona"
TITLE_NAME_RE = re.compile(
    r"\b(?:(" + MALE_TITLES + r")|(" + FEMALE_TITLES + r"))\b\.?\s+([A-ZÀ-Þ][^\W\d_]+)", re.I)
# "MARIA ROSA, brasileira, casada" and "Joao Silva, Brazilian, married":
# the words after a name in a deed agree with the person's gender.
INTRO_RE = re.compile(
    r"\b([A-ZÀ-Þ][^\W\d_]+)(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Þ][^\W\d_]+){0,5},\s+"
    r"(brasileir[oa]|dinamarqu[eê]sa?|portugues[a]?|casad[oa]|solteir[oa]|divorciad[oa])\b")
FEMALE_INTRO = {"brasileira", "dinamarquesa", "dinamarquêsa", "portuguesa", "casada", "solteira", "divorciada"}
# Email signatures: "Ana Lima | Lawyer (She/Her)", "Ana Lima, ela/dela,
# Advogada". Real case 2026-10-31: a woman whose only clue was "(She/Her)"
# got the male fake Gabriel. The clue sits on the name's line or starts the next one.
SIGNATURE_RE = re.compile(
    r"\b([A-ZÀ-Þ][^\W\d_]+)(?:[ \t]+(?:d[aeo]s?[ \t]+)?[A-ZÀ-Þ][^\W\d_]+){0,4}"
    r"(?:[ \t]*[|,\-–]?[^\n|]{0,25}?|[ \t]*\n[ \t]*)"
    r"\(?\b(he/him|she/her|ele/dele|ela/dela|advogad[oa])\b", re.I)
FEMALE_SIGNATURE = {"she/her", "ela/dela", "advogada"}
# Sign-offs and greetings agree with the writer or the reader:
# "Obrigada, Ana", "Prezado Joao", "Cara Ana".
GREETING_RE = re.compile(r"\b(Obrigad|Prezad|Car)([oa])[,!.]?\s+([A-ZÀ-Þ][^\W\d_]+)")


def learn_genders(texts: list[str]) -> None:
    """Read each person's gender from the titles before their name (Mr.,
    Sra.), the agreeing words after it in a deed (brasileiro/brasileira),
    pronouns and job words in a signature (She/Her, Advogado), and
    greetings and sign-offs (Prezada, Obrigado).
    A word with as much evidence for both stays open."""
    from collections import Counter
    votes: dict[str, Counter] = {}
    for t in texts:
        for m in TITLE_NAME_RE.finditer(t):
            g = "m" if m.group(1) else "f"
            votes.setdefault(strip_accents(m.group(3)).casefold(), Counter())[g] += 1
        for m in INTRO_RE.finditer(t):
            g = "f" if m.group(2).lower() in FEMALE_INTRO else "m"
            votes.setdefault(strip_accents(m.group(1)).casefold(), Counter())[g] += 1
        for m in SIGNATURE_RE.finditer(t):
            g = "f" if m.group(2).lower() in FEMALE_SIGNATURE else "m"
            votes.setdefault(strip_accents(m.group(1)).casefold(), Counter())[g] += 1
        for m in GREETING_RE.finditer(t):
            g = "m" if m.group(2) == "o" else "f"
            votes.setdefault(strip_accents(m.group(3)).casefold(), Counter())[g] += 1
    GENDERS.clear()
    for word, c in votes.items():
        if c["m"] != c["f"]:
            GENDERS[word] = "m" if c["m"] > c["f"] else "f"


def firsts_for(token: str) -> list[str]:
    g = GENDERS.get(strip_accents(core_token(token)).casefold())
    if g == "m":
        return [f for f in FIRSTS if f not in FEMALE_FIRSTS]
    if g == "f":
        return [f for f in FIRSTS if f not in MALE_FIRSTS]
    return FIRSTS


def wrong_gender(token: str, fake: str) -> bool:
    g = GENDERS.get(strip_accents(core_token(token)).casefold())
    first = title_words(fake.split()[0]) if fake.split() else ""
    return (g == "m" and first in FEMALE_FIRSTS) or (g == "f" and first in MALE_FIRSTS)


class Hit:
    def __init__(self, token: str, kind: str, width: float, size: float):
        self.token = token
        self.kind = kind
        self.width = width
        self.size = size


POSSESSIVE_RE = re.compile(r"(?<=[^\W\d_])(?:['’]s|['’])$")


def strip_possessive(token: str) -> str:
    """Quaresma's and Quaresma’s are Quaresma. Real case 2026-10-21: the
    possessive forms got their own fakes (Karen, Beatriz) and lost the 's,
    so one man read as three people."""
    return POSSESSIVE_RE.sub("", token)


def force(hits: dict, token: str, kind: str, width: float, size: float) -> None:
    token = strip_possessive(token.strip()) if kind in NAME_KINDS else token.strip()
    if len(token) < 2 or (token.lower() in KEEP and kind == "name"):
        return
    prev = hits.get(token)
    hits[token] = Hit(token, kind, width, size if size else 11.0)
    if prev is not None:
        hits[token].width = min(prev.width, width)
        hits[token].size = max(prev.size, size or 0)


def consider(hits: dict, token: str, kind: str, width: float, size: float) -> None:
    token = strip_possessive(token.strip()) if kind in NAME_KINDS else token.strip()
    if len(token) < 2:
        return
    if token.lower() in KEEP and kind == "name":
        return
    prev = hits.get(token)
    if prev is None or width < prev.width:
        hits[token] = Hit(token, kind, width, size if size else 11.0)
        if prev is not None and size and size > prev.size:
            hits[token].size = size


def scan_text_blobs(text: str, hits: dict, size: float = 11.0) -> None:
    occupied: list[tuple[int, int]] = []
    for rx, kind in (
        (EMAIL_RE, "email"),
        (CNPJ_RE, "cnpj"),
        (CPF_RE, "cpf"),
        (UUID_RE, "uuid"),
        (HEX_RE, "hex"),
        (HOST_RE, "host"),
        (URL_RE, "url"),
    ):
        for m in rx.finditer(text):
            value = m.group(0).rstrip(".,;:")
            if kind == "url" and url_is_public(value):
                continue
            if any(s <= m.start() and m.end() <= e for s, e in occupied):
                continue
            consider(hits, value, kind, 1000.0, size)
            occupied.append(m.span())
    for rx in DOC_ID_RES:
        for m in rx.finditer(text):
            consider(hits, m.group(1), "uuid", 1000.0, size)
            occupied.append(m.span(1))
    # A street written over line breaks ("Rua \n Fulano da Costa 1419,
    # apto. 122"): its name words, house number and apartment. Real case
    # 2026-10-31: a home address stayed in a sworn translation.
    for m in STREET_RE.finditer(text):
        for w in re.findall(r"[^\W\d_]+", m.group(1)):
            if (w[:1].isupper() and len(w) >= 3 and w.lower() not in KEEP and _bare(w) not in HONORIFICS
                    and _bare(w) not in ADDRESS_WORDS and w.lower() not in CONNECTORS):
                consider(hits, w, "place", 1000.0, size)
        for g in (2, 3):
            if m.group(g) and len(m.group(g)) >= 2:
                consider(hits, m.group(g), "number", 1000.0, size)
    for rx in ID_NUMBER_RES:
        for m in rx.finditer(text):
            if any(s <= m.start(1) and m.end(1) <= e for s, e in occupied):
                continue
            consider(hits, m.group(1), "number", 1000.0, size)
            occupied.append(m.span(1))
    for m in PHONE_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in occupied):
            continue
        if len(re.sub(r"\D", "", m.group(0))) < 10:
            continue
        consider(hits, m.group(0).strip(), "phone", 1000.0, size)
        occupied.append(m.span())
    for rx, kind in ((CEP_RE, "cep"), (IP_RE, "ip"), (RG_RE, "rg")):
        for m in rx.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in occupied):
                continue
            # An RG-shaped run inside a longer id is not a separate document.
            if kind == "rg" and any(abs(m.start() - s) <= 2 or abs(m.end() - e) <= 2 for s, e in occupied):
                continue
            consider(hits, m.group(0), kind, 1000.0, size)
            # An IP is not also an RG (its first 8 digits look like one).
            occupied.append(m.span())
    # Provider host names next to an IP (static.dk.customer.tdc.net) say
    # where the signer was. Any host name within 200 characters after an IP;
    # the host often wraps onto the next line.
    anchors = [m.end() for m in IP_RE.finditer(text)] + [m.end() for m in re.finditer(r"IP:", text)]
    for anchor in sorted(set(anchors)):
        window = text[anchor:anchor + 200]
        for m in ANY_HOST_RE.finditer(window):
            host = m.group(0).rstrip(".")
            start = m.start()
            if start > 0 and window[start - 1] in "@/":
                continue
            if host.lower().startswith("example") or ".example" in host.lower() or IP_RE.fullmatch(host):
                continue
            consider(hits, host, "host", 1000.0, size)


def url_is_public(url: str) -> bool:
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
    return any(host == h or host.endswith("." + h) for h in KEEP_URL_HOSTS)


def line_is_mixed(words: list[str]) -> bool:
    has_lower = any(any(c.islower() for c in w) for w in words)
    has_upper = any(core_token(w).isupper() and core_token(w).isalpha() for w in words)
    return has_lower and has_upper


def scan_word_lines(lines: list[list[str]], hits: dict, size: float = 11.0) -> None:
    """lines are lists of raw word strings, in order."""
    for words in lines:
        raws = words
        cores = [core_token(w) for w in raws]
        mixed = line_is_mixed(raws)
        for i, core in enumerate(cores):
            if not core:
                continue
            low = core.lower().rstrip(".")
            # Street name is the next word after Rua / Avenida.
            if low in STREET_PREFIX and i + 1 < len(cores):
                nxt = cores[i + 1]
                if nxt and nxt.lower() not in KEEP and nxt[:1].isupper():
                    consider(hits, nxt, "place", 1000.0, size)
            # Person after Sr. / Mr.
            if low in TITLE_PREFIX and i + 1 < len(cores):
                for j in range(i + 1, min(i + 3, len(cores))):
                    nxt = cores[j]
                    if nxt.lower() in CONNECTORS:
                        continue
                    if nxt[:1].isupper() and nxt.lower() not in KEEP:
                        kind = "last" if j > i + 1 else "name"
                        consider(hits, nxt, kind, 1000.0, size)
            # Parenthetical brand: (ONovolab)
            if raws[i].startswith("(") and raws[i].endswith(")") and len(core) >= 4:
                if core.lower() not in KEEP and any(c.isupper() for c in core):
                    force(hits, core, "org", 1000.0, size)
            # Company tokens before LTDA. Skip role words such as BRASIL.
            if low in {"ltda", "llc", "inc", "s.a", "sa"} and i > 0:
                grabbed = 0
                j = i - 1
                while j >= 0 and grabbed < 2:
                    prev = cores[j]
                    j -= 1
                    if not prev or prev.lower() in KEEP or prev.lower() in CONNECTORS:
                        continue
                    if prev.isupper() and prev.isalpha() and len(prev) >= 4:
                        force(hits, prev, "org", 1000.0, size)
                        grabbed += 1
            # All-caps name inside a sentence. Headings are a whole line of caps, so they are skipped.
            if mixed and core.isupper() and core.isalpha() and len(core) >= 4 and core.lower() not in KEEP:
                consider(hits, core, "name", 1000.0, size)
        # Title-case pair only when the uppercase form of that word is also a token we already flagged,
        # or when both words are not normal English/Portuguese prose. Applied in a second step below.

    flagged_upper = {t.upper() for t in hits if hits[t].kind in {"name", "last", "org"}}
    for words in lines:
        cores = [core_token(w) for w in words]
        for i, core in enumerate(cores):
            if i + 1 >= len(cores):
                break
            a, b = core, cores[i + 1]
            if not a or not b:
                continue
            if a.lower() in KEEP or b.lower() in KEEP or a.lower() in CONNECTORS:
                continue
            # "First Last": title case, and the all-caps form appears, or both look like names
            # (no vowel-only check). Require the pair to be mid-line (not a heading).
            def titleish(s: str) -> bool:
                return len(s) >= 3 and s[0].isupper() and s[1:].islower() and s.isalpha()
            if titleish(a) and titleish(b):
                if a.upper() in flagged_upper or b.upper() in flagged_upper:
                    consider(hits, a, "name", 1000.0, size)
                    consider(hits, b, "last", 1000.0, size)
    # Street names often fall on the next extractor line after "Avenida" or "Rua".
    flat = [core_token(w) for line in lines for w in line]
    for i, core in enumerate(flat):
        if core.lower().rstrip(".") not in STREET_PREFIX:
            continue
        for nxt in flat[i + 1:i + 4]:
            if not nxt or nxt.lower() in KEEP or nxt.lower() in STREET_PREFIX:
                continue
            if nxt[:1].isupper() and nxt.isalpha() and len(nxt) >= 4:
                consider(hits, nxt, "place", 1000.0, size)
                break


NAME_KINDS = {"name", "last", "org", "place"}
WORDS_RE = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")


def title_words(text: str) -> str:
    return " ".join(
        w.lower() if w.lower() in CONNECTORS else w[:1].upper() + w[1:].lower()
        for w in text.split(" ")
    )


def same_case(fake: str, like: str) -> str:
    """Write the fake in the case of `like`: FAKE, fake, or Fake Name."""
    if not fake[:1].isalpha():
        return fake
    if like.isupper():
        return fake.upper()
    if like.islower():
        return fake.lower()
    if like == title_words(like):
        return title_words(fake)
    return fake


# Kept inside a full company or person name (São Carlos, ACME S/A).
LEGAL_FORMS = {"são", "sao", "ltda", "ltda.", "s/a", "s.a", "s.a.", "sa", "me", "epp", "eireli", "slu", "ss", "a/s", "aps", "gmbh"}


def one_edit_apart(a: str, b: str) -> bool:
    """True when b is a with one letter changed, added, or removed."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def merge_spelling_variants(families: dict[str, list[str]]) -> None:
    """One company written two ways in the same documents (a typo, or a
    variant spelling) is one company. Real run 2026-10-21: an email had the
    company name in two spellings one letter apart, and the copy showed it
    as two different fake companies. Merged only for company words of 6+
    letters, same first 3 letters, exactly one edit apart. Never for people:
    Fernanda and Fernando are two people."""
    keys = sorted(families)
    for i, a in enumerate(keys):
        if a not in families:
            continue
        kind_a, word_a = a.split(":", 1)
        for b in keys[i + 1:]:
            if b not in families:
                continue
            kind_b, word_b = b.split(":", 1)
            # Companies only: Fernanda and Fernando are two people.
            if kind_a != "org" or kind_b != "org" or min(len(word_a), len(word_b)) < 6 or word_a[:3] != word_b[:3]:
                continue
            if one_edit_apart(word_a, word_b):
                families[a].extend(families.pop(b))


def person_evidence_words() -> set[str]:
    """Bare words a detector read as part of a person: the local model,
    GLiNER, email headers, author fields. The capitals rule is not evidence."""
    words = {_bare(w) for t in MODEL_PERSON_TOKENS for w in re.findall(r"[^\W\d_]+", t)}
    return words | GLINER_PERSON_WORDS | {_bare(w) for w in PEOPLE_FIELD_WORDS}


def merge_person_company_words(families: dict[str, list[str]]) -> None:
    """A word found as a person and as a company word is one entity, and it
    takes a company fake. Real run 2026-10-21: the same surname was "Leandro"
    as a person and "KAPPA" inside a law firm's name; and the counterparty
    company, flagged by the capitals rule as a person, became "Irene". A
    partner of a firm named after them reads as a company name; a company
    with a woman's name misleads the reader more."""
    evidence = person_evidence_words()
    for key in list(families):
        kind, word = key.split(":", 1)
        if kind != "name" or "org:" + word not in families:
            continue
        if _bare(word) in evidence:
            # A detector read it as a person (a firm named after its
            # partners): one person fake for the person and the firm word.
            families[key].extend(families.pop("org:" + word))
        else:
            families["org:" + word].extend(families.pop(key))


def family_key(token: str, kind: str) -> str:
    if kind == "ip":
        return "ip:" + re.sub(r"\D", "", token)
    # Case- and accent-blind: certificate logs write JOSE for José.
    return kind.replace("last", "name") + ":" + strip_accents(token).casefold()


# Tokens that are only replaced as whole words: case variants. "Clara" the
# person must not turn "declara" into "degabriel".
WHOLE_WORD_ONLY: set[str] = set()
# Strings the local model returned (and their words). Case variants are made
# only for these, not for words the caps scan guessed (CONTRATADA).
MODEL_TOKENS: set[str] = set()
MODEL_PERSON_TOKENS: set[str] = set()
# Names from places that only hold people: email From/To/Cc display names
# and Word author fields. Their words are never dropped as ordinary words.
PEOPLE_FIELD_WORDS: set[str] = set()


def add_phrase_words(hits: dict) -> int:
    """Every distinctive word of a multi-word name, company or place becomes
    a hit of its own. The PDF writer replaces one word box at a time, so a
    phrase found only as a whole (by GLiNER, or as a header display name)
    was never replaced in a PDF: real run 2026-10-31, four company names
    and a person stayed in attached PDFs. The words count as vetted, so
    their case variants are made too."""
    n = 0
    for token in list(hits):
        hit = hits[token]
        if hit.kind not in NAME_KINDS or " " not in token.strip():
            continue
        for i, w in enumerate(token.split()):
            core = core_token(w)
            if (len(core) < 3 or not core.isalpha() or core in hits or core.lower() in KEEP
                    or kept_in_name(core, hit.kind) or _bare(core) in PUBLIC_TERMS):
                continue
            kind = "last" if hit.kind in {"name", "last"} and i > 0 else hit.kind
            hits[core] = Hit(core, kind, 1000.0, hit.size)
            MODEL_TOKENS.add(core)
            n += 1
    return n


def add_variants(hits: dict) -> None:
    """A name the local model found as MARIA also appears as Maria; an IP
    found as 1.2.3.4 also appears as 1-2-3-4 inside a provider host name.
    No lowercase variant: lowercase names are mostly ordinary words."""
    for token, hit in list(hits.items()):
        variants = []
        vetted = (token in MODEL_TOKENS or token in PEOPLE_FIELD_WORDS
                  or _bare(token) in GLINER_PERSON_WORDS or _bare(token) in GLINER_ORG_WORDS)
        if hit.kind in NAME_KINDS and vetted and WORDS_RE.fullmatch(token):
            variants = [token.upper(), title_words(token)]
        elif hit.kind == "ip":
            variants = [token.replace(".", "-")]
        for v in variants:
            if v == token or v in hits or v.lower() in KEEP or v.lower() in CONNECTORS:
                continue
            hits[v] = Hit(v, hit.kind, hit.width, hit.size)
            if hit.kind != "ip":
                WHOLE_WORD_ONLY.add(v)


def usable_pins(pins: dict, kinds: dict) -> dict:
    """The fakes of an earlier run that pass today's map checks. When one
    fake stood for several people, the person with the most forms keeps it
    and the others get new fakes. Owner 2026-10-21: a rerun must keep the
    fakes the lawyer already read and fix only what is wrong."""
    def fold_(x: str) -> str:
        return strip_accents(x).casefold()

    # Review of #1334: a pin from an earlier run can be a real name that a
    # new document brought into the case ("Eva", "IPE"). Every real value
    # and every real word of this run is checked, not only the token's own.
    real_values = {fold_(t) for t in kinds}
    real_words = {fold_(core_token(w)) for t in kinds for w in t.split() if core_token(w)}

    def clashes(token: str, fake: str) -> bool:
        if fold_(fake) in real_values:
            return True
        own = {fold_(core_token(w)) for w in token.split()}
        return any(fold_(core_token(w)) in real_words and fold_(core_token(w)) not in own
                   for w in fake.split())

    pins = {t: f for t, f in pins.items()
            if isinstance(t, str) and isinstance(f, str) and t in kinds and f
            and fold_(f) != fold_(t) and not clashes(t, f) and not contains_real_name(f, [t])
            and not (kinds.get(t) == "name" and wrong_gender(t, f))}
    groups = entity_groups(pins, kinds)
    by_fake: dict[str, dict[str, list[str]]] = {}
    for t, f in pins.items():
        if t in groups and strip_accents(f).casefold() not in SHARED_ON_PURPOSE:
            by_fake.setdefault(strip_accents(f).casefold(), {}).setdefault(groups[t], []).append(t)
    for owners in by_fake.values():
        if len(owners) < 2:
            continue
        keep = max(owners, key=lambda g: (len(owners[g]), keyed(g)))
        for g, tokens in owners.items():
            if g != keep:
                for t in tokens:
                    pins.pop(t, None)
    # Every other map mistake (two fakes, a broken company form) is rebuilt.
    for t in check_map(pins, {t: kinds[t] for t in pins}):
        pins.pop(t, None)
    return pins


def known_fake(by_family: dict, word: str, kind: str) -> str | None:
    """The fake a word already has, as the same kind or as the other of
    person and company: "Monforte Advogados" takes the fake of the person
    "Monforte". Plan run 2026-10-21: the firm showed one fake and the
    partner another."""
    bare = strip_accents(word).casefold()
    for key in (family_key(word, kind), "name:" + bare, "org:" + bare):
        if key in by_family:
            return by_family[key]
    return None


def build_map(hits: dict, pins: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Return (real -> fake, real -> kind). `pins` are the fakes of an
    earlier run of the same case; those that pass the map checks are kept."""
    mapping: dict[str, str] = {}
    kinds = {token: hit.kind for token, hit in hits.items()}
    REAL_NAME_FOLDS.clear()
    REAL_NAME_FOLDS.update(real_name_folds(kinds))
    if pins:
        mapping.update(usable_pins(pins, kinds))
    # A fake must never equal a real word from this file, or the leak check
    # would see it, and a reader could not tell the two apart.
    taken = set(hits) | {t.upper() for t in hits} | set(mapping.values())
    # Also the accent-free forms: the fake "Fabio" for the real "Fábio" would
    # read as the real name to a person and to the accent-blind check.
    for t in list(taken):
        bare = strip_accents(t)
        taken |= {bare, bare.upper(), title_words(bare) if WORDS_RE.fullmatch(bare) else bare}
    # One fake per word family (MARIA, Maria): chosen so that, written in each
    # member's case, it fits every member's narrowest box. Before this, the
    # caps form got a different fake whenever the first pick did not fit, so
    # one signer showed two names on the same page.
    families: dict[str, list[str]] = {}
    for token, hit in hits.items():
        if hit.kind in NAME_KINDS and " " not in token.strip():
            families.setdefault(family_key(token, hit.kind), []).append(token)
    merge_spelling_variants(families)
    merge_person_company_words(families)
    for key in sorted(families, key=keyed):
        members = families[key]
        # Only a pin from a member of the family's own kind counts: an old
        # company fake ("GRANITO", from a firm's name in capitals) must not
        # name a person.
        want = {"org"} if key.startswith("org:") else {"place"} if key.startswith("place:") else {"name", "last"}
        pinned = [m for m in members if m in mapping and hits[m].kind in want]
        for m in members:
            if m in mapping and m not in pinned and pinned == []:
                del mapping[m]
        if pinned:
            # An earlier run's fake for this family: every member uses it.
            for m in members:
                if m not in mapping:
                    mapping[m] = same_case(mapping[pinned[0]], m)
                    taken.add(mapping[m])
            continue
        if len(members) < 2:
            continue
        member_kinds = {hits[m].kind for m in members}
        pool = (ORGS if key.startswith("org:") else PLACES if key.startswith("place:")
                else LASTS if "last" in member_kinds else FIRSTS)
        start = seed_index(key, len(pool))
        initial = members[0][:1].upper()
        ordered = pool[start:] + pool[:start]
        ordered = [o for o in ordered if o[:1].upper() == initial] + [o for o in ordered if o[:1].upper() != initial]
        # The option that fits the most members wins (longest on a tie).
        best, best_fit = None, []
        for option in ordered:
            shown = {m: same_case(option, m) for m in members}
            if option.casefold() in {t.casefold() for t in taken} or contains_real_name(option, members):
                continue
            fit = [m for m in members if text_width(shown[m], hits[m].size) <= hits[m].width + 0.6]
            if len(fit) > len(best_fit) or (len(fit) == len(best_fit) and best and len(option) > len(best)):
                best, best_fit = option, fit
            if len(fit) == len(members):
                break
        if best is None:
            used = {t.casefold() for t in taken}
            free = [o for o in ordered if o.casefold() not in used and not contains_real_name(o, members)]
            best = min(free, key=lambda o: text_width(o.upper(), 11.0)) if free else None
        if best:
            # Every member gets the family's fake, also a member whose box it
            # does not fit: the PDF writer shrinks it a little there. One
            # person with two names is the worse mistake (Karen and Nina,
            # 2026-10-21).
            for m in members:
                mapping[m] = same_case(best, m)
                taken.add(mapping[m])
    for token in sorted(hits, key=lambda t: (hits[t].kind, keyed(t))):
        if token in mapping:
            continue
        hit = hits[token]
        if hit.kind in NAME_KINDS and " " in token.strip():
            continue  # full names are built from their words below
        fake = choose_fake(token, hit.kind, hit.width, hit.size, taken)
        mapping[token] = fake
        taken.add(fake)
    # A full name gets the fakes of its words, so "Maria Souza" and "SOUZA"
    # stay the same person in the copy. The lookup is case- and accent-blind:
    # "Pirilampo Brasil" takes the fake of "PIRILAMPO". Real run 2026-10-21:
    # an exact-case lookup gave one company the forms "Alfa Lumen" and
    # "ALFA NORTE".
    by_family: dict[str, str] = {}
    for token in sorted(mapping, key=keyed):
        if kinds.get(token) in NAME_KINDS and " " not in token.strip():
            by_family.setdefault(family_key(token, kinds[token]), mapping[token])
    # A full name kept from an earlier run is rebuilt when one of its words
    # now has another fake, so "GRANITO LESTE" follows its words.
    for token in list(mapping):
        if kinds.get(token) not in NAME_KINDS or " " not in token.strip():
            continue
        real_words, fake_words = token.split(), mapping[token].split()
        stale = len(real_words) != len(fake_words)
        for r, f in zip(real_words, fake_words):
            known = known_fake(by_family, core_token(r), kinds[token])
            if known and not kept_in_name(core_token(r), kinds[token]) \
                    and strip_accents(core_token(f)).casefold() != strip_accents(known).casefold():
                stale = True
        if stale:
            del mapping[token]
    for token, hit in hits.items():
        if token in mapping:
            continue
        words = token.split()
        built = []
        for w in words:
            core = core_token(w)
            # Inside a person's or company's name only connectors stay. "Carlos"
            # is kept alone (São Carlos) but not in "Carlos Eduardo Nogueira".
            if core.lower() in CONNECTORS or core.lower() in LEGAL_FORMS or (
                    _bare(core) in CASE_KEEP and hit.kind in {"org", "place"}):
                built.append(w)
            elif hit.kind == "org" and is_generic_org_word(core):
                built.append(w)  # Brasil, Ltda, Consultoria stay inside a company name
            elif hit.kind == "place" and (_bare(core) in ADDRESS_WORDS or core.lower() in KEEP
                                          or _bare(core) in GENERIC_ORG_WORDS):
                built.append(w)  # Rua, sala, São Paulo stay inside an address
            elif hit.kind in {"name", "last"} and _bare(core) in HONORIFICS:
                built.append(w)  # Dra., Sr. stay before a name
            elif any(ch.isdigit() for ch in core):
                built.append(w.replace(core, mask_shape(core)))  # 555 in an address
            elif core in mapping:
                built.append(w.replace(core, mapping[core]))
            elif known_fake(by_family, core, hit.kind):
                built.append(w.replace(core, same_case(known_fake(by_family, core, hit.kind), core)))
            else:
                fake = same_case(choose_fake(core, hit.kind, hit.width, hit.size, taken), core)
                by_family[family_key(core, hit.kind)] = fake
                built.append(fake)
        if " ".join(built) == token:
            # Every word is one the builder keeps (a public body such as
            # "National Mining Agency"): nothing identifies anyone, and a
            # "fake" equal to the real value fails every check.
            del kinds[token]
            continue
        mapping[token] = " ".join(built)
        taken.add(mapping[token])
    # Domain labels from emails also get replaced when they appear as words.
    extra = {}
    for token in list(mapping):
        if "@" in token:
            local, _, domain = token.partition("@")
            label = domain.split(".")[0]
            if label and label.lower() not in KEEP and len(label) >= 4 and not label.lower().startswith("example"):
                extra[label] = ("example", "org")
                if label[:1].isupper():
                    extra[label.capitalize()] = ("Example", "org")
            if local and len(local) >= 3 and local.isalpha():
                extra[local] = ("ana", "name")
    # The same word elsewhere already has a fake (joao@ and the person Joao):
    # reuse it so one person keeps one name.
    by_fold = {}
    for real, fake in mapping.items():
        if " " not in real and real.isalpha():
            by_fold.setdefault(real.casefold(), fake)
    for token, (fake, kind) in extra.items():
        if token not in mapping:
            known = by_fold.get(token.casefold())
            mapping[token] = same_case(known, token) if known else fake
            kinds[token] = kind
    return mapping, kinds


def kept_in_name(word: str, kind: str) -> bool:
    """Words the builder keeps unchanged inside a full name: connectors,
    legal forms, generic company words, address words, titles, and words a
    person kept by hand for this case."""
    core = core_token(word)
    b = _bare(core)
    if b in CASE_KEEP and kind in {"org", "place"}:
        # Review of #1334: "Santos" kept for the port city must not stay
        # inside the person "Clara Santos".
        return True
    if core.lower() in CONNECTORS or core.lower() in LEGAL_FORMS:
        return True
    if kind == "org" and is_generic_org_word(core):
        return True
    if kind == "place" and (b in ADDRESS_WORDS or core.lower() in KEEP or b in GENERIC_ORG_WORDS):
        return True
    return kind in {"name", "last"} and b in HONORIFICS


def real_name_folds(kinds: dict) -> set[str]:
    """Distinctive real words of 4+ letters, accent- and case-blind. A
    token's own 3-letter name is checked separately (contains_real_name)."""
    out = set()
    for token, kind in kinds.items():
        if kind not in NAME_KINDS:
            continue
        for w in token.split():
            core = core_token(w)
            if len(core) >= 4 and core.isalpha() and core.lower() not in KEEP and not kept_in_name(core, kind):
                out.add(strip_accents(core).casefold())
    return out


# Fakes shared on purpose: every email domain becomes "example", and a bare
# mailbox word ("contato") becomes "ana".
SHARED_ON_PURPOSE = {"example", "ana"}


def entity_groups(mapping: dict, kinds: dict) -> dict[str, str]:
    """Real token -> group: one group per real person, company, or place.
    Case and accents do not matter (MARIA, Maria), company spellings one
    letter apart are one company, and a full name joins the group of its
    first distinctive word ("Alfa Brasil Ltda" joins "ALFA")."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    group = {}
    for t in mapping:
        if kinds.get(t) in NAME_KINDS and " " not in t.strip():
            group[t] = family_key(t, kinds[t])
            find(group[t])
    for k in list(parent):
        if k.startswith("name:") and "org:" + k[5:] in parent:
            parent[find(k)] = find("org:" + k[5:])
    orgs = sorted(k for k in parent if k.startswith("org:"))
    for i, a in enumerate(orgs):
        wa = a.split(":", 1)[1]
        for b in orgs[i + 1:]:
            wb = b.split(":", 1)[1]
            if min(len(wa), len(wb)) >= 6 and wa[:3] == wb[:3] and one_edit_apart(wa, wb):
                parent[find(b)] = find(a)
    for t in mapping:
        if t in group or kinds.get(t) not in NAME_KINDS:
            continue
        for w in t.split():
            core = core_token(w)
            if core and core.isalpha() and not kept_in_name(core, kinds[t]):
                group[t] = family_key(core, kinds[t])
                find(group[t])
                break
    return {t: find(k) for t, k in group.items()}


MAP_PROBLEM_TEXT = {
    "fake_has_real": "the fake contains a real name",
    "shared_fake": "one fake stands for two different people or companies",
    "two_fakes": "one person or company has two different fakes",
    "company_form": "a company or address lost a word that should stay (Brasil, Ltda, Rua)",
}


def check_map(mapping: dict, kinds: dict) -> dict[str, str]:
    """Real token -> problem, for the four map mistakes the owner found by
    hand on 2026-10-21: "Helena" for the real "Helen"; "IPE" for two
    companies; one signer as "Karen" and "Nina"; one company as "Alfa
    Lumen", "ALFA NORTE", and "Alfa IPE". The tokens are never printed."""
    problems: dict[str, str] = {}
    folds = real_name_folds(kinds)
    group = entity_groups(mapping, kinds)

    def f(text: str) -> str:
        return strip_accents(text).casefold().strip()

    for token, fake in mapping.items():
        kind = kinds.get(token)
        if kind not in NAME_KINDS:
            continue
        if f(fake) == f(token):
            problems[token] = "fake_has_real"
            continue
        own = {f(core_token(w)) for w in token.split()}
        for fw in fake.split():
            word = f(core_token(fw))
            if word and word not in own and (any(real in word for real in folds)
                                             or any(len(o) >= 3 and o in word for o in own)):
                problems[token] = "fake_has_real"
                break
    groups_of_fake: dict[str, set] = {}
    fakes_of_group: dict[str, set] = {}
    groups_root = {family_key(t, kinds[t]): g for t, g in group.items() if " " not in t.strip()}
    for token, fake in mapping.items():
        if token not in group or f(fake) in SHARED_ON_PURPOSE:
            continue
        groups_of_fake.setdefault(f(fake), set()).add(group[token])
        if " " not in token.strip():
            fakes_of_group.setdefault(group[token], set()).add(f(fake))
    for token, fake in mapping.items():
        if token in problems or token not in group or f(fake) in SHARED_ON_PURPOSE:
            continue
        if len(groups_of_fake.get(f(fake), ())) > 1:
            problems[token] = "shared_fake"
        elif " " not in token.strip() and len(fakes_of_group.get(group[token], ())) > 1:
            problems[token] = "two_fakes"
    for token, fake in mapping.items():
        kind = kinds.get(token)
        if token in problems or kind not in NAME_KINDS or " " not in token.strip():
            continue
        real_words, fake_words = token.split(), fake.split()
        if len(real_words) != len(fake_words):
            problems[token] = "company_form"
            continue
        for r, w in zip(real_words, fake_words):
            core = core_token(r)
            if kept_in_name(core, kind) and r != w:
                problems[token] = "company_form"
                break
            if not core.isalpha() or kept_in_name(core, kind):
                continue
            bare = strip_accents(core).casefold()
            known = (fakes_of_group.get(groups_root.get(family_key(core, kind)))
                     or fakes_of_group.get(groups_root.get("name:" + bare))
                     or fakes_of_group.get(groups_root.get("org:" + bare)))
            if known and f(core_token(w)) not in known:
                problems[token] = "two_fakes"
                break
    return problems


def label_order(label: str) -> tuple:
    prefix, _, n = label.rpartition("_")
    return (prefix, int(n) if n.isdigit() else 0)


def print_map_checks(mapping: dict, kinds: dict, labels: dict) -> dict[str, str]:
    """GROUP lines: every form each real person or company takes in the
    copies, fakes only, so the owner can see that "Joana" and "Eva" are two
    people and that "Alfa", "Alfa Brasil", "ALFA A/S" are one company
    without opening the map. Then one MAP_CHECK line with the counts."""
    group = entity_groups(mapping, kinds)
    members: dict[str, list[str]] = {}
    for token, g in group.items():
        members.setdefault(g, []).append(token)
    lines = []
    for tokens in members.values():
        fakes = sorted({mapping[t] for t in tokens}, key=lambda x: (len(x), x))
        if len(fakes) >= 2:
            first = min((labels[t] for t in tokens), key=label_order)
            lines.append((label_order(first), f"GROUP {first}: " + " | ".join(fakes)))
    print(f"GROUPS {len(lines)} people or companies written in more than one form")
    for _key, line in sorted(lines):
        print(line)
    problems = check_map(mapping, kinds)
    counts = {k: sum(1 for v in problems.values() if v == k) for k in MAP_PROBLEM_TEXT}
    print("MAP_CHECK " + ", ".join(f"{k} {n}" for k, n in counts.items()))
    return problems


MAP_PROBLEMS: dict[str, str] = {}


LABEL_KIND = {
    "name": "PERSON", "last": "PERSON", "org": "ORG", "place": "PLACE",
    "email": "EMAIL", "cpf": "CPF", "cnpj": "CNPJ", "rg": "RG", "cep": "CEP",
    "ip": "IP", "uuid": "ID", "hex": "HASH", "host": "HOST", "number": "NUMBER",
    "phone": "PHONE", "url": "URL",
}


def make_labels(kinds: dict[str, str]) -> dict[str, str]:
    """PERSON_1, ORG_2 ... Stable order, no real value in the label."""
    counters: dict[str, int] = {}
    labels = {}
    for token in sorted(kinds, key=lambda t: (LABEL_KIND.get(kinds[t], "OTHER"), keyed(t))):
        prefix = LABEL_KIND.get(kinds[token], "OTHER")
        counters[prefix] = counters.get(prefix, 0) + 1
        labels[token] = f"{prefix}_{counters[prefix]}"
    return labels


def safe_name(token: str, labels: dict[str, str]) -> str:
    return f"{labels.get(token, 'VALUE')} ({short_hash(token)})"


_COPY_IDS: dict = {}


def copy_id_for_dst(dst: Path) -> str:
    return _COPY_IDS.get(str(dst)) or keyed("output:" + str(dst))[:8]


def save_map(dst: Path, mapping: dict, kinds: dict, labels: dict) -> Path:
    """Owner-only file outside the project. Agents must not read it."""
    folder = anon_home() / "maps"
    folder.mkdir(exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / (copy_id_for_dst(dst) + ".json")
    rows = {
        labels[t]: {"real": t, "fake": mapping[t], "kind": kinds.get(t, "")}
        for t in sorted(mapping, key=lambda t: labels[t])
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"output": str(dst), "map": rows}, fh, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)
    return path


def tighten_widths_from_pdf(doc, mapping_kinds: dict, keep_missing: bool = True) -> dict:
    """Replace the placeholder width with the narrowest real box."""
    import fitz
    hits = {}
    for page in doc:
        words = page.get_text("words")
        size_cache = span_sizes(page)
        buried_tokens = [t for t in mapping_kinds
                         if (len(t) >= 5 or not t.isalpha()) and t not in WHOLE_WORD_ONLY]
        for w in words:
            raw = w[4]
            core = core_token(raw)
            rect = fitz.Rect(w[0], w[1], w[2], w[3])
            size = size_cache.get(id_of_word(w), 11.0)
            if core in mapping_kinds or raw in mapping_kinds:
                key = core if core in mapping_kinds else raw
                consider(hits, key, mapping_kinds[key], rect.width, size)
                continue
            # A value inside a longer word (Email:x@y, NAME-NAME) takes the whole
            # word's box when it is written, so that box sets its width too.
            # Real run 2026-10-21: two 5-letter names inside longer words got an
            # 8-letter fake that fit none of their 66 boxes.
            for t in buried_tokens:
                if t in raw and not inside_longer_word(t, raw):
                    consider(hits, t, mapping_kinds[t], rect.width, size)
        # Multi-character secrets that are one word already covered.
        # Secrets split by the extractor still show up as their own word
        # (emails, cpf). Also search the page text for ones that are a single rect.
        text = page.get_text()
        for token, kind in mapping_kinds.items():
            if kind not in {"email", "cpf", "cnpj", "rg", "cep", "uuid", "hex", "ip", "host", "phone", "url"}:
                continue
            for rect in page.search_for(token):
                size = 9.0 if kind == "email" else 11.0
                consider(hits, token, kind, rect.width, size)
    if not keep_missing:
        return hits
    # Keep tokens we detected even if a later search missed the box.
    for token, kind in mapping_kinds.items():
        if token not in hits:
            consider(hits, token, kind, 80.0, 11.0)
    return hits


def id_of_word(w) -> tuple:
    return (round(w[0], 1), round(w[1], 1), w[4])


def span_sizes(page) -> dict:
    sizes = {}
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size") or 11)
                # Map each word-sized piece by origin is enough; we look up by
                # walking words separately. Store span bbox -> size.
                bbox = tuple(round(v, 1) for v in span["bbox"])
                sizes[bbox] = size
    return sizes


def size_for_rect(page, rect, fallback: float) -> float:
    data = page.get_text("dict")
    best = fallback
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sb = span["bbox"]
                import fitz
                if fitz.Rect(sb).intersects(rect):
                    best = float(span.get("size") or best)
                    return best
    return best


def pdf_lines(doc) -> list[list[str]]:
    lines = []
    for page in doc:
        grouped: dict[tuple, list] = {}
        for w in page.get_text("words"):
            grouped.setdefault((w[5], w[6]), []).append(w)
        for key in sorted(grouped):
            ws = sorted(grouped[key], key=lambda w: w[0])
            lines.append([w[4] for w in ws])
    return lines


# Built-in PDF fonts by (bold, italic, family).
BASE14 = {
    (False, False, "sans"): "helv", (True, False, "sans"): "hebo",
    (False, True, "sans"): "heit", (True, True, "sans"): "hebi",
    (False, False, "serif"): "tiro", (True, False, "serif"): "tibo",
    (False, True, "serif"): "tiit", (True, True, "serif"): "tibi",
    (False, False, "mono"): "cour", (True, False, "mono"): "cobo",
    (False, True, "mono"): "coit", (True, True, "mono"): "cobi",
}


def span_is_bold(span: dict) -> bool:
    return bool(span.get("flags", 0) & 16) or re.search(
        r"bold|black|heavy|semibold|demi", span.get("font", ""), re.I) is not None


def span_font(span: dict) -> str:
    name = span.get("font", "")
    flags = span.get("flags", 0)
    italic = bool(flags & 2) or re.search(r"italic|oblique", name, re.I) is not None
    if flags & 8 or re.search(r"courier|mono|consol", name, re.I):
        family = "mono"
    elif (flags & 4 or re.search(r"times|georgia|garamond|cambria|palatino|serif", name, re.I)) \
            and not re.search(r"sans", name, re.I):
        family = "serif"
    else:
        family = "sans"
    return BASE14[(span_is_bold(span), italic, family)]


def span_rgb(span: dict) -> tuple[float, float, float]:
    c = int(span.get("color", 0))
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


def page_spans(page) -> list[dict]:
    return [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", [])
            for s in l["spans"] if s["text"].strip()]


def style_at(spans: list[dict], rect) -> tuple[str, tuple[float, float, float]]:
    """Font and color of the original text under this box. Real run
    2026-10-21: a red bold heading "<company> BRAZIL" came out as a black
    regular fake next to a red bold "BRAZIL"."""
    import fitz
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    best, best_area = None, 0.0
    for s in spans:
        box = fitz.Rect(s["bbox"])
        if box.contains(center):
            return span_font(s), span_rgb(s)
        area = abs(box & rect)
        if area > best_area:
            best, best_area = s, area
    if best is not None:
        return span_font(best), span_rgb(best)
    return "helv", (0.0, 0.0, 0.0)


def apply_pdf(src: Path, dst: Path, mapping: dict[str, str]) -> list[tuple[str, str]]:
    import fitz
    doc = fitz.open(src)
    notes = []
    # Longest token first so an email is taken before its domain word.
    tokens = sorted(mapping, key=len, reverse=True)
    for page in doc:
        words = page.get_text("words")
        spans = page_spans(page)
        jobs = []  # rect, fake, size, font, color
        used = []
        for token in tokens:
            fake = mapping[token]
            found_rects = []  # rect, raw word, text to show (None: the fake)
            pieces, fpieces = token.split(), fake.split()
            if len(pieces) >= 2 and len(fpieces) == len(pieces):
                # A value written as several boxes on one line (a phone with
                # spaces, a full name): each piece gets its piece of the
                # fake. Real run 2026-10-31: phones stayed in a 23-page PDF.
                for k in range(len(words) - len(pieces) + 1):
                    run = words[k:k + len(pieces)]
                    if len({(w[5], w[6]) for w in run}) != 1:
                        continue
                    if not all(core_token(w[4]) == core_token(pc) for w, pc in zip(run, pieces)):
                        continue
                    rects = [fitz.Rect(w[0], w[1], w[2], w[3]) for w in run]
                    if any(r.intersects(u) for r in rects for u in used):
                        continue
                    found_rects += [(r, w[4], fp) for r, w, fp in zip(rects, run, fpieces)]
            elif len(token) >= 12:
                # A long value broken over two boxes (a link at a line end):
                # the fake goes in the first box, the second is cleared.
                for k in range(len(words) - 1):
                    ca, cb = core_token(words[k][4]), core_token(words[k + 1][4])
                    if (len(ca) >= 4 and len(cb) >= 2 and token.startswith(ca) and token.endswith(cb)
                            and len(ca) + len(cb) >= len(token) - 2 and ca != token):
                        ra = fitz.Rect(words[k][0], words[k][1], words[k][2], words[k][3])
                        rb = fitz.Rect(words[k + 1][0], words[k + 1][1], words[k + 1][2], words[k + 1][3])
                        if any(ra.intersects(u) or rb.intersects(u) for u in used):
                            continue
                        found_rects += [(ra, words[k][4], fake), (rb, words[k + 1][4], "")]
            for w in words:
                raw = w[4]
                core = core_token(raw)
                # Exact word, or a secret buried in a longer word
                # (Email:name@domain.com, (SHA256):hex).
                exact = core == token or raw == token
                # A secret inside a longer word (Email:name@domain.com,
                # (SHA256):hex) takes the whole word. Only long or
                # punctuated secrets: "Ana" must not take "Analista".
                buried = (
                    (len(token) >= 5 or not token.isalpha())
                    and token not in WHOLE_WORD_ONLY
                    and token in raw
                    and not inside_longer_word(token, raw)
                )
                # A short value glued to a mark in one box ("ABC/DK"): the
                # mark stays, the value goes. Real run 2026-10-31.
                glued = (not exact and not buried and token.isalpha() and (3 <= len(token) < 5 or (len(token) == 2 and token.isupper() and "/Ref" in raw))
                         and re.search(r"(?<![^\W_])" + re.escape(token) + r"(?![^\W_])", raw) is not None)
                if not exact and not buried and not glued:
                    continue
                rect = fitz.Rect(w[0], w[1], w[2], w[3])
                if any(rect.intersects(u) for u in used):
                    continue
                if exact:
                    found_rects.append((rect, raw, None))
                elif glued:
                    found_rects.append((rect, raw, raw.replace(token, fake)))
                else:
                    found_rects.append((rect, token, None))
            for rect, raw, override in found_rects:
                if any(rect.intersects(u) for u in used):
                    continue
                used.append(rect)
                size = size_for_rect(page, rect, 11.0)
                font, color = style_at(spans, rect)
                shown = fake if override is None else override
                suffix = ""
                if override is None and raw.endswith(",") and not fake.endswith(","):
                    suffix = ","
                if not shown:
                    jobs.append((rect, "", size, font, color))
                    continue
                width = text_width(shown + suffix, size, font)
                if width > rect.width + 0.8:
                    # Name-like fakes must stay at this size. Pick was already
                    # fit to the narrowest box. If this box is tighter, drop
                    # the suffix first.
                    if suffix and text_width(shown, size, font) <= rect.width + 0.8:
                        suffix = ""
                    else:
                        # Last resort: a smaller font. A slightly smaller word
                        # is better than leaving the real value in the copy.
                        new_size = size * (rect.width / width) * 0.98
                        if new_size < 4.0:
                            # Nothing readable fits. The box is still cleared:
                            # real run 2026-10-31, a footer's company name and
                            # website in 8.6-point boxes stayed in a 23-page
                            # attachment, and the copy failed its check.
                            notes.append((f"blanked tight box {rect.width:.1f}", token))
                            jobs.append((rect, "", size, font, color))
                            continue
                        if not mapping_kind_is_rigid(token, fake):
                            notes.append((f"shrunk to fit box {rect.width:.1f}", token))
                        size = new_size
                jobs.append((rect, shown + suffix, size, font, color))
        # House numbers only. A bare "01" is also a date, so it is not global.
        grouped = {}
        for w in words:
            grouped.setdefault((w[5], w[6]), []).append(w)
        for key in grouped:
            ws = sorted(grouped[key], key=lambda item: item[0])
            for i, w in enumerate(ws):
                if i == 0:
                    continue
                prev = core_token(ws[i - 1][4]).lower().rstrip(".")
                if prev not in {"nº", "no", "n"}:
                    continue
                num = core_token(w[4])
                if not num.isdigit() or len(num) < 2:
                    continue
                rect = fitz.Rect(w[0], w[1], w[2], w[3])
                if any(rect.intersects(u) for u in used):
                    continue
                used.append(rect)
                size = size_for_rect(page, rect, 11.0)
                font, color = style_at(spans, rect)
                fake_num = mask_shape(num)
                if text_width(fake_num, size, font) > rect.width + 0.8:
                    fake_num = str(seed_index(num, 89) + 10)
                jobs.append((rect, fake_num, size, font, color))
        for rect, *_rest in jobs:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        if jobs:
            page.apply_redactions(images=0)
            for rect, shown, size, font, color in jobs:
                if not shown:
                    continue
                page.insert_text(
                    fitz.Point(rect.x0, rect.y1 - 1.3),
                    shown,
                    fontname=font,
                    fontsize=max(size, 6),
                    color=color,
                )
        cover_signature_images(page)
    notes += blank_pdf_pictures(doc, mapping)
    scrub_pdf_extras(doc, mapping)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=4, deflate=True)
    # On the saved file, as the check reads it. Painting changes what OCR
    # segments next to it, so it goes again until nothing new is read.
    for _round in range(3):
        saved = fitz.open(dst)
        more = cover_ocr_values(saved, mapping)
        if not more:
            break
        notes += more
        tmp = dst.with_name(dst.name + ".tmp")
        saved.save(tmp, garbage=4, deflate=True)
        saved.close()
        tmp.replace(dst)
    return notes


def scrub_pdf_extras(doc, mapping: dict[str, str]) -> None:
    """Parts of a PDF that are not page text: metadata, the XMP block,
    comments, form fields, bookmarks, and attached files."""
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:
        pass
    for page in doc:
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
        for widget in list(page.widgets() or []):
            page.delete_widget(widget)
        for link in page.get_links():
            if link.get("uri"):
                page.delete_link(link)
    toc = doc.get_toc(simple=True)
    if toc:
        doc.set_toc([[lvl, replace_tokens(title, mapping), pno] for lvl, title, pno in toc])
    for name in list(doc.embfile_names()):
        doc.embfile_del(name)
    doc.set_metadata({"creator": "offline-anonymize"})


def pdf_extras_text(doc) -> str:
    """Everything scrub_pdf_extras touches, for the leak check."""
    parts = [str(v) for v in (doc.metadata or {}).values() if v]
    try:
        parts.append(doc.get_xml_metadata() or "")
    except Exception:
        pass
    for page in doc:
        for annot in page.annots() or []:
            parts += [str(v) for v in (annot.info or {}).values() if v]
        for widget in page.widgets() or []:
            parts += [str(widget.field_name or ""), str(widget.field_value or "")]
        parts += [link.get("uri", "") for link in page.get_links() if link.get("uri")]
    parts += [title for _lvl, title, _p in doc.get_toc(simple=True)]
    parts += list(doc.embfile_names())
    return "\n".join(parts)


def mapping_kind_is_rigid(token: str, fake: str) -> bool:
    """IDs keep their shape, so a slightly smaller size is allowed."""
    return bool(re.search(r"\d", token)) or "@" in token or len(token) >= 32


def blank_pdf_pictures(doc, mapping: dict[str, str]) -> list[tuple[str, str]]:
    """A picture on a PDF page that shows a real value (a letterhead with an
    address and phones) is blanked in place; the text over it stays. Real
    run 2026-10-21: a letterhead picture kept a postal code, two phones, a
    person, a company, and a place, and the PDF stopped with OCR_LEAKS."""
    import fitz
    langs = ocr_langs()
    if langs is None:
        return []
    fakes = {fold(f) for f in mapping.values()}
    notes = []
    for page in doc:
        rects = []
        for img in page.get_images(full=True):
            try:
                box = page.get_image_bbox(img)
            except Exception:
                continue
            if not isinstance(box, fitz.Rect) or box.is_empty or box.is_infinite:
                continue
            box = box & page.rect
            if box.width < 20 or box.height < 8:
                continue
            # A scanned page is one big picture; blanking it would erase the
            # page. Those stay and the OCR check reports them for a person.
            if box.get_area() > 0.6 * page.rect.get_area():
                continue
            png = page.get_pixmap(dpi=200, clip=box).tobytes("png")
            found = tokens_in_ocr(ocr_png(png, langs), mapping, fakes)
            if found:
                rects.append(box)
                for token in found:
                    notes.append((f"blanked picture on page {page.number + 1} that showed", token))
        for rect in rects:
            page.add_redact_annot(rect)
        if rects:
            # Pixels only: blank the picture, keep any text drawn over it.
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, text=fitz.PDF_REDACT_TEXT_NONE)
    return notes


def cover_signature_images(page) -> None:
    """Remove handwriting and QR pictures. Do not cover real text."""
    import fitz
    words = []
    for w in page.get_text("words"):
        words.append(fitz.Rect(w[0], w[1], w[2], w[3]))
    rects = []
    for img in page.get_images(full=True):
        bb = page.get_image_bbox(img)
        box = bb if isinstance(bb, fitz.Rect) else None
        if box is None:
            continue
        # Logos in the top band and full-width pictures stay.
        if box.y0 < 90 or box.width > 350 or box.height > 220:
            continue
        # Green check marks are small and sit at the left.
        if box.x1 < 120 and box.height < 50:
            continue
        # Bottom 2pt of the picture. The date line sits above these stamps.
        # images=1 deletes the whole picture when this sliver touches it.
        sliver = fitz.Rect(box.x0 + 2, box.y1 - 2.2, box.x1 - 2, box.y1 - 0.3)
        if sliver.height < 0.8 or any(sliver.intersects(wr) for wr in words):
            sliver = fitz.Rect(box.x0 + 2, box.y0 + 0.3, box.x1 - 2, box.y0 + 2.2)
        if sliver.height < 0.8 or any(sliver.intersects(wr) for wr in words):
            continue
        rects.append(sliver)
    for rect in rects:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    if rects:
        # 1 = delete the whole picture that touches the sliver. Text outside
        # the sliver stays.
        page.apply_redactions(images=1, text=0)


def open_docx(path: Path):
    """python-docx, or None when it cannot open the file (a part the file
    lists is missing). Real run 2026-10-21: KeyError in pkgreader. The raw
    XML is still readable and is used instead."""
    from docx import Document
    try:
        return Document(str(path))
    except Exception:
        return None


def docx_lines(path: Path) -> tuple[list[list[str]], str]:
    doc = open_docx(path)
    chunks = []
    lines = []
    if doc is None:
        text = docx_xml_text(path)
        for para in text.splitlines():
            if para.strip():
                chunks.append(para)
                lines.append(para.split())
        return lines, "\n".join(chunks)

    def take_paragraph(p):
        text = p.text
        if text.strip():
            chunks.append(text)
            lines.append(text.split())

    for p in doc.paragraphs:
        take_paragraph(p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    take_paragraph(p)
    for section in doc.sections:
        for part in (section.header, section.footer):
            for p in part.paragraphs:
                take_paragraph(p)
    # Hidden author and comment text.
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith(".rels"):
                    raw = zf.read(name).decode("utf-8", "replace")
                    targets = " ".join(re.findall(r'Target="([^"]+)"', raw))
                    if targets:
                        chunks.append(targets)
                        lines.append(targets.split())
                    continue
                if name in {"word/comments.xml", "docProps/core.xml", "docProps/app.xml"} or name.startswith("word/header") or name.startswith("word/footer"):
                    raw = zf.read(name).decode("utf-8", "replace")
                    plain = re.sub(r"<[^>]+>", " ", raw)
                    plain = re.sub(r"\s+", " ", plain)
                    chunks.append(plain)
                    lines.append(plain.split())
    except zipfile.BadZipFile:
        pass
    return lines, "\n".join(chunks)


@functools.lru_cache(maxsize=16)
def _compile_tokens(tokens: tuple[tuple[str, bool], ...]) -> re.Pattern | None:
    parts = []
    for token, whole_word in tokens:
        body = re.escape(token)
        # Short tokens and case variants only as whole words ("Lia" must not
        # hit "Liana"). Longer found tokens anywhere, so a name glued to
        # other text still goes.
        if len(token) < 5 or whole_word:
            if token[0].isalnum() or token[0] == "_":
                body = r"(?<!\w)" + body
            if token[-1].isalnum() or token[-1] == "_":
                body = body + r"(?!\w)"
        elif token.isalpha():
            left, right = word_continuation_guard(token)
            body = left + body + right
        parts.append(body)
    return re.compile("|".join(parts)) if parts else None


UPPER_LETTERS = "A-ZÀ-ÖØ-Þ"
LOWER_LETTERS = "a-zß-öø-ÿ"


def word_continuation_guard(token: str) -> tuple[str, str]:
    """Lookarounds that stop a name being replaced inside a longer word that
    goes on in the same case. Real case 2026-10-21: the name CLAUS was
    replaced inside CLAUSE (103 times, "SALESE 7.1"), which let a reader
    work out the real first name; "Environment" was replaced inside
    "Environmental". A name glued to other text in another case
    (MariaSilva, Email:x) is still replaced."""
    if not token or not token[0].isalpha() or not token[-1].isalpha():
        return "", ""
    left = UPPER_LETTERS if token[0].isupper() else LOWER_LETTERS
    right = UPPER_LETTERS if token[-1].isupper() else LOWER_LETTERS
    return f"(?<![{left}])", f"(?![{right}])"


def inside_longer_word(token: str, text: str) -> bool:
    """True when every occurrence of token in text continues a same-case word."""
    left, right = word_continuation_guard(token)
    if not left:
        return False
    return token in text and re.search(left + re.escape(token) + right, text) is None


def token_pattern(mapping: dict[str, str]) -> re.Pattern | None:
    """One regex for every token, longest first. One pass, so a fake is never
    replaced again by a later token."""
    ordered = sorted((t for t in mapping if t), key=lambda t: (-len(t), t))
    return _compile_tokens(tuple((t, t in WHOLE_WORD_ONLY) for t in ordered))


def replace_tokens(text: str, mapping: dict[str, str]) -> str:
    pattern = token_pattern(mapping)
    if pattern is None or not text:
        return text
    return pattern.sub(lambda m: mapping[m.group(0)], text)


def replace_in_pieces(pieces: list[str], mapping: dict[str, str]) -> list[str]:
    """Replace names across the text pieces of one paragraph (Word runs).
    Each fake goes into the piece where its name starts; text outside a name
    stays in its own piece, so its color and weight stay. Before this, the
    whole paragraph went into the first run and took that run's look."""
    joined = "".join(pieces)
    pattern = token_pattern(mapping)
    if pattern is None or not joined:
        return list(pieces)
    matches = list(pattern.finditer(joined))
    if not matches:
        return list(pieces)
    owner = []
    for k, piece in enumerate(pieces):
        owner += [k] * len(piece)
    out = [""] * len(pieces)
    pos = 0
    for m in matches:
        for c in range(pos, m.start()):
            out[owner[c]] += joined[c]
        out[owner[m.start()]] += mapping[m.group(0)]
        pos = m.end()
    for c in range(pos, len(joined)):
        out[owner[c]] += joined[c]
    return out


def replace_paragraph(paragraph, mapping: dict[str, str]) -> bool:
    runs = paragraph.runs
    if not runs:
        return False
    old = [r.text or "" for r in runs]
    new = replace_in_pieces(old, mapping)
    if new == old:
        return False
    for run, before, after in zip(runs, old, new):
        if after != before:
            run.text = after
    return True


def apply_docx(src: Path, dst: Path, mapping: dict[str, str]) -> list[tuple[str, str]]:
    doc = open_docx(src)
    if doc is None:
        # Raw path: copy the archive and rewrite every XML part.
        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                try:
                    data = zin.read(item.filename)
                except Exception:
                    # Real run 2026-10-21: one part failed its checksum
                    # (BadZipFile). The rest of the file is still usable.
                    continue
                zout.writestr(item, data)
        patch_docx_xml(dst, mapping)
        strip_docx_people(dst)
        return [("opened without python-docx (raw XML path)", "")] + blank_docx_pictures(dst, mapping)
    for p in doc.paragraphs:
        replace_paragraph(p, mapping)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    replace_paragraph(p, mapping)
    for section in doc.sections:
        for part in (section.header, section.footer):
            for p in part.paragraphs:
                replace_paragraph(p, mapping)
    try:
        doc.core_properties.author = ""
        doc.core_properties.last_modified_by = ""
        doc.core_properties.title = ""
    except Exception:
        pass
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dst))
    patch_docx_xml(dst, mapping)
    return blank_docx_pictures(dst, mapping)


def strip_docx_people(path: Path) -> None:
    """Author and last-modified-by, for the raw path (python-docx clears them
    on the normal path)."""
    with zipfile.ZipFile(path) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    core = items.get("docProps/core.xml")
    if core is None:
        return
    text = core.decode("utf-8", "replace")
    text = re.sub(r"(<dc:creator>)[^<]*(</dc:creator>)", r"\1\2", text)
    text = re.sub(r"(<cp:lastModifiedBy>)[^<]*(</cp:lastModifiedBy>)", r"\1\2", text)
    for tag in ("dc:title", "dc:subject", "cp:keywords", "dc:description"):
        text = re.sub(rf"(<{tag}>)[^<]*(</{tag}>)", r"\1\2", text)
    items["docProps/core.xml"] = text.encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, data in items.items():
            zf.writestr(n, data)
    tmp.replace(path)


# Visible text and tracked-deleted text. Deleted text is still in the file;
# Word shows it with "All Markup".
DOCX_TEXT_RE = r"(<w:(?:t|delText)(?:\s[^>]*)?>)(.*?)(</w:(?:t|delText)>)"


def rewrite_xml_paragraphs(xml: str, mapping: dict[str, str]) -> str:
    """Replace names across the runs of one paragraph, keeping each run's text
    in its run (see replace_in_pieces)."""

    def one_kind(block: str, tag: str) -> str:
        # Visible text (w:t) and tracked-deleted text (w:delText) are
        # replaced separately. Review of #1333: joined, a fake could move from
        # the deleted text into the visible text, or the other way.
        pattern = rf"(<w:{tag}(?:\s[^>]*)?>)(.*?)(</w:{tag}>)"
        pieces = list(re.finditer(pattern, block, flags=re.S))
        if not pieces:
            return block
        new = replace_in_pieces([piece.group(2) for piece in pieces], mapping)
        if new == [piece.group(2) for piece in pieces]:
            return block
        texts = iter(new)

        def put(piece: re.Match) -> str:
            text = next(texts)
            opening = piece.group(1)
            # Word drops a leading or trailing space without this.
            if text != text.strip() and "xml:space" not in opening:
                opening = opening[:-1] + ' xml:space="preserve">'
            return opening + text + piece.group(3)

        return re.sub(pattern, put, block, flags=re.S)

    def one_p(match: re.Match) -> str:
        return one_kind(one_kind(match.group(0), "t"), "delText")

    return re.sub(r"<w:p\b.*?</w:p>", one_p, xml, flags=re.S)


def patch_docx_xml(path: Path, mapping: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "r") as zf:
        items = {name: zf.read(name) for name in zf.namelist()}
    changed = False
    for name, data in list(items.items()):
        if not (name.endswith(".xml") or name.endswith(".rels")):
            continue
        text = data.decode("utf-8", "replace")
        new = rewrite_xml_paragraphs(text, mapping)
        # Attributes such as w:author and docProps fields.
        new = replace_tokens(new, mapping)
        if new != text:
            items[name] = new.encode("utf-8")
            changed = True
    if not changed:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)
    tmp.replace(path)


def copy_id(src: Path) -> str:
    """Keyed 8-character id of an input. Printed instead of the file name."""
    return keyed("output:" + str(src))[:8]


def name_pattern(mapping: dict) -> re.Pattern | None:
    """Tokens for file names: whole words, any case. In a file name "_"
    separates words, and lowercase names are common (contrato maria.pdf)."""
    tokens = sorted((t for t in mapping if len(t) >= 3), key=lambda t: (-len(t), t))
    if not tokens:
        return None
    alts = "|".join(re.escape(t) for t in tokens)
    return re.compile(r"(?<![^\W_])(?:" + alts + r")(?![^\W_])", re.IGNORECASE)


def anonymize_name(stem: str, mapping: dict) -> str:
    pattern = name_pattern(mapping)
    if pattern is None:
        return stem
    folded = {}
    for t in sorted(mapping, key=len, reverse=True):
        folded.setdefault(t.casefold(), mapping[t])
    return pattern.sub(lambda m: same_case(folded[m.group(0).casefold()], m.group(0)), stem)


def name_leaks(name: str, originals: list[str]) -> list[str]:
    """Real values left in a file name, any case, "_" as a separator."""
    left = []
    for t in originals:
        if len(t) < 3:
            continue
        if re.search(r"(?<![^\W_])" + re.escape(t) + r"(?![^\W_])", name, re.IGNORECASE):
            left.append(t)
    return left


MAX_NAME_BYTES = 255 - len(".report.json")
COPY_NAMES_TAKEN: set = set()


def output_path_for(src: Path, explicit: str | None, mapping: dict | None = None) -> Path:
    """ANON- plus the input's own name, with the same replacements as the
    content: "Contrato Maria Souza.pdf" becomes "ANON-Contrato Elena Duarte.pdf".
    The leak check reads this name too."""
    if explicit:
        return Path(explicit)
    stem = anonymize_name(src.stem, mapping) if mapping else src.stem
    suffix = ".eml" if src.suffix.lower() in EMAIL_TYPES else src.suffix
    if src.suffix.lower() == ".msg" and src.with_suffix(".eml").exists():
        stem += " (msg)"  # an .eml with the same name sits in the folder
    def fit(tag: str) -> str:
        # The report adds ".report.json"; both must fit the 255-byte limit.
        # The tag that tells duplicates apart is never cut.
        cut, mark = stem, ""
        while len(f"ANON-{cut}{mark}{tag}{suffix}".encode("utf-8")) > MAX_NAME_BYTES and len(cut) > 1:
            cut, mark = cut[:-1], "~"
        return f"ANON-{cut}{mark}{tag}{suffix}"

    cid = copy_id(src)
    name = fit("")
    # Two inputs can map to one name (two usernames both become "ana"), or
    # two long names can be cut to the same text.
    for width in (4, 8):
        if name.casefold() not in COPY_NAMES_TAKEN:
            break
        name = fit(f" ({cid[:width]})")
    COPY_NAMES_TAKEN.add(name.casefold())
    return src.with_name(name)


def report_path_for(dst: Path) -> Path:
    # The copy's full name, so Contrato.pdf and Contrato.docx get two reports.
    return dst.with_name(dst.name + ".report.json")


def strip_accents(text: str) -> str:
    """Remove accents but keep length and case, so positions still match."""
    return "".join(
        unicodedata.normalize("NFKD", c)[0] if unicodedata.normalize("NFKD", c) else c
        for c in text
    )


def any_case_hit(token: str, text: str) -> bool:
    """Whole-word match ignoring case and accents, skipping all-lowercase
    hits: a name in lowercase is almost always an ordinary word."""
    pattern = r"(?<!\w)" + re.escape(strip_accents(token)) + r"(?!\w)"
    plain = strip_accents(text)
    for m in re.finditer(pattern, plain, flags=re.IGNORECASE):
        if not text[m.start():m.end()].islower():
            return True
    return False


def verify(path: Path, originals: list[str]) -> list[str]:
    leaked = verify_text(extract_plain(path), originals)
    # The copy's file name: any case, "_" as a separator.
    for t in name_leaks(path.name, originals):
        if t not in leaked:
            leaked.append(t)
    return leaked


def source_text(path: Path) -> str:
    if path.suffix.lower() in EMAIL_TYPES:
        return load_mail(path).all_text()
    return extract_plain(path)


def verify_text(text: str, originals: list[str]) -> list[str]:
    leaked = []
    for token in originals:
        if len(token) < 3:
            continue
        if token in WHOLE_WORD_ONLY:
            # Case variants are replaced only as whole words, so they are
            # checked the same way ("CLARA" is not inside "DECLARAÇÃO").
            present = re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text) is not None
            if not present and len(token) >= 5:
                present = any_case_hit(token, text)
        elif len(token) >= 5 or any(ch.isspace() for ch in token) or any(ch in token for ch in "@/.-"):
            present = token in text and not (token.isalpha() and inside_longer_word(token, text))
            if not present and len(token) >= 5:
                # Other case, as a whole word, not all lowercase: MARIA
                # replaced but Maria left is a leak; "declara" and the
                # ordinary word "clara" are not leaks of "Clara".
                present = any_case_hit(token, text)
        else:
            present = re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text) is not None
        groups = re.findall(r"\d+", token)
        if not present and len(groups) >= 2 and sum(map(len, groups)) >= 8:
            # The same number with other separators (1.2.3.4 as 1-2-3-4).
            # Only short separators between the groups, never a whole line.
            loose = r"(?<!\d)" + r"[\s.\-/]?".join(groups) + r"(?!\d)"
            present = re.search(loose, text) is not None
        if present:
            leaked.append(token)
    # Unique, stable order.
    seen = []
    for item in leaked:
        if item not in seen:
            seen.append(item)
    return seen


def extract_plain(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        import fitz
        doc = fitz.open(path)
        return "\n".join(page.get_text() for page in doc) + "\n" + pdf_extras_text(doc)
    if path.suffix.lower() == ".docx":
        return (docx_xml_text(path) or docx_lines(path)[1]) + "\n" + docx_archive_text(path)
    if path.suffix.lower() == ".eml":
        return mail_plain(path)
    return path.read_text(encoding="utf-8", errors="replace")


def scan_docx_hidden(path: Path, hits: dict) -> None:
    """Comment authors and document creator are not in the body text."""
    try:
        with zipfile.ZipFile(path) as zf:
            blobs = []
            for name in ("word/comments.xml", "docProps/core.xml", "docProps/app.xml"):
                if name in zf.namelist():
                    blobs.append(zf.read(name).decode("utf-8", "replace"))
    except zipfile.BadZipFile:
        return
    authors = []
    for blob in blobs:
        authors += re.findall(r'w:author="([^"]+)"', blob)
        authors += re.findall(r"<dc:creator>([^<]+)</dc:creator>", blob)
        authors += re.findall(r"<cp:lastModifiedBy>([^<]+)</cp:lastModifiedBy>", blob)
        authors += re.findall(r"<Manager>([^<]+)</Manager>", blob)
        for company in re.findall(r"<Company>([^<]+)</Company>", blob):
            company = company.strip()
            if company and company.lower() not in KEEP:
                force(hits, company, "org", 1000.0, 11.0)
    for author in authors:
        for part in re.split(r"[|\s]+", author):
            word = core_token(part)
            if len(word) < 3 or word.lower() in KEEP or word.lower() in CONNECTORS:
                continue
            if word.isalpha():
                consider(hits, word, "name", 1000.0, 11.0)
                PEOPLE_FIELD_WORDS.add(word)


# Word parts that hold layout, not content: style and font lists, numbering,
# settings, themes. Real run 2026-10-21: the built-in style name list in
# styles.xml matched a capitalized word and failed 9 files.
DOCX_LAYOUT_PARTS = re.compile(
    r"^word/(styles|stylesWithEffects|fontTable|numbering|settings|webSettings)\.xml$|^word/theme/"
)


def is_docx_layout_part(name: str) -> bool:
    return bool(DOCX_LAYOUT_PARTS.search(name))


def docx_archive_text(path: Path) -> str:
    """Every .xml and .rels part of the archive, entities decoded. Covers link
    targets (mailto:), docProps/app.xml <Company>, and attributes."""
    import html as html_lib
    parts = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                if is_docx_layout_part(name):
                    continue
                parts.append(html_lib.unescape(zf.read(name).decode("utf-8", "replace")))
    except zipfile.BadZipFile:
        return ""
    return "\n".join(parts)


def docx_xml_text(path: Path) -> str:
    """Paragraph text, including text boxes. Runs are joined with no extra spaces."""
    parts = []
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.startswith("word/") or not name.endswith(".xml"):
                    continue
                raw = zf.read(name).decode("utf-8", "replace")
                for block in re.findall(r"<w:p\b.*?</w:p>", raw, flags=re.S):
                    bits = [m.group(2) for m in re.finditer(DOCX_TEXT_RE, block, flags=re.S)]
                    if bits:
                        parts.append("".join(bits))
    except zipfile.BadZipFile:
        return ""
    return "\n".join(parts)


def ollama_identify(text: str) -> list[tuple[str, str]]:
    """The model lists people, companies, and CNPJ. With FIREWORKS_API_KEY
    set the text goes to the Fireworks AI API; otherwise it stays local."""
    prompt = (
        "You are a local extractor. Read the document below. "
        "Return a JSON array only. Each item is "
        '{"text":"exact substring copied from the document","kind":"person"} '
        'or kind "company" or kind "cnpj". '
        "Include every person name, every company name, and every CNPJ number "
        "exactly as written, including punctuation. "
        "Do not include cities, countries, law names, or role words such as "
        "EMPREGADORA. Do not explain. Do not wrap in markdown.\n\n"
        "DOCUMENT:\n" + text[:60000]
    )
    raw = llm_generate(prompt, 2048)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    found = []
    for item in data:
        if not isinstance(item, dict):
            continue
        surface = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "").lower()
        if kind not in {"person", "company", "cnpj"} or len(surface) < 2:
            continue
        if surface.lower() in KEEP or len(surface.split()) > 8:
            continue
        if kind == "cnpj" and not re.search(r"\d{8}", re.sub(r"\D", "", surface)):
            continue
        if kind == "person" and len(surface) > 80:
            continue
        if kind == "company" and len(surface) > 120:
            continue
        # Only accept a string that is really in the document.
        if surface not in text:
            continue
        found.append((surface, kind))
    return found


def shape(value: str) -> str:
    """Xxxxx Xxxxx / ddd.ddd: the form of a value without the value."""
    out = []
    for ch in value:
        if ch.isdigit():
            out.append("d")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch if ch.isascii() else "?")
    return "".join(out)


def output_text(path: Path) -> str:
    if path.suffix.lower() == ".eml":
        return load_mail(path).all_text()
    if path.suffix.lower() == ".docx":
        return docx_xml_text(path)
    return extract_plain(path)


def second_look(dst: Path, mapping: dict[str, str]) -> list[str] | None:
    """Ask the local model to list people and companies in the OUTPUT.
    Anything it names that is not made of our fakes or kept words may be a
    real value the first pass missed. Returns the suspects, or None if the
    local model did not answer."""
    allowed = {w.lower() for fake in mapping.values() for w in re.findall(r"\w+", fake)}
    allowed |= KEEP | CONNECTORS
    allowed |= {"example", "offline", "anonymize", "attachment", "removed", "type", "supported",
                "logo", "icon", "real", "value", "seen", "pictures", "checked", "without", "ocr",
                "picture", "could", "read", "cleaned"}
    try:
        found = ollama_identify(output_text(dst) + "\n" + dst.stem.replace("_", " "))
    except Exception:
        return None
    suspects = []
    for surface, _kind in found:
        # "AlfaQuimica" is two fakes glued together: compare the pieces.
        words = [
            piece
            for w in re.findall(r"[^\W\d_]+", surface)
            for piece in (re.findall(r"[A-ZÀ-Þ]?[a-zß-ÿ]+|[A-ZÀ-Þ]+(?![a-zß-ÿ])", w) or [w])
            if len(piece) >= 3
        ]
        if words and any(w.lower() not in allowed for w in words):
            if surface not in suspects:
                suspects.append(surface)
    return suspects


# Words that describe a company rather than name it. Inside a company name
# they stay as they are ("Pirilampo Brasil Ltda" -> "Alfa Brasil Ltda"), so one
# company keeps one form. Real run 2026-10-21: "Brasil" became "Lumen" in one
# case and "NORTE" in another, and "A/S" became "IPE".
GENERIC_ORG_WORDS = {
    "brasil", "brazil", "brasileira", "brasileiro", "do", "holding", "participacoes", "comercio",
    "industria", "servicos", "services", "consultoria", "consulting", "consultancy", "gestao",
    "management", "ambiental", "environment", "environmental", "group", "grupo", "team", "company",
    "companhia", "cia", "international", "internacional", "solutions", "solucoes", "tecnologia",
    "technology", "technologies", "engenharia", "engineering", "quimica", "chemical", "chemicals",
    "mining", "mineracao", "trading", "importadora", "exportadora", "distribuidora", "logistica",
    "saude", "health", "capital", "investimentos", "partners", "associados", "advogados", "law",
    "firm", "bank", "banco", "seguros", "energia", "energy", "norte", "sul", "leste", "oeste",
    "denmark", "dinamarca", "chile", "usa", "america", "europe", "europa", "global",
}
# Public bodies and institutions: not personal data, never replaced.
INSTITUTION_WORDS = {
    "central", "receita", "federal", "junta", "comercial", "tribunal", "arbitral", "court", "courts",
    "district", "foro", "comarca", "ministry", "ministerio", "agency", "agencia", "national",
    "nacional", "department", "departamento", "registry", "registro", "revenue", "service", "servico",
    "board", "trade", "state", "estado", "city", "cidade", "council", "conselho", "ordem", "bar",
    "association", "associacao", "center", "centro", "arbitration", "arbitragem", "mediation",
    "mediacao", "chamber", "programa", "observatorio", "system", "sistema", "integrated",
    "foreign", "affairs", "internal", "labor", "code", "civil", "articles", "regulatory", "patent",
    "taxpayer", "office", "organization", "organizacao", "empresarial", "business", "corporate",
    "entities", "authority", "autoridade", "dinamarquesa", "danish", "brazilian", "regional",
    "chemistry", "water", "sanitation", "property", "intellectual", "importers", "integracao",
    "alimentacao", "trabalhador", "anvisa", "inmetro", "siscomex", "radar", "jucesp", "drei", "rfb",
    "oab", "amcham", "anm", "inpi", "bpto", "cade", "cvm", "susep", "ans", "clt", "rdc",
    "d4sign", "docusign", "sign", "itau", "bradesco", "santander", "coworking", "copenhague",
}
# Roles that name what someone is, not who.
ROLE_WORDS = {
    "party", "parties", "contracted", "contracting", "employee", "employer", "witness", "witnesses",
    "representative", "representatives", "individual", "individuals", "partner", "director",
    "directors", "shareholder", "shareholders", "officer", "officers", "minor", "client", "cliente",
    "empregada", "empregado", "empregadora", "outorgada", "outorgante", "administradora",
    "signatory", "signatories", "notary", "public", "previous", "current", "damaged", "prejudiced",
    "designated", "permanent", "one", "other", "testemunha", "lawyer", "advogada", "socio", "socia",
}
HONORIFICS = {"sr", "sra", "srta", "dr", "dra", "mr", "mrs", "ms", "miss", "prof", "profa",
              "senhor", "senhora", "doutor", "doutora", "dona", "d", "mme", "herr", "frau"}
ADDRESS_WORDS = {"rua", "avenida", "av", "alameda", "travessa", "rodovia", "estrada", "praca",
                 "sala", "andar", "bloco", "conjunto", "apto", "apartamento", "suite", "cep", "zip",
                 "code", "no", "street", "road", "floor", "room", "dr", "dra"}


def _bare(word: str) -> str:
    return strip_accents(word).lower().strip(".,;:()[]\"'")


def is_generic_org_word(word: str) -> bool:
    b = _bare(word)
    return (b in GENERIC_ORG_WORDS or b in INSTITUTION_WORDS or b in LEGAL_FORMS or b in KEEP
            or b in CONNECTORS or b in FUNCTION_WORDS or b in CONTRACT_WORDS or b in ROLE_WORDS
            or b in HONORIFICS)


def distinctive_words(span: str, label: str) -> list[str]:
    """The words of a span that actually identify someone."""
    out = []
    for w in re.findall(r"[^\W\d_]+", span):
        b = _bare(w)
        if len(w) < 3 or b in FUNCTION_WORDS or b in CONNECTORS:
            continue
        # Review of #1332: for a person only roles and titles are filtered;
        # Ana, Câmara, Paulo are names.
        if label == "person" and (b in ROLE_WORDS or b in HONORIFICS or b in CONTRACT_WORDS):
            continue
        if label != "person" and b in KEEP:
            continue
        if label == "organization" and is_generic_org_word(w):
            continue
        if label == "street address" and (b in ADDRESS_WORDS or b in GENERIC_ORG_WORDS):
            continue
        if label == "organization" and w.islower():
            # Real run 2026-10-21: GLiNER labeled the lowercase legal phrase
            # for "power of attorney" a company. People keep lowercase words:
            # review of #1333, "falei com o bartolomeu vasconcelos".
            continue
        out.append(w)
    return out


GLINER_MODEL = os.environ.get("OFFLINE_ANON_GLINER") or "urchade/gliner_multi_pii-v1"
GLINER_LABELS = ["person", "organization", "street address", "phone number"]
_GLINER: list = [None]
GLINER_WORDS: set = set()          # bare words GLiNER saw inside entities (context check)
GLINER_ORG_WORDS: set = set()      # bare words GLiNER saw inside a company
GLINER_PERSON_WORDS: set = set()   # bare words GLiNER saw inside a person
LAST_GLINER_COUNTS: dict = {}


def gliner_enabled() -> bool:
    return GLINER_MODEL.strip().lower() not in {"off", "0", "none", ""}


def gliner_entities(text: str) -> list[tuple[str, str]]:
    """(span, label) from GLiNER, run locally. It reads each word in its
    sentence, which the capitals rule cannot. Deterministic."""
    if _GLINER[0] is None:
        from gliner import GLiNER
        _GLINER[0] = GLiNER.from_pretrained(GLINER_MODEL)
    model = _GLINER[0]
    # Review of #1332: GLiNER silently ignores words past about 384 per
    # chunk. Windows of 250 words, overlapping by 50, so every word is read
    # with some context on both sides.
    # Windows are sized in GLiNER's own units, where every dot, dash, and
    # slash counts (a CPF is 7 of them): 300 per window, overlapping by 60.
    pieces = list(re.finditer(r"\w+(?:[-_]\w+)*|\S", text))
    parts = []
    for start in range(0, max(1, len(pieces)), 240):
        window = pieces[start:start + 300]
        if window:
            parts.append(text[window[0].start(): window[-1].end()])
        if start + 300 >= len(pieces):
            break
    found = []
    seen = set()
    for chunk in parts:
        for e in model.predict_entities(chunk, GLINER_LABELS, threshold=0.5):
            item = (re.sub(r"\s+", " ", e["text"]).strip(), e["label"])
            if item not in seen:
                seen.add(item)
                found.append(item)
    return found


def add_gliner_ids(hits: dict, text: str) -> None:
    counts = {"person": 0, "organization": 0, "street address": 0, "phone number": 0}
    for span, label in gliner_entities(text):
        if label == "phone number":
            if len(re.sub(r"\D", "", span)) >= 8:
                force(hits, span, "phone", 1000.0, 11.0)
                counts[label] += 1
            continue
        words = distinctive_words(span, label)
        for w in re.findall(r"[^\W\d_]+", span):
            GLINER_WORDS.add(_bare(w))
            if label == "organization":
                GLINER_ORG_WORDS.add(_bare(w))
            elif label == "person":
                GLINER_PERSON_WORDS.add(_bare(w))
        if not words:
            continue  # a role, a public body, or a generic description
        counts[label] += 1
        if label == "person":
            MODEL_PERSON_TOKENS.add(span)
            if len(words) >= 2:
                force(hits, span, "name", 1000.0, 11.0)
            for i, w in enumerate(words):
                if w not in hits:
                    force(hits, w, "name" if i == 0 else "last", 1000.0, 11.0)
                MODEL_TOKENS.add(w)
        elif label == "organization":
            if "." in span and " " not in span:
                force(hits, span, "host", 1000.0, 11.0)
                continue
            if len(span.split()) >= 2:
                force(hits, span, "org", 1000.0, 11.0)
            for w in words:
                if w not in hits:
                    force(hits, w, "org", 1000.0, 11.0)
                # Its capitals and title forms are replaced too. Real run
                # 2026-10-21: a company word found in title case stayed in
                # capitals in a heading.
                MODEL_TOKENS.add(w)
        else:  # street address
            force(hits, span, "place", 1000.0, 11.0)
            for w in words:
                if w not in hits:
                    force(hits, w, "place", 1000.0, 11.0)
                MODEL_TOKENS.add(w)
    LAST_GLINER_COUNTS.clear()
    LAST_GLINER_COUNTS.update(counts)
    print("GLINER " + " ".join(f"{k.split()[0]} {v}" for k, v in counts.items()))


def retype_company_words(hits: dict) -> int:
    """A single word flagged as a person (often by the capitals rule) that
    GLiNER read only inside a company name is a company word. Real run
    2026-10-21: the counterparty company's name in capitals became the fake
    person "Irene". A word GLiNER or the local model also read as a person,
    or one from an email header or author field, stays a person."""
    person_words = person_evidence_words()
    n = 0
    for token, hit in hits.items():
        if hit.kind not in {"name", "last"} or " " in token.strip() or not token.isalpha():
            continue
        b = _bare(token)
        if b in GLINER_ORG_WORDS and b not in person_words:
            hit.kind = "org"
            n += 1
    return n


def llm_says_not_a_name(word: str, text: str) -> bool:
    """Ask the local model about one word in its sentence. True only on a
    clear "no"; any doubt or error keeps the word as a name."""
    m = re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text)
    if not m:
        return False
    context = re.sub(r"\s+", " ", text[max(0, m.start() - 250): m.end() + 250])
    prompt = (f'Text: "{context}"\n\nIn this text, is the word "{word}" part of the name of a '
              f"specific person or a specific company? Answer with one word: yes or no.")
    try:
        answer = llm_generate(prompt, 8, timeout=120)
    except Exception:
        return False
    answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip().lower()
    return answer.startswith("no")


def after_title_or_street(word: str, text: str) -> bool:
    titles = "|".join(sorted(HONORIFICS | STREET_PREFIX, key=len, reverse=True))
    pattern = (r"(?i:\b(?:" + titles + r"))(?:\(a\))?[.ºª]*\s+(?:[^\W\d_]+\s+){0,2}"
               + re.escape(word) + r"(?!\w)")
    return re.search(pattern, text) is not None


def reject_rule_only(hits: dict, rule_tokens: set, text: str = "") -> int:
    """A word only the capitals or pair rules flagged is dropped only when
    both context judges say it is not a name: GLiNER did not see it inside a
    person, company, or address, and the local model, asked about that
    sentence, answers no. Words after a title (Sr., Dra.) or a street word
    (Rua) are never dropped. Real run 2026-10-21: SAIBAM (the opening word of
    a power of attorney) became the fake name ZOE."""
    rejected = 0
    for token in rule_tokens:
        hit = hits.get(token)
        if hit is None or hit.kind not in NAME_KINDS or token in MODEL_TOKENS or token in EXTRA_TOKENS:
            continue
        if token in PEOPLE_FIELD_WORDS or after_title_or_street(token, text):
            continue
        words = [_bare(w) for w in re.findall(r"[^\W\d_]+", token)]
        if words and any(w in GLINER_WORDS for w in words):
            continue
        if not llm_says_not_a_name(token, text):
            continue
        del hits[token]
        WHOLE_WORD_ONLY.discard(token)
        rejected += 1
    return rejected


LAST_RULE_REJECTED: list = [0]


def gliner_second_look(dst: Path, mapping: dict[str, str]) -> list[str]:
    """GLiNER on the copy: a person, address, or phone whose words are not
    fakes or ordinary words is a possible leftover."""
    if not gliner_enabled():
        return []
    allowed = {_bare(w) for fake in mapping.values() for w in re.findall(r"[^\W\d_]+", fake)}
    fake_digits = {re.sub(r"\D", "", f) for f in mapping.values() if re.search(r"\d{6}", f)}
    suspects = []
    text = output_text(dst) + "\n" + dst.stem.replace("_", " ")
    for span, label in gliner_entities(text):
        if label == "organization":
            continue  # companies are covered by the map and the local model
        if label == "phone number":
            digits = re.sub(r"\D", "", span)
            if len(digits) >= 8 and digits not in fake_digits and span not in mapping.values():
                suspects.append(span)
            continue
        words = [w for w in distinctive_words(span, label) if _bare(w) not in allowed and not w.islower()]
        if words and span not in suspects:
            suspects.append(span)
    return suspects


def add_local_ids(hits: dict, text: str) -> None:
    found = ollama_identify(text)
    counts = {"person": 0, "company": 0, "cnpj": 0}
    for surface, kind in found:
        counts[kind] += 1
        mapped = {"person": "name", "company": "org", "cnpj": "cnpj"}[kind]
        force(hits, surface, mapped, 1000.0, 11.0)
        MODEL_TOKENS.add(surface.strip())
        if kind == "person":
            MODEL_PERSON_TOKENS.add(surface.strip())
        if kind == "company" and "." in surface and " " not in surface.strip():
            hits[surface.strip()].kind = "host"  # pirilampo.com is a domain, not a word
            continue
        if kind in {"person", "company"}:
            for i, part in enumerate(surface.split()):
                word = core_token(part)
                if kind == "company" and is_generic_org_word(word):
                    continue
                if len(word) >= 3 and word.lower() not in KEEP and word.lower() not in CONNECTORS:
                    # Words after a person's first name draw from surnames,
                    # so a fake reads as a name, not four first names.
                    word_kind = "last" if kind == "person" and i > 0 and word not in hits else mapped
                    force(hits, word, word_kind if word not in hits else hits[word].kind, 1000.0, 11.0)
                    MODEL_TOKENS.add(word)
    if LOCAL_SUBFILE[0]:
        # An attachment: its counts add to the email's.
        for k, v in counts.items():
            LAST_LOCAL_COUNTS[k] = LAST_LOCAL_COUNTS.get(k, 0) + v
    else:
        LAST_LOCAL_COUNTS.clear()
        LAST_LOCAL_COUNTS.update(counts)
    print(
        f"LOCAL person {counts['person']} company {counts['company']} cnpj {counts['cnpj']}"
    )
    if sum(counts.values()) == 0:
        print("LOCAL_EMPTY")
        if LOCAL_SUBFILE[0]:
            return  # the email itself decides
        if not FOLDER_MODE:
            raise SystemExit(1)
        # Folder mode: other files' names still apply to this one. The copy
        # is written, but it can never be called CLEAN (see write_and_check).
        LOCAL_EMPTY_FILES.add(LOCAL_CURRENT_FILE[0])


FOLDER_MODE = False
LOCAL_EMPTY_FILES: set = set()
LOCAL_CURRENT_FILE: list = [None]


# ---------------------------------------------------------------- email

def html_to_text(markup: str) -> str:
    import html as html_lib
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", markup)
    markup = re.sub(r"<[^>]+>", " ", markup)
    text = html_lib.unescape(markup)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


class MailNode:
    """One email: the headers a reader needs, the plain body, forwarded
    emails inside it, and the extensions of attachments that were dropped."""

    HEADERS = ("From", "To", "Cc", "Subject", "Date")

    def __init__(self):
        self.headers: dict[str, str] = {}
        self.body = ""
        self.children: list[MailNode] = []
        self.dropped: list[str] = []
        self.attachments: list[Attachment] = []

    def add_attachment(self, att: Attachment) -> None:
        if att.kind == "other":
            self.dropped.append(f"{att.label} (type not supported)")
        else:
            self.attachments.append(att)

    def all_text(self) -> str:
        head = "\n".join(f"{k}: {v}" for k, v in self.headers.items() if k != "Date")
        rest = "\n\n".join(child.all_text() for child in self.children)
        docs = "\n\n".join(Path(a.name).stem.replace("_", " ") + "\n" + attachment_text(a)
                            for a in self.attachments if a.kind == "document")
        return head + "\n\n" + self.body + ("\n\n" + rest if rest else "") + ("\n\n" + docs if docs else "")

    def all_dropped(self) -> list[str]:
        out = list(self.dropped)
        for child in self.children:
            out += child.all_dropped()
        return out

    def all_attachments(self) -> list[Attachment]:
        out = list(self.attachments)
        for child in self.children:
            out += child.all_attachments()
        return out


def node_from_email(msg) -> MailNode:
    node = MailNode()
    for name in MailNode.HEADERS:
        value = msg.get(name)
        if value:
            node.headers[name] = str(value)
    body_part = msg.get_body(preferencelist=("plain", "html")) if msg.is_multipart() else msg
    if body_part is not None:
        try:
            content = body_part.get_content()
        except Exception:
            content = (body_part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if body_part.get_content_type() == "text/html":
            content = html_to_text(content)
        node.body = content if isinstance(content, str) else ""
    # Walk this email's own parts. A forwarded email (message/rfc822) becomes
    # a child and is not walked here: msg.walk() would descend into it, and
    # is_multipart() is True for it, so a plain walk loses it.
    stack = list(msg.iter_parts()) if msg.is_multipart() else []
    while stack:
        part = stack.pop(0)
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) else inner
            node.children.append(node_from_email(inner))
            continue
        if part.is_multipart():
            stack = list(part.iter_parts()) + stack
            continue
        if part is body_part:
            continue
        if ctype in {"text/plain", "text/html"} and not part.get_filename():
            # The other half of a plain/html pair. The chosen body covers it.
            continue
        data = part.get_payload(decode=True) or b""
        att = Attachment(part.get_filename() or "", ctype, data,
                         part.get_content_disposition() == "inline" or bool(part.get("Content-ID")))
        child = mail_from_bytes(att)
        if child is not None:
            node.children.append(child)
        else:
            node.add_attachment(att)
    return node


def mail_from_bytes(att: Attachment) -> "MailNode | None":
    """An email attached as a file (.eml or .msg bytes) becomes a child."""
    if att.ext == ".eml":
        import email
        from email import policy
        try:
            return node_from_email(email.message_from_bytes(att.data, policy=policy.default))
        except Exception:
            return None
    if att.ext == ".msg":
        try:
            import extract_msg
            m = extract_msg.openMsg(att.data)
            try:
                return node_from_msg(m)
            finally:
                m.close()
        except Exception:
            return None
    return None


def node_from_msg(m) -> MailNode:
    import email
    from email import policy
    node = MailNode()
    values = {"From": m.sender, "To": m.to, "Cc": m.cc, "Subject": m.subject, "Date": m.date}
    for name, value in values.items():
        if value:
            node.headers[name] = str(value)
    body = m.body or ""
    if not body.strip() and getattr(m, "htmlBody", None):
        raw = m.htmlBody
        body = html_to_text(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    node.body = body
    for att in m.attachments:
        data = getattr(att, "data", None)
        name = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or ""
        ext = attachment_type(name)
        if isinstance(data, (bytes, bytearray)) and ext == ".eml":
            node.children.append(node_from_email(email.message_from_bytes(bytes(data), policy=policy.default)))
        elif data is not None and hasattr(data, "attachments") and hasattr(data, "body"):
            node.children.append(node_from_msg(data))
        elif isinstance(data, (bytes, bytearray)):
            inline = bool(getattr(att, "cid", None) or getattr(att, "contentId", None)
                          or getattr(att, "hidden", False) is True)
            node.add_attachment(Attachment(name, str(getattr(att, "mimetype", "") or ""), data, inline))
        else:
            node.dropped.append(f"{ext} (type not supported)")
    return node


def load_mail(path: Path) -> MailNode:
    if path.suffix.lower() == ".msg":
        import extract_msg
        m = extract_msg.openMsg(str(path))
        try:
            return node_from_msg(m)
        finally:
            m.close()
    import email
    from email import policy
    with open(path, "rb") as fh:
        return node_from_email(email.message_from_binary_file(fh, policy=policy.default))


def header_names(node: MailNode) -> list[str]:
    """Display names from From/To/Cc, for every email in the tree."""
    from email.utils import getaddresses
    names = []
    for key in ("From", "To", "Cc"):
        value = node.headers.get(key, "")
        for display, _addr in getaddresses([value.replace(";", ",")]):
            display = display.strip().strip('"').strip()
            if display and "@" not in display:
                names.append(display)
    for child in node.children:
        names += header_names(child)
    return names


def anonymize_address_header(value: str, mapping: dict[str, str]) -> str:
    from email.utils import formataddr, getaddresses
    pairs = getaddresses([value.replace(";", ",")])
    if not pairs or not any(addr for _n, addr in pairs):
        return replace_tokens(value, mapping)
    out = []
    for display, addr in pairs:
        out.append(formataddr((replace_tokens(display, mapping), replace_tokens(addr, mapping))))
    return ", ".join(out)


def build_mail(node: MailNode, mapping: dict[str, str], notes: list | None = None):
    from email.message import EmailMessage
    from email import policy
    out = EmailMessage(policy=policy.default)
    for key, value in node.headers.items():
        if key in {"From", "To", "Cc"}:
            out[key] = anonymize_address_header(value, mapping)
        elif key == "Date":
            out[key] = value
        else:
            out[key] = replace_tokens(value, mapping)
    out["X-Anonymized-By"] = "offline-anonymize"
    body = replace_tokens(node.body, mapping)
    kept = clean_attachments(node, mapping, notes)
    if node.dropped:
        body += f"\n\n[offline-anonymize: {len(node.dropped)} attachment(s) removed: {', '.join(node.dropped)}]\n"
    out.set_content(body)
    for child in node.children:
        out.add_attachment(build_mail(child, mapping, notes))
    for data, ctype, filename in kept:
        maintype, _, subtype = ctype.partition("/")
        out.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return out


ATTACHMENT_CTYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
}


def clean_attachments(node: MailNode, mapping: dict[str, str],
                      notes: list | None) -> list[tuple[bytes, str, str]]:
    """The attachments of one email, cleaned. A document goes through the
    same writer as a file of its own. A picture is read with OCR: a real
    value seen in it, or an inline picture with no text (a logo), drops it.
    What is dropped is added to node.dropped with its reason."""
    kept: list[tuple[bytes, str, str]] = []
    notes = notes if notes is not None else []
    langs = ocr_langs()
    fakes = {fold(f) for f in mapping.values()}
    for att in node.attachments:
        cleaned_name = anonymize_name(Path(Path(att.name).name).stem, mapping) or "attachment"
        filename = cleaned_name + att.ext
        if att.kind == "document":
            src = attachment_path(att)
            # The cleaned copy carries the cleaned name: the leak check
            # reads file names too.
            dst = src.with_name("ANON-" + filename)
            try:
                inner = apply_pdf(src, dst, mapping) if att.ext == ".pdf" else apply_docx(src, dst, mapping)
            except Exception as exc:
                dst.unlink(missing_ok=True)
                node.dropped.append(f"{att.label} (could not be cleaned: {type(exc).__name__})")
                continue
            notes += [(f"in an attached {att.ext}: {m}", t) for m, t in inner]
            # Real run 2026-10-31: a 23-page PDF kept values in boxes too
            # narrow for any fake, and letterheads showed names to OCR.
            # An attachment that fails a check is dropped, never shipped.
            leaked = verify(dst, list(mapping))
            if not leaked:
                try:
                    ocr_hits, _unreadable = ocr_check(dst, mapping)
                except Exception:
                    ocr_hits = None
                leaked = [t for _w, t in (ocr_hits or [])]
            if leaked:
                node.dropped.append(f"{att.label} (a real value would remain in it)")
                notes.append((f"dropped an attached {att.ext} that would still show", leaked[0]))
                continue
            kept.append((dst.read_bytes(), ATTACHMENT_CTYPES[att.ext], filename))
            continue
        # A picture.
        if langs is None:
            node.dropped.append(f"{att.label} (pictures are not checked without OCR)")
            continue
        png = picture_png(att.data, att.ext)
        if png is None:
            node.dropped.append(f"{att.label} (picture could not be read)")
            continue
        text = ocr_png(png, langs)
        found = tokens_in_ocr(text, mapping, fakes)
        if found:
            node.dropped.append(f"{att.label} (a real value was seen in it)")
            notes.append((f"dropped an attached {att.ext} that showed", found[0]))
            continue
        if att.inline and sum(ch.isalpha() for ch in text) < MIN_PICTURE_LETTERS:
            node.dropped.append(f"{att.label} (logo or icon)")
            continue
        kept.append((att.data, ATTACHMENT_CTYPES.get(att.ext, att.ctype or "application/octet-stream"), filename))
    return kept


def apply_mail(src: Path, dst: Path, mapping: dict[str, str]) -> list[tuple[str, str]]:
    """Writes .eml: the headers a reader needs, the plain body, forwarded
    emails, and the attachments the script can clean or check (see
    Attachment). HTML and links are removed: they can hold names the text
    check cannot see."""
    node = load_mail(src)
    notes: list[tuple[str, str]] = []
    msg = build_mail(node, mapping, notes)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(msg.as_bytes())
    dropped = node.all_dropped()
    kept = sum(1 for part in msg.walk() if part.get_filename() and part.get_content_type() != "message/rfc822")
    if kept:
        notes.append((f"kept {kept} attachment(s)", ""))
    if dropped:
        notes.append((f"removed {len(dropped)} attachment(s): {', '.join(sorted(set(dropped)))}", ""))
    return notes


def mail_plain(path: Path) -> str:
    """Every header and decoded text part of the output, for the leak check."""
    import email
    from email import policy
    with open(path, "rb") as fh:
        msg = email.message_from_binary_file(fh, policy=policy.default)
    parts = []
    for part in msg.walk():
        for key, value in part.items():
            parts.append(f"{key}: {value}")
        if part.get_content_maintype() == "text":
            try:
                parts.append(part.get_content())
            except Exception:
                parts.append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
    # The raw file, without the base64 lines of attachments: their text is
    # read below, and a 7 MB attachment would make every check crawl.
    raw = path.read_bytes().decode("utf-8", "replace")
    parts.append(re.sub(r"(?m)^[A-Za-z0-9+/=]{40,}\r?$", "", raw))
    for att in node_from_email(msg).all_attachments():
        parts.append(Path(att.name).stem.replace("_", " "))
        parts.append(attachment_text(att))
    return "\n".join(parts)


# ------------------------------------------------------------------ OCR

def ocr_langs() -> str | None:
    exe = shutil.which("tesseract")
    if not exe:
        return None
    try:
        listed = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    have = set(listed.split())
    langs = [lang for lang in ("por", "eng") if lang in have]
    return "+".join(langs) if langs else None


def ocr_png(png: bytes, langs: str) -> str:
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", langs, "--psm", "3"],
        input=png, capture_output=True, timeout=180,
    )
    return result.stdout.decode("utf-8", "replace")


def ocr_words(png: bytes, langs: str) -> list[tuple[str, float, float, float, float]]:
    """OCR words with their pixel boxes: text, left, top, width, height."""
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", langs, "--psm", "3", "tsv"],
        input=png, capture_output=True, timeout=180,
    )
    out = []
    for line in result.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # level 5 is a word
            continue
        text = cols[11].strip()
        if text:
            out.append((text, float(cols[6]), float(cols[7]), float(cols[8]), float(cols[9])))
    return out


def cover_ocr_values(doc, mapping: dict[str, str]) -> list[tuple[str, str]]:
    """Paint over every word on a rendered page that OCR reads as a piece of
    a real value: a name in a letterhead, a stamp, a scanned signature page.
    It uses the OCR the check runs, so what the check would report is what
    gets covered. Real run 2026-10-31: three attached PDFs failed the OCR
    check on letterheads and scanned pages after every text box was clean."""
    import fitz
    langs = ocr_langs()
    if langs is None:
        return []
    fakes = {fold(f) for f in mapping.values()}
    notes = []
    dpi = 200
    for page in doc:
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        found = tokens_in_ocr(ocr_png(png, langs), mapping, fakes)
        if not found:
            continue
        words, digit_runs = set(), set()
        for token in found:
            digits = re.sub(r"\D", "", token)
            if len(digits) >= 6 and len(digits) >= len(token) // 2:
                digit_runs.add(digits)
            if " " not in token.strip() and len(core_token(token)) >= 3 and fold(core_token(token)) not in fakes:
                # A value of one word is painted whatever word it is; the
                # lists below only sort the words inside a longer value.
                words.add(fold(core_token(token)))
                continue
            for w in token.split():
                core = fold(core_token(w))
                # Every word of the value except connectors, titles, address
                # words and function words. A person's surname can also be
                # an ordinary word ("Costa", "Leal"): it is painted too.
                if (len(core) < 3 or core in fakes or core.lower() in CONNECTORS or core.lower() in FUNCTION_WORDS
                        or _bare(core) in ADDRESS_WORDS or _bare(core) in HONORIFICS or _bare(core) in LEGAL_FORMS):
                    continue
                words.add(core)
        scale = 72.0 / dpi
        rects = []
        for text, x, y, w, h in ocr_words(png, langs):
            t = fold(core_token(text))
            d = re.sub(r"\D", "", text)
            hit = any(k == t or (len(k) >= 5 and k in t) for k in words)
            hit = hit or (len(d) >= 4 and any(d in k or (len(d) >= 6 and k in d) for k in digit_runs))
            if not hit:
                continue
            r = fitz.Rect(x * scale, y * scale, (x + w) * scale, (y + h) * scale) + (page.rect.x0, page.rect.y0, page.rect.x0, page.rect.y0)
            if page.rotation:
                r = r * ~page.rotation_matrix
            rects.append(r)
        if not rects:
            continue
        for r in rects:
            page.add_redact_annot(r, fill=(1, 1, 1))
        # Pixels of pictures under the boxes go; text stays (the text check
        # covers text). A white box on top also hides vector drawings.
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS, text=fitz.PDF_REDACT_TEXT_NONE)
        for r in rects:
            page.draw_rect(r, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
        notes.append((f"covered {len(rects)} word box(es) on page {page.number + 1} that showed", found[0]))
    return notes


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.upper())


def output_pictures(path: Path) -> tuple[list[tuple[str, bytes]], int]:
    """(where, png bytes) for OCR, and the count of pictures OCR cannot read."""
    suffix = path.suffix.lower()
    pics: list[tuple[str, bytes]] = []
    unreadable = 0
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(path)
        for i, page in enumerate(doc):
            pics.append((f"page {i + 1}", page.get_pixmap(dpi=200).tobytes("png")))
    elif suffix == ".docx":
        import fitz
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.startswith("word/media/"):
                    continue
                ext = Path(name).suffix.lower()
                data = zf.read(name)
                if ext == ".png":
                    pics.append((f"picture {Path(name).name}", data))
                elif ext in {".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}:
                    try:
                        pics.append((f"picture {Path(name).name}", fitz.Pixmap(data).tobytes("png")))
                    except Exception:
                        unreadable += 1
                else:
                    unreadable += 1
    elif suffix == ".eml":
        for att in load_mail(path).all_attachments():
            if att.kind == "document":
                try:
                    inner, bad = output_pictures(attachment_path(att))
                except Exception:
                    unreadable += 1
                    continue
                pics += [(f"attached {att.ext} {where}", png) for where, png in inner]
                unreadable += bad
            elif att.kind == "picture":
                png = picture_png(att.data, att.ext)
                if png is None:
                    unreadable += 1
                else:
                    pics.append((f"attached {att.ext}", png))
    return pics, unreadable


def ocr_check(path: Path, mapping: dict[str, str]) -> tuple[list[tuple[str, str]] | None, int]:
    """Read the output's pages and pictures as pixels. A real value seen there
    is a leak the text check missed (a name inside a picture or a stamp)."""
    pics, unreadable = output_pictures(path)
    if not pics:
        return [], unreadable
    langs = ocr_langs()
    if langs is None:
        return None, unreadable
    fakes = {fold(f) for f in mapping.values()}
    hits = []
    for where, png in pics:
        hits += [(where, token) for token in tokens_in_ocr(ocr_png(png, langs), mapping, fakes)]
    return hits, unreadable


def tokens_in_ocr(text: str, mapping: dict[str, str], fakes: set[str]) -> list[str]:
    spaced = re.sub(r"\s+", " ", text)
    digit_lines = [re.sub(r"\D", "", line) for line in text.splitlines()]
    found = []
    for token in mapping:
        digits = re.sub(r"\D", "", token)
        if len(digits) >= 6 and len(digits) >= len(token) // 2:
            if any(digits in line for line in digit_lines):
                found.append(token)
            continue
        core = fold(token).strip()
        if len(core) < 4 or core in fakes:
            continue
        # Case- and accent-blind, whole word. A one-word hit in all lowercase
        # is skipped (usually an ordinary word); a full name is not.
        joined = re.sub(r"\s+", " ", token.strip())
        if any_case_hit(joined, spaced) or (" " in joined and re.search(
                r"(?<!\w)" + re.escape(strip_accents(joined)) + r"(?!\w)", strip_accents(spaced), re.I)):
            found.append(token)
    return found


def blank_docx_pictures(path: Path, mapping: dict[str, str]) -> list[tuple[str, str]]:
    """A picture whose pixels show a real value (a logo with the company
    name, a scanned signature) is replaced by a white picture of the same
    size. The layout keeps its place. Pictures with no real value stay."""
    langs = ocr_langs()
    if langs is None:
        return []
    import fitz
    fakes = {fold(f) for f in mapping.values()}
    with zipfile.ZipFile(path) as zf:
        items = {name: zf.read(name) for name in zf.namelist()}
    notes = []
    for name, data in list(items.items()):
        ext = Path(name).suffix.lower()
        if not name.startswith("word/media/") or ext not in {".png", ".jpg", ".jpeg"}:
            continue
        try:
            pix = fitz.Pixmap(data)
            png = data if ext == ".png" else pix.tobytes("png")
        except Exception:
            continue
        found = tokens_in_ocr(ocr_png(png, langs), mapping, fakes)
        if not found:
            continue
        white = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, pix.width, pix.height), False)
        white.set_rect(white.irect, (255, 255, 255))
        items[name] = white.tobytes("png" if ext == ".png" else "jpeg")
        for token in found:
            notes.append((f"blanked picture {Path(name).name} that showed", token))
    if notes:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in items.items():
                zf.writestr(name, data)
        tmp.replace(path)
    return notes


# -------------------------------------------------------------- collect

def collect(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    # Per-run state: a variant from one run must not change another's.
    COPY_NAMES_TAKEN.clear()
    WHOLE_WORD_ONLY.clear()
    MODEL_TOKENS.clear()
    CASE_KEEP.clear()
    MODEL_PERSON_TOKENS.clear()
    GLINER_WORDS.clear()
    GLINER_ORG_WORDS.clear()
    GLINER_PERSON_WORDS.clear()
    PEOPLE_FIELD_WORDS.clear()
    hits = collect_hits(path)
    dropped = drop_ordinary_words(hits, [visible_text(path)], [path.stem])
    if dropped:
        print(f"NOTE {dropped} ordinary word(s) were flagged as names and are kept")
    merge_possessives(hits)
    learn_genders([visible_text(path)])
    public = drop_public_terms(hits, [visible_text(path)])
    if public:
        print(f"NOTE {public} public body or template word(s) were flagged as names and are kept")
    return build_map(hits)


ADDRESS_RE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)+\.[a-z]{2,}\b", re.I)


def visible_text(path: Path) -> str:
    """What a reader sees: no XML tags, no layout parts."""
    s = path.suffix.lower()
    if s == ".docx":
        return docx_xml_text(path)
    if s == ".pdf":
        import fitz
        return "\n".join(page.get_text() for page in fitz.open(path))
    return load_mail(path).all_text()


HONORIFIC_NAME_RE = re.compile(
    r"\b(?i:sr|sra|srta|dr|dra|mr|mrs|ms|miss|prof|profa|senhor|senhora|doutor|doutora|dona|d)"
    r"(?:\(a\))?[.ºª]*\s+([A-ZÀ-Þ][^\W\d_]+)")

# Function words and contract vocabulary in Portuguese and English. On their
# own they never name anyone. Accent-free, lowercase.
FUNCTION_WORDS = {
    "the", "of", "and", "or", "to", "in", "on", "for", "by", "with", "at",
    "this", "that", "these", "those", "all", "both", "each", "any", "would", "shall",
    "accordingly", "whereas", "hereby", "herein",
    "os", "um", "uma", "de", "da", "do", "das", "dos", "em", "no", "na",
    "por", "para", "com", "este", "esta", "estes", "estas", "esse", "essa", "todos", "todas",
    "tudo", "cada", "qualquer", "seu", "sua", "pelo", "pela",
}
CONTRACT_WORDS = {
    "registered", "confidential", "signatories", "agreement", "attorney", "company", "office",
    "agency", "national", "trademark", "health", "mining", "consultancy", "particular",
    "termination", "contract", "contrato", "termo", "instrumento", "distrato", "procuracao",
    "acordo", "aditivo", "clausula", "parte", "partes", "empresa", "sociedade", "advogados",
    "advogado", "ambiental", "representante", "legal", "contratante", "contratada",
    "contratado", "empregador", "empregado", "outorgante", "outorgado", "testemunha",
}
COMMON_WORDS = FUNCTION_WORDS | CONTRACT_WORDS


def drop_ordinary_words(hits: dict, texts: list[str], file_names: list[str] = ()) -> int:
    """A single word flagged as a name or company that is written in
    lowercase 2+ times in the documents is an ordinary word (a heading in
    capitals such as CONTRATO), not a name: names are almost never
    lowercase. Full names of several words are kept, so "Maria Rosa" is
    still replaced. Real run 2026-10-21: 6 heading words in capitals were
    taken for names, and every copy of a 26-file case failed. This is the
    common-word fence from the monorepo's app/src/lib/pii/commonWords.mjs,
    measured on the case itself instead of a word list."""
    from collections import Counter
    lower = Counter()
    after_title = set()
    for t in texts:
        # Names are lowercase inside addresses (joao.silva@x.com); those
        # must not count as ordinary use.
        t = ADDRESS_RE.sub(" ", t)
        lower.update(w for w in re.findall(r"[^\W\d_]+", t) if w.islower())
        # "Sra. Rosa", "Dr. Leal": the word after an honorific is a name.
        after_title.update(m.group(1).lower() for m in HONORIFIC_NAME_RE.finditer(t))
    # Words of full person names the local model listed are names, even when
    # they are also ordinary words (Quintina Rosa). Company names are not
    # protected this way: their generic words (Advogados, Health) are not
    # identifying alone, and their distinctive word is not used in lowercase.
    # Every word the local model listed as part of a person, alone or in a
    # full name (Rosa, Quintina Rosa), is protected.
    # Second review of #1331: also every word of a multi-word person hit from
    # any detector, and every word from email headers and author fields
    # ("Ana Campos" in From:, signed "Campos" in the body).
    # Real run 2026-10-31: an email footer's "intended recipient" and
    # "reader of this message" came back as people, and their words then
    # protected "message" and "person" from this check. A multi-word hit
    # written all in lowercase whose every word is lowercase 3+ times in the
    # documents is ordinary prose. A lowercase real name in a chat ("falei
    # com o bartolomeu vasconcelos") has words seen once or twice.
    dropped_phrases = 0
    for t in list(hits):
        if hits[t].kind not in {"name", "last"} or " " not in t.strip() or not t.islower():
            continue
        ws = [w for w in re.findall(r"[^\W\d_]+", t) if len(w) >= 3 and w not in FUNCTION_WORDS]
        if ws and all(lower[w] >= 3 for w in ws):
            del hits[t]
            MODEL_PERSON_TOKENS.discard(t)
            WHOLE_WORD_ONLY.discard(t)
            dropped_phrases += 1
    person_sources = set(MODEL_PERSON_TOKENS) | set(PEOPLE_FIELD_WORDS) | {
        t for t, h in hits.items() if h.kind in {"name", "last"} and " " in t.strip()}
    in_person_names = {strip_accents(w).lower() for t in person_sources
                       for w in re.findall(r"[^\W\d_]+", t)}
    # A word in an input's file name is never dropped: a surname that is
    # only there (Contrato Rosa.docx) would stay on the copy's name.
    name_words = {w.lower() for n in file_names for w in re.findall(r"[^\W\d_]+", n)}
    # Review of #1331: protection wins over the contract-word list (Power,
    # Will, and Social are real names); only function words are never names.
    protected = {strip_accents(w).lower() for w in (in_person_names | name_words | after_title)} - FUNCTION_WORDS
    dropped = dropped_phrases
    for token in list(hits):
        hit = hits[token]
        if hit.kind not in NAME_KINDS or token in EXTRA_TOKENS:
            continue
        words = [strip_accents(w).lower() for w in re.findall(r"[^\W\d_]+", token)]
        if " " in token.strip():
            # A phrase made only of common words ("The Agreement") is not a name.
            if words and all((w in COMMON_WORDS or w in CONNECTORS) and w not in protected for w in words):
                del hits[token]
                WHOLE_WORD_ONLY.discard(token)
                dropped += 1
            continue
        if not token.isalpha():
            continue
        low = strip_accents(token).lower()
        if low in HONORIFICS:
            # Dra., Sr.: part of a name's span, never a name themselves.
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            dropped += 1
            continue
        if low in protected:
            continue
        if is_generic_org_word(token):
            # A company descriptor, institution, or role on its own (Consultoria,
            # Receita, Contratada) names nobody. Runs after the protections, so
            # a word in a listed person's name is kept; the lists hold no first
            # names or surnames (Ana and Câmara were removed in the #1332 review).
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            dropped += 1
            continue
        if low in COMMON_WORDS:
            # Real run 2026-10-21: The, Por, Este, Agreement, Procuração were
            # taken for names. Never a name on its own.
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            dropped += 1
            continue
        if lower.get(token.lower(), 0) >= 2 and not token.islower():
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            dropped += 1
    return dropped


def merge_hits(into: dict, more: dict) -> None:
    """Folder mode: one entry per real value across files. The narrowest PDF
    box anywhere sets the width, so one fake fits every file."""
    for token, hit in more.items():
        prev = into.get(token)
        if prev is None:
            into[token] = Hit(token, hit.kind, hit.width, hit.size)
        else:
            prev.width = min(prev.width, hit.width)
            prev.size = max(prev.size, hit.size)


EXTRA_TERMS_FILE = "_extra_terms.txt"
# Words a person decided are not names (an opening formula, a registry),
# one per line, in the case folder. Not secret, but kept out of the share.
KEEP_TERMS_FILE = "_keep_terms.txt"


def case_memory_path(folder: Path) -> Path:
    """The decisions of earlier runs of one case folder. Holds real values,
    so it lives with the maps, readable only by this user."""
    folder = folder.resolve()
    if folder.name == ORIGINALS_DIR:
        folder = folder.parent
    home = anon_home() / "cases"
    home.mkdir(exist_ok=True)
    os.chmod(home, 0o700)
    return home / (keyed("case:" + str(folder))[:12] + ".json")


def load_case_memory(folder: Path) -> dict:
    path = case_memory_path(folder)
    memory = {"pins": {}, "keep": [], "extra": []}
    if path.exists():
        # Review of #1334: a broken file must never be overwritten. It holds
        # the values added by hand; losing them would leave them in copies.
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ok = (isinstance(data, dict) and isinstance(data.get("pins", {}), dict)
                  and isinstance(data.get("keep", []), list) and isinstance(data.get("extra", []), list))
        except Exception:
            ok = False
        if not ok:
            print(f"ERROR the case memory {path.name} in {path.parent} cannot be read; "
                  "fix or move that file, then run again")
            raise SystemExit(2)
        memory.update(data)
    return memory


def save_case_memory(folder: Path, memory: dict) -> None:
    path = case_memory_path(folder)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(memory, fh, ensure_ascii=False, indent=1)
    os.chmod(path, 0o600)


def load_keep_terms(folder: Path) -> list[str]:
    path = folder / KEEP_TERMS_FILE
    if not path.exists() and folder.name == ORIGINALS_DIR:
        path = folder.parent / KEEP_TERMS_FILE
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


CASE_KEEP: set = set()


def merge_possessives(hits: dict) -> int:
    """Fold a possessive hit (Quaresma's) into its base name, from any
    detector. The token pattern then turns Quaresma's into Fake's."""
    n = 0
    for token in list(hits):
        hit = hits[token]
        base = strip_possessive(token)
        if base == token or hit.kind not in NAME_KINDS or len(base) < 2:
            continue
        del hits[token]
        WHOLE_WORD_ONLY.discard(token)
        if base in hits:
            hits[base].width = min(hits[base].width, hit.width)
        else:
            hits[base] = Hit(base, hit.kind, hit.width, hit.size)
        n += 1
    return n


def drop_public_terms(hits: dict, texts: list[str]) -> int:
    """Remove public bodies and template words (PUBLIC_TERMS) found as a
    name, a company, or a place. The local model returns some of them as
    companies, which the capitals rule's KEEP check never sees."""
    agency_ana = any(ANA_AGENCY_RE.search(t) for t in texts)
    n = 0
    for token in list(hits):
        if _bare(token) in PUBLIC_TERMS or (token == "ANA" and agency_ana):
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            n += 1
    return n


def drop_kept_terms(hits: dict, terms: list[str]) -> int:
    """Remove values a person kept by hand, in any case, alone or as a whole
    phrase. Their words also stay inside longer names."""
    folds = {strip_accents(t).casefold() for t in terms}
    CASE_KEEP.clear()
    CASE_KEEP.update(f for f in folds if " " not in f)
    n = 0
    for token in list(hits):
        if strip_accents(token).casefold() in folds:
            del hits[token]
            WHOLE_WORD_ONLY.discard(token)
            n += 1
    return n


def load_extra_terms(folder: Path) -> list[str]:
    """Values a person adds because no detector finds them (a foreign
    address, a project code name). One per line, in the case folder. The file
    holds real values: it is never processed and must not be shared."""
    path = folder / EXTRA_TERMS_FILE
    if not path.exists() and folder.name == ORIGINALS_DIR:
        path = folder.parent / EXTRA_TERMS_FILE
    if not path.exists():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


EXTRA_TOKENS: set = set()


def add_extra_terms(hits: dict, terms: list[str]) -> None:
    for term in terms:
        EXTRA_TOKENS.add(term)
        EXTRA_TOKENS.update(core_token(w) for w in term.replace(",", " ").split())
        force(hits, term, "place", 1000.0, 11.0)
        for part in term.replace(",", " ").split():
            word = core_token(part)
            if word.isdigit() and len(word) >= 2:
                force(hits, word, "number", 1000.0, 11.0)
            elif len(word) >= 3 and word.lower() not in KEEP and word.lower() not in CONNECTORS:
                force(hits, word, "place", 1000.0, 11.0)
                MODEL_TOKENS.add(word)


def collect_hits(path: Path) -> dict:
    hits: dict[str, Hit] = {}
    add_extra_terms(hits, load_extra_terms(path.parent))
    doc = None
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(path)
        lines = pdf_lines(doc)
        blob = "\n".join(page.get_text() for page in doc)
    elif suffix == ".docx":
        lines, blob = docx_lines(path)
        blob = blob + "\n" + docx_xml_text(path)
        scan_docx_hidden(path, hits)
    elif suffix in EMAIL_TYPES:
        node = load_mail(path)
        blob = node.all_text()
        lines = [line.split() for line in blob.splitlines() if line.strip()]
        for display in header_names(node):
            PEOPLE_FIELD_WORDS.update(w for w in display.replace(",", " ").split())
            if display.lower() not in KEEP:
                force(hits, display, "name", 1000.0, 11.0)
            for part in display.replace(",", " ").split():
                word = core_token(part)
                if len(word) >= 3 and word.isalpha() and word.lower() not in KEEP and word.lower() not in CONNECTORS:
                    force(hits, word, "name", 1000.0, 11.0)
    else:
        raise SystemExit(f"unsupported type: {path.suffix}")
    # The file name often holds the names too (Contrato Maria Souza.pdf), and
    # a name that is only there must still be found.
    file_words = path.stem.replace("_", " ")
    blob = blob + "\n" + file_words
    lines = lines + [file_words.split()]
    scan_text_blobs(blob, hits)
    before_rules = set(hits)
    scan_word_lines(lines, hits)
    rule_tokens = set(hits) - before_rules
    identify_text = blob
    if suffix == ".docx":
        identify_text = (docx_xml_text(path) or blob) + "\n" + file_words
    try:
        add_local_ids(hits, identify_text)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR local_model {type(exc).__name__}")
        raise SystemExit(1)
    LAST_RULE_REJECTED[0] = 0
    if gliner_enabled():
        try:
            add_gliner_ids(hits, identify_text)
        except Exception as exc:
            print(f"ERROR gliner {type(exc).__name__}; set OFFLINE_ANON_GLINER=off to run without it")
            raise SystemExit(1)
        LAST_RULE_REJECTED[0] = reject_rule_only(hits, rule_tokens, identify_text)
        if LAST_RULE_REJECTED[0]:
            print(f"NOTE {LAST_RULE_REJECTED[0]} word(s) flagged only by the capitals rule were not names in context")
        retyped = retype_company_words(hits)
        if retyped:
            print(f"NOTE {retyped} word(s) flagged as a person are part of a company name in context and get a company fake")
    add_phrase_words(hits)
    add_variants(hits)
    if suffix in EMAIL_TYPES:
        collect_attachment_hits(path, hits)
    if doc is not None:
        kinds = {t: h.kind for t, h in hits.items()}
        hits = tighten_widths_from_pdf(doc, kinds)
    return hits


def collect_attachment_hits(path: Path, hits: dict) -> None:
    """Documents attached to an email are read like files of their own, so
    a name that is only in a draft is found, and its fake fits the draft's
    PDF boxes. A document with no text the detectors can read (a scan)
    sends the email's copy to review."""
    for att in load_mail(path).all_attachments():
        if att.kind != "document":
            continue
        sub = attachment_path(att)
        if sum(ch.isalpha() for ch in attachment_text(att)) < MIN_PICTURE_LETTERS:
            print(f"NOTE an attached {att.ext} has no text the detectors can read; the copy goes to review")
            REVIEW_ATTACHMENTS.add(path)
            continue
        LOCAL_SUBFILE[0] = True
        try:
            merge_hits(hits, collect_hits(sub))
        except SystemExit:
            raise
        except Exception as exc:
            print(f"NOTE an attached {att.ext} could not be read ({type(exc).__name__}); the copy goes to review")
            REVIEW_ATTACHMENTS.add(path)
        finally:
            LOCAL_SUBFILE[0] = False


def write_report(dst: Path, report: dict) -> Path:
    """A record of the run that can travel with the copy: what was replaced
    (counts only), which checks ran, and their results. No real value, no
    label hash, no input file name."""
    path = report_path_for(dst)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def new_report(src: Path, dst: Path, mapping: dict, labels: dict,
               present: set | None = None, local_counts: dict | None = None,
               folder_files: int = 0) -> dict:
    from datetime import datetime, timezone
    counts: dict[str, int] = {}
    for token in mapping:
        if present is not None and token not in present:
            continue
        prefix = labels[token].rsplit("_", 1)[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    extra = {}
    if folder_files:
        extra["shared_map"] = (
            f"one map for {folder_files} files in the folder: the same person "
            "has the same fake in every copy"
        )
    return {
        "tool": "offline-anonymize",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input_type": src.suffix.lower(),
        "copy_id": copy_id_for_dst(dst),
        "privacy_status": (
            "pseudonymized: a real-to-fake map is kept on the owner's computer, "
            "so the owner can reverse the replacement"
        ),
        "local_model": llm_backend_name(),
        "context_model": GLINER_MODEL if gliner_enabled() else "off",
        "local_model_found": dict(local_counts if local_counts is not None else LAST_LOCAL_COUNTS),
        **extra,
        "values_replaced": dict(sorted(counts.items())),
        "pictures_blanked": 0,
        "attachments_kept": 0,
        "attachments_removed": 0,
        "checks": {"text": "not_run", "ocr": "not_run", "ocr_unreadable_pictures": 0, "look": "not_run",
                   "map_sync": "not_run", "map": "not_run", "second_look": "not_run"},
        "result": "",
        "not_covered": [
            "Details that identify a person without a name (job title with city, "
            "exact dates, salary, unique facts) are not changed. A person who knows "
            "the context must read the copy for them.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Anonymize a PDF, DOCX, MSG, or EML locally.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Show the labels and write nothing.")
    parser.add_argument("--reveal", action="store_true",
                        help="Print real values. Only for a person at a local terminal; never in a cloud agent.")
    args = parser.parse_args()
    src = args.input.expanduser().resolve()
    if src.is_dir():
        return run_folder(args, src)
    if not src.is_file():
        print("ERROR missing file", file=sys.stderr)
        return 2
    if src.suffix.lower() not in SUPPORTED:
        print("ERROR only .pdf, .docx, .msg, .eml, or a folder", file=sys.stderr)
        return 2
    mapping, kinds = collect(src)
    labels = make_labels(kinds)
    print_map(args, mapping, labels)
    MAP_PROBLEMS.clear()
    MAP_PROBLEMS.update(print_map_checks(mapping, kinds, labels))
    if args.dry_run:
        print("DRY_RUN")
        return 0
    return write_and_check(args, src, mapping, kinds, labels)


def print_map(args, mapping: dict, labels: dict) -> None:
    print(f"MAP {len(mapping)}")
    for token in sorted(mapping, key=lambda t: labels[t]):
        real = f" {token!r}" if args.reveal else ""
        print(f"  {safe_name(token, labels)}{real} -> {mapping[token]}")


def run_folder(args, folder: Path) -> int:
    global FOLDER_MODE
    try:
        return _run_folder(args, folder)
    finally:
        FOLDER_MODE = False


def _run_folder(args, folder: Path) -> int:
    """Every supported file in the folder (not subfolders), one shared map.
    Pass 1 finds values in all files; pass 2 writes each copy from the one
    map, so the same person has the same fake in the PDF, the Word file, and
    the email, and a name found in one file is replaced in all of them."""
    if args.output:
        print("ERROR -o is for one file; a folder writes ANON- copies next to each file", file=sys.stderr)
        return 2
    # "~$" files are Word's lock files while a document is open.
    def inputs_in(where: Path) -> list[Path]:
        return sorted(
            p for p in where.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(("ANON-", ".", "~$"))
        )

    files = inputs_in(folder)
    # A rerun on a folder that was already sorted reads from originals/.
    if not files and (folder / ORIGINALS_DIR).is_dir():
        files = inputs_in(folder / ORIGINALS_DIR)
    if not files:
        print("ERROR no .pdf, .docx, .msg, or .eml in the folder", file=sys.stderr)
        return 2
    global FOLDER_MODE
    FOLDER_MODE = True
    _COPY_IDS.clear()
    LOCAL_EMPTY_FILES.clear()
    REVIEW_ATTACHMENTS.clear()
    COPY_NAMES_TAKEN.clear()
    WHOLE_WORD_ONLY.clear()
    MODEL_TOKENS.clear()
    CASE_KEEP.clear()
    MODEL_PERSON_TOKENS.clear()
    GLINER_WORDS.clear()
    GLINER_ORG_WORDS.clear()
    GLINER_PERSON_WORDS.clear()
    PEOPLE_FIELD_WORDS.clear()
    merged: dict = {}
    local: dict[Path, dict] = {}
    readable = []
    failed_read = 0
    for i, path in enumerate(files, 1):
        print(f"FILE {i}/{len(files)} {path.suffix.lower()} reading")
        LOCAL_CURRENT_FILE[0] = path
        LAST_LOCAL_COUNTS.clear()
        try:
            hits = collect_hits(path)
        except SystemExit:
            raise
        except BaseException as exc:
            # No traceback: library errors quote the file path, which often
            # holds a person's name.
            print(f"FILE {i}/{len(files)} ERROR {type(exc).__name__}; this file was skipped and has no copy")
            failed_read += 1
            if isinstance(exc, KeyboardInterrupt):
                return 130
            continue
        local[path] = dict(LAST_LOCAL_COUNTS)
        merge_hits(merged, hits)
        readable.append(path)
    if not readable:
        return 1
    # A value found in one file can sit in a narrow PDF box in another (the
    # email's model listed a 3-letter word that the PDF's run did not). Measure
    # every merged value in every PDF so its fake fits there too. Real run
    # 2026-10-21: a 7-letter fake for a 3-letter word could not be written in
    # 24 PDF boxes, and the PDF stopped with LEAKS.
    import fitz
    all_kinds = {t: h.kind for t, h in merged.items()}
    for path in readable:
        if path.suffix.lower() != ".pdf":
            continue
        try:
            boxes = tighten_widths_from_pdf(fitz.open(path), all_kinds, keep_missing=False)
        except Exception:
            continue
        merge_hits(merged, boxes)
    for path in readable:
        if path.suffix.lower() not in EMAIL_TYPES:
            continue
        for att in load_mail(path).all_attachments():
            if att.kind != "document" or att.ext != ".pdf":
                continue
            try:
                boxes = tighten_widths_from_pdf(fitz.open(attachment_path(att)), all_kinds, keep_missing=False)
            except Exception:
                continue
            merge_hits(merged, boxes)
    texts = []
    for path in readable:
        try:
            texts.append(visible_text(path))
        except Exception:
            pass
    dropped = drop_ordinary_words(merged, texts, [p.stem for p in readable])
    if dropped:
        print(f"NOTE {dropped} ordinary word(s) were flagged as names and are kept")
    merge_possessives(merged)
    learn_genders(texts)
    public = drop_public_terms(merged, texts)
    if public:
        print(f"NOTE {public} public body or template word(s) were flagged as names and are kept")
    # Decisions from earlier runs of this case: fakes already shared, words
    # kept by hand, values added by hand.
    memory = load_case_memory(folder)
    if memory["extra"]:
        add_extra_terms(merged, memory["extra"])
    kept = drop_kept_terms(merged, list(memory["keep"]) + load_keep_terms(folder))
    if kept:
        print(f"NOTE {kept} value(s) kept by hand in an earlier run stay as they are")
    mapping, kinds = build_map(merged, memory["pins"])
    if memory["pins"]:
        reused = sum(1 for t, f in memory["pins"].items() if mapping.get(t) == f)
        print(f"MEMORY reused {reused} fake(s) from earlier runs; {len(mapping) - reused} new or changed")
    if not args.dry_run:
        # Review of #1334: values this run did not see (a skipped file) keep
        # their earlier fake.
        memory["pins"] = {**memory["pins"], **mapping}
        save_case_memory(folder, memory)
    labels = make_labels(kinds)
    print(f"SHARED_MAP {len(files)} files")
    print_map(args, mapping, labels)
    MAP_PROBLEMS.clear()
    MAP_PROBLEMS.update(print_map_checks(mapping, kinds, labels))
    if args.dry_run:
        print("DRY_RUN")
        return 0
    worst = 1 if failed_read else 0
    for i, path in enumerate(files, 1):
        if path not in readable:
            continue
        print(f"FILE {i}/{len(files)} {path.suffix.lower()} writing")
        # Count every map value that occurs in this file, including names
        # found only in other files and email domain labels.
        try:
            present = set(verify_text(source_text(path), list(mapping)))
        except Exception:
            present = None
        code = write_and_check(args, path, mapping, kinds, labels,
                               present=present, local_counts=local[path], folder_files=len(files),
                               local_empty=path in LOCAL_EMPTY_FILES)
        worst = max(worst, code)
    try:
        sort_outputs(folder, files)
    except Exception as exc:
        # No message: it would quote the folder path, which can hold a name.
        print(f"ERROR sorting into {ORIGINALS_DIR}/ and {ANONYMIZED_DIR}/ failed ({type(exc).__name__}); files left where they are")
        worst = max(worst, 1)
    print(f"FOLDER_DONE {len(files)} files, exit {worst}")
    return worst


ORIGINALS_DIR = "originals"
ANONYMIZED_DIR = "anonymized"
REVIEW_DIR = "needs-review"


def sort_outputs(folder: Path, inputs: list[Path]) -> None:
    """Owner 2026-10-21: at the end of a folder run, originals go into
    originals/ and the copies with their reports into anonymized/, so the
    folder to share is one subfolder. Nothing is deleted or overwritten: an
    earlier anonymized/ is renamed with the date, and an original whose name
    is already in originals/ stays where it is."""
    import shutil
    from datetime import datetime
    originals = folder / ORIGINALS_DIR
    anonymized = folder / ANONYMIZED_DIR
    review = folder / REVIEW_DIR
    for d in (originals, anonymized, review):
        if d.exists() and not d.is_dir():
            print(f"NOTE a file named {d.name} is in the way; sorting skipped, files left where they are")
            return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for d in (anonymized, review):
        if d.is_dir() and any(d.iterdir()):
            target = folder / f"{d.name}-previous-{stamp}"
            n = 2
            while target.exists():
                target = folder / f"{d.name}-previous-{stamp}-{n}"
                n += 1
            d.rename(target)
    originals.mkdir(exist_ok=True)
    anonymized.mkdir(exist_ok=True)
    moved_copies = to_review = 0
    # Only the copies this run wrote. A CLEAN copy goes to anonymized/, the
    # folder to share; anything else goes to needs-review/.
    for dst_str in list(_COPY_IDS):
        dst = Path(dst_str)
        rp = report_path_for(dst)
        if not dst.exists() or dst.parent not in {p.parent for p in inputs}:
            continue
        result = ""
        if rp.exists():
            try:
                result = json.loads(rp.read_text()).get("result", "")
            except Exception:
                result = ""
        dest = anonymized if result == "CLEAN" else review
        dest.mkdir(exist_ok=True)
        shutil.move(str(dst), str(dest / dst.name))
        if rp.exists():
            shutil.move(str(rp), str(dest / rp.name))
        if dest is anonymized:
            moved_copies += 1
        else:
            to_review += 1
    moved_originals = kept = 0
    for src in inputs:
        if src.parent == originals:
            continue
        target = originals / src.name
        if target.exists():
            kept += 1
            continue
        shutil.move(str(src), str(target))
        moved_originals += 1
    print(f"SORTED originals {moved_originals} into {ORIGINALS_DIR}/, CLEAN copies {moved_copies} into {ANONYMIZED_DIR}/, other copies {to_review} into {REVIEW_DIR}/")
    if kept:
        print(f"NOTE {kept} original(s) left in place: a file with the same name is already in {ORIGINALS_DIR}/")


def write_and_check(args, src: Path, mapping: dict, kinds: dict, labels: dict,
                    present: set | None = None, local_counts: dict | None = None,
                    folder_files: int = 0, local_empty: bool = False) -> int:
    dst = output_path_for(src, args.output, mapping)
    if dst.resolve() == src:
        print("ERROR output is the original file", file=sys.stderr)
        return 2
    _COPY_IDS[str(dst)] = copy_id(src)
    suffix = src.suffix.lower()
    try:
        # A report from an earlier run must never sit next to a new copy.
        report_path_for(dst).unlink(missing_ok=True)
        if suffix == ".pdf":
            notes = apply_pdf(src, dst, mapping)
        elif suffix == ".docx":
            notes = apply_docx(src, dst, mapping)
        else:
            notes = apply_mail(src, dst, mapping)
    except BaseException as exc:
        # A half-written copy is removed. No traceback: it can quote values.
        dst.unlink(missing_ok=True)
        print(f"ERROR writing the copy failed ({type(exc).__name__}); no copy was kept")
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    report = new_report(src, dst, mapping, labels, present, local_counts, folder_files)
    report["pictures_blanked"] = len({m for m, _t in notes if m.startswith("blanked picture")})
    for message, _token in notes:
        removed = re.match(r"removed (\d+) attachment", message)
        if removed:
            report["attachments_removed"] = int(removed.group(1))
        kept = re.match(r"kept (\d+) attachment", message)
        if kept:
            report["attachments_kept"] = int(kept.group(1))
    report["result"] = "INCOMPLETE"
    write_report(dst, report)

    def finish(result: str, code: int) -> int:
        report["result"] = result
        write_report(dst, report)
        print(f"REPORT copy {copy_id_for_dst(dst)}")
        print(result)
        return code

    try:
        return check_copy(args, src, dst, mapping, kinds, labels, notes, report, finish, local_empty)
    except BaseException as exc:
        report["error"] = type(exc).__name__
        print(f"ERROR the checks did not finish ({type(exc).__name__}); do not use the copy")
        return finish("ERROR", 130 if isinstance(exc, KeyboardInterrupt) else 1)


def pdf_look_changes(src: Path, dst: Path) -> int:
    """Lines of one color and weight in the original that show another color
    or weight in the copy, at the same place."""
    import fitz
    a, b = fitz.open(src), fitz.open(dst)

    def look(span: dict) -> tuple:
        return tuple(round(x * 255) for x in span_rgb(span)), span_is_bold(span)

    def same(x: tuple, y: tuple) -> bool:
        return x[1] == y[1] and all(abs(p - q) <= 3 for p, q in zip(x[0], y[0]))

    changed = 0
    for pa, pb in zip(a, b):
        copy_spans = page_spans(pb)
        for block in pa.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                looks = {look(s) for s in spans}
                if len(looks) != 1:
                    continue
                want = looks.pop()
                box = fitz.Rect(line["bbox"])
                for s in copy_spans:
                    r = fitz.Rect(s["bbox"])
                    if box.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)) and not same(look(s), want):
                        changed += 1
                        break
    return changed


RUN_LOOK_RE = re.compile(r"<w:(?:b|i|u|color|highlight|rStyle|shd|caps|strike|vertAlign)\b[^>]*/?>")


def docx_paragraph_looks(path: Path) -> dict[str, list[tuple[str, frozenset]]]:
    out = {}
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.startswith("word/") or not name.endswith(".xml") or is_docx_layout_part(name):
                continue
            raw = zf.read(name).decode("utf-8", "replace")
            paras = []
            for block in re.findall(r"<w:p\b.*?</w:p>", raw, flags=re.S):
                text = "".join(m.group(2) for m in re.finditer(DOCX_TEXT_RE, block, flags=re.S))
                looks = set()
                for run in re.findall(r"<w:r\b.*?</w:r>", block, flags=re.S):
                    if not "".join(m.group(2) for m in re.finditer(DOCX_TEXT_RE, run, flags=re.S)).strip():
                        continue
                    rpr = re.search(r"<w:rPr>(.*?)</w:rPr>", run, flags=re.S)
                    looks.add(" ".join(sorted(RUN_LOOK_RE.findall(rpr.group(1)))) if rpr else "")
                paras.append((text, frozenset(looks)))
            out[name] = paras
    return out


def docx_look_changes(src: Path, dst: Path) -> int:
    """Changed paragraphs where a run look (color, bold, link style) of the
    original no longer appears in the copy."""
    a, b = docx_paragraph_looks(src), docx_paragraph_looks(dst)
    changed = 0
    for name, paras in a.items():
        other = b.get(name)
        if other is None or len(other) != len(paras):
            continue
        for (ta, la), (tb, lb) in zip(paras, other):
            if ta != tb and not la <= lb:
                changed += 1
    return changed


def look_changes(src: Path, dst: Path) -> int | None:
    """None when the check does not apply (email) or could not run."""
    try:
        if dst.suffix.lower() == ".pdf":
            return pdf_look_changes(src, dst)
        if dst.suffix.lower() == ".docx":
            return docx_look_changes(src, dst)
    except Exception:
        return None
    return None


def missing_fakes(src: Path, dst: Path, mapping: dict[str, str], kinds: dict) -> list[str] | None:
    """Names written on their own in the original whose fake from the map is
    not in the copy. Owner 2026-10-21: after hand fixes the map still held
    the old fakes, so the owner's key no longer matched the copies. A name
    seen only inside an email address or a link does not count (the address
    is replaced as a whole), and the fake's words may sit on two PDF lines."""
    try:
        before = ADDRESS_RE.sub(" ", visible_text(src))
        after = fold(visible_text(dst))
    except Exception:
        return None
    after_words = set(re.findall(r"[^\W_]+", after))
    out = []
    for token in mapping:
        if kinds.get(token) not in NAME_KINDS:
            continue
        if not re.search(r"(?<![\w@.])" + re.escape(token) + r"(?![\w@])", before):
            continue
        fake_words = re.findall(r"[^\W_]+", fold(mapping[token]))
        if fake_words and not all(w in after_words for w in fake_words):
            out.append(token)
    return out


def check_copy(args, src, dst, mapping, kinds, labels, notes, report, finish, local_empty=False) -> int:
    for message, token in notes:
        print("NOTE", message, safe_name(token, labels) if token else "")
    map_path = save_map(dst, mapping, kinds, labels)
    # No file names: the copy keeps the input's name with known names
    # replaced, but a name the detectors missed would still be in it. The
    # copy is ANON-<input name> next to the input; the id links it to its
    # report and map.
    print(f"OUTPUT copy {copy_id_for_dst(dst)} {dst.suffix.lower()}")
    print(f"MAPFILE {map_path.name}")
    failed = False
    leaked = verify(dst, list(mapping))
    report["checks"]["text"] = "fail" if leaked else "pass"
    if leaked:
        failed = True
        print("LEAKS")
        for item in leaked:
            print(f"  {safe_name(item, labels)}")
    ocr_hits, unreadable = ocr_check(dst, mapping)
    report["checks"]["ocr"] = "skipped" if ocr_hits is None else ("fail" if ocr_hits else "pass")
    report["checks"]["ocr_unreadable_pictures"] = unreadable
    if unreadable:
        print(f"NOTE {unreadable} picture(s) in a format OCR cannot read; check them by eye")
    if ocr_hits is None:
        print("OCR_SKIPPED tesseract is not installed; pictures were not checked")
    elif ocr_hits:
        failed = True
        print("OCR_LEAKS")
        for where, token in ocr_hits:
            print(f"  {where} {safe_name(token, labels)}")
    if failed:
        return finish("OCR_LEAKS" if ocr_hits else "LEAKS", 1)
    changed = look_changes(src, dst)
    report["checks"]["look"] = "skipped" if changed is None else ("changed" if changed else "pass")
    if changed:
        # Not a privacy problem: the copy is usable, but it does not look
        # like the original in these places.
        print(f"LOOK {changed} line(s) or paragraph(s) lost their color or weight; open the copy to compare")
    unsynced = missing_fakes(src, dst, mapping, kinds)
    report["checks"]["map_sync"] = "skipped" if unsynced is None else ("fail" if unsynced else "pass")
    if unsynced:
        print(f"MAP_SYNC {len(unsynced)} name(s) in the copy do not show the fake the map records")
        for token in unsynced:
            print(f"  {safe_name(token, labels)}")
    affected = []
    if MAP_PROBLEMS:
        try:
            affected = verify_text(source_text(src), list(MAP_PROBLEMS))
        except Exception:
            affected = list(MAP_PROBLEMS)
    report["checks"]["map"] = "fail" if affected else "pass"
    if affected:
        print(f"MAP_CHECK {len(affected)} name(s) in this copy have a map problem")
        for token in affected:
            print(f"  {safe_name(token, labels)} {MAP_PROBLEM_TEXT[MAP_PROBLEMS[token]]}")
        return finish("REVIEW", 1)
    suspects = second_look(dst, mapping)
    try:
        gl = gliner_second_look(dst, mapping)
    except Exception:
        gl = []
        suspects = None if not suspects else suspects  # no CLEAN without the GLiNER look
    if gl:
        suspects = (suspects or []) + [g for g in gl if g not in (suspects or [])]
    report["checks"]["second_look"] = "skipped" if suspects is None else ("review" if suspects else "pass")
    if suspects is None:
        print("SECOND_LOOK_SKIPPED the local model did not answer")
    elif suspects:
        # Shapes only. A person reads the real words with --reveal or by
        # opening the copy; a cloud agent must not.
        print(f"REVIEW {len(suspects)} name(s) the local model still sees in the copy")
        for item in suspects:
            real = f" {item!r}" if args.reveal else ""
            print(f"  {shape(item)}{real}")
        return finish("REVIEW", 1)
    ok = ocr_hits is not None and not unreadable and suspects is not None
    if src in REVIEW_ATTACHMENTS:
        print("REVIEW an attached file has no text the detectors can read; read the copy")
        return finish("REVIEW", 1)
    if local_empty:
        # Only other files' names were applied here. Nothing proves this file
        # had no others, so a person reads it.
        print("REVIEW the local model listed no names in this file; read the copy")
        return finish("REVIEW", 1)
    return finish("CLEAN" if ok else "CLEAN_TEXT_ONLY", 0)


if __name__ == "__main__":
    raise SystemExit(main())
