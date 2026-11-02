"""Regression tests on fictional documents.

Every person, company, and number below is invented. These files are safe to
show a cloud agent; real documents are not. The local model is replaced by a
stub that returns the fictional names, so the tests run without Ollama.

Run:
  ~/.local/share/offline-anonymize/bin/python -m pytest tests -q
"""

from __future__ import annotations

import email
import re
import importlib.util
import shutil
import sys
import zipfile
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("anonymize", HERE.parent / "scripts" / "anonymize.py")
anon = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anon)

PEOPLE = ["Quintino Abelardo Varella", "Rosalva Teixeira Monforte"]
COMPANY = "PIRILAMPO ENGENHARIA LTDA"
CPF = "417.902.385-16"
CNPJ = "62.814.530/0001-77"
EMAIL = "quintino.varella@pirilampo-eng.com.br"
PHONE = "+55 (16) 98877-1234"
REAL_WORDS = ["Quintino", "Abelardo", "Varella", "Rosalva", "Monforte", "PIRILAMPO", CPF, CNPJ, EMAIL, "98877-1234"]


@pytest.fixture(autouse=True)
def local_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFLINE_ANON_HOME", str(tmp_path / "home"))
    # GLiNER is off unless a test turns it on with a stub (no model download).
    monkeypatch.setattr(anon, "GLINER_MODEL", "off")
    anon._SECRET = None

    def fake_model(text):
        found = []
        for person in PEOPLE:
            if person in text:
                found.append((person, "person"))
        if "PIRILAMPO" in text:
            found.append(("PIRILAMPO ENGENHARIA", "company"))
        return found

    monkeypatch.setattr(anon, "ollama_identify", fake_model)
    # Never the real Ollama in tests: the context judge keeps every word as
    # a name unless a test says otherwise.
    monkeypatch.setattr(anon, "llm_says_not_a_name", lambda word, text: False)
    yield



def found_path(rest: str) -> Path:
    """Output lines print ids, not file names. Resolve the rest of an OUTPUT
    ("copy <id> .ext"), REPORT ("copy <id>"), or MAPFILE ("<id>.json") line."""
    import json
    import os
    root = Path(os.environ["OFFLINE_ANON_HOME"]).parent
    parts = rest.split()
    if parts[0] == "copy":
        for rp in sorted(root.rglob("*.report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if json.loads(rp.read_text()).get("copy_id") == parts[1]:
                return rp.with_name(rp.name[: -len(".report.json")]) if len(parts) == 3 else rp
        raise FileNotFoundError(rest)
    hits = sorted(root.rglob(rest), key=lambda p: p.stat().st_mtime)
    if hits:
        return hits[-1]
    raise FileNotFoundError(rest)


def sorted_copies(folder: Path, pattern: str):
    """Copies after a folder run: CLEAN ones in anonymized/, the rest in needs-review/."""
    pattern = pattern if ("*" in pattern or pattern.endswith(".json")) else pattern + "*"
    if not pattern.startswith("ANON-") and not pattern.endswith(".json"):
        pattern = "ANON-" + pattern
    out = []
    for sub in ("anonymized", "needs-review"):
        d = folder / sub
        if d.is_dir():
            out += list(d.glob(pattern))
    return out


def run(monkeypatch, capsys, path: Path, *extra: str) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", ["anonymize.py", str(path), *extra])
    code = anon.main()
    return code, capsys.readouterr().out


def text_png(text: str) -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=300, height=60)
    page.insert_text((10, 35), text, fontsize=16)
    return page.get_pixmap(dpi=150).tobytes("png")


def make_docx(path: Path, picture: bytes | None = None) -> None:
    from docx import Document
    from docx.shared import Inches
    doc = Document()
    doc.add_paragraph(f"EMPREGADORA: {COMPANY}, CNPJ {CNPJ}.")
    doc.add_paragraph(f"EMPREGADO: {PEOPLE[0]}, CPF {CPF}, email {EMAIL}, telefone {PHONE}.")
    doc.add_paragraph(f"Testemunha: {PEOPLE[1]}. A empresa Liana Tecidos não é parte.")
    if picture:
        pic_path = path.with_suffix(".png")
        pic_path.write_bytes(picture)
        doc.add_picture(str(pic_path), width=Inches(3))
    doc.core_properties.author = PEOPLE[1]
    doc.save(str(path))


def make_pdf(path: Path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 120
    for line in (
        f"EMPREGADORA: {COMPANY}, CNPJ {CNPJ}.",
        f"EMPREGADO: {PEOPLE[0]}, CPF {CPF}.",
        f"Email: {EMAIL}",
        f"Testemunha: {PEOPLE[1]}.",
        "Sao Carlos/SP, 09 de setembro de 2026.",
    ):
        page.insert_text((72, y), line, fontsize=11)
        y += 18
    # A signature stamp: a picture with a name in it, below the date line.
    page.insert_image(fitz.Rect(150, 260, 400, 310), stream=text_png("Rosalva Monforte"))
    doc.save(str(path))


def make_msg_like_eml(path: Path) -> None:
    inner = EmailMessage(policy=policy.default)
    inner["From"] = f"{PEOPLE[1]} <rosalva.monforte@pirilampo-eng.com.br>"
    inner["To"] = f"{PEOPLE[0]} <{EMAIL}>"
    inner["Subject"] = "Contrato do Quintino"
    inner.set_content(f"Oi Quintino,\nsegue o contrato. CPF {CPF}.\nAtt,\nRosalva\n{PHONE}\n")
    inner.add_alternative(f"<p>Oi <b>Quintino</b>, segue o contrato. CPF {CPF}.</p>", subtype="html")
    inner.add_attachment(text_png("Rosalva Monforte"), maintype="image", subtype="png", filename="logo.png")
    outer = EmailMessage(policy=policy.default)
    outer["From"] = f"{PEOPLE[0]} <{EMAIL}>"
    outer["To"] = "Equipe Juridica <juridico@example.org>"
    outer["Subject"] = "FW: Contrato do Quintino"
    outer.set_content("Encaminho abaixo.\n")
    outer.add_attachment(inner)
    path.write_bytes(outer.as_bytes())


def assert_no_real_values(output: str) -> None:
    for word in REAL_WORDS:
        assert word not in output, f"stdout printed a real value: {word[:2]}..."


def test_docx_replaces_and_hides_values(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "CLEAN" in out
    assert_no_real_values(out)
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    for word in REAL_WORDS:
        assert word not in text
    # "Liana" is a different word; replacing "Lia..." must not touch it.
    assert "Liana Tecidos" in text


def test_two_people_get_different_fakes(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    mapping, kinds = anon.collect(src)
    assert mapping["Quintino"] != mapping["Rosalva"]
    assert mapping["Varella"] != mapping["Monforte"]
    # Fakes are not real words from the file.
    assert not set(mapping.values()) & set(mapping)


def test_map_file_is_private_and_outside_output_folder(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    map_path = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("MAPFILE ")))
    assert map_path.parent.parent == tmp_path / "home"
    assert oct(map_path.stat().st_mode & 0o777) == "0o600"
    assert "Quintino" in map_path.read_text()
    # Only the run report sits next to the copy, and it holds no map.
    beside = list(tmp_path.glob("*.json"))
    assert [p.name.endswith(".report.json") for p in beside] == [True]
    assert "Quintino" not in beside[0].read_text()


def test_reveal_prints_values(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src, "--dry-run", "--reveal")
    assert "Quintino" in out


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_docx_picture_with_name_is_blanked(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src, picture=text_png("Rosalva Monforte"))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "blanked picture" in out
    assert out.strip().splitlines()[-1] == "CLEAN"
    assert_no_real_values(out)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_docx_picture_without_name_stays(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src, picture=text_png("Logo Generico"))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "blanked picture" not in out


def test_second_look_reports_missed_name_as_shape(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    first = anon.ollama_identify

    def model_that_sees_a_leftover(text):
        found = first(text)
        if "Liana Tecidos" in text and not found:
            # Second look on the output: the model points at a name the
            # first pass did not list.
            return [("Liana Tecidos", "company")]
        return found

    monkeypatch.setattr(anon, "ollama_identify", model_that_sees_a_leftover)
    code, out = run(monkeypatch, capsys, src)
    assert code == 1
    assert "REVIEW 1" in out
    assert "Xxxxx Xxxxxxx" in out
    assert "Liana" not in out


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_pdf_stamp_picture_removed_and_text_replaced(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.pdf"
    make_pdf(src)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert out.strip().splitlines()[-1] == "CLEAN"
    assert_no_real_values(out)
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    assert "Sao Carlos/SP, 09 de setembro de 2026." in text


def test_email_with_forwarded_message(tmp_path, monkeypatch, capsys):
    src = tmp_path / "thread.eml"
    make_msg_like_eml(src)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert_no_real_values(out)
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    assert dst.suffix == ".eml"
    raw = dst.read_bytes()
    text = anon.mail_plain(dst)
    for word in REAL_WORDS + ["rosalva.monforte", "pirilampo-eng"]:
        assert word not in text
    msg = email.message_from_bytes(raw, policy=policy.default)
    types = [part.get_content_type() for part in msg.walk()]
    assert "message/rfc822" in types
    assert "image/png" not in types and "text/html" not in types
    assert "removed 1 attachment" in out


def blank_png() -> bytes:
    import fitz
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 120, 40), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    return pix.tobytes("png")


DOCX_CTYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_eml_with_documents(path: Path, tmp_path: Path, pictures: bool = False) -> None:
    """The names are only inside the attachments; the body is clean."""
    pdf = tmp_path / "Contrato Varella.pdf"
    make_pdf(pdf)
    docx = tmp_path / "Minuta Monforte.docx"
    make_docx(docx)
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "Equipe Juridica <juridico@example.org>"
    msg["To"] = "Diretoria <diretoria@example.org>"
    msg["Subject"] = "Minutas para revisao"
    msg.set_content("Seguem as minutas em anexo.\n")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    msg.add_attachment(docx.read_bytes(), maintype="application", subtype=DOCX_CTYPE.split("/")[1], filename=docx.name)
    msg.add_attachment(b"PK\x03\x04zip", maintype="application", subtype="zip", filename="anexos.zip")
    if pictures:
        msg.add_attachment(text_png("Prazo final de trinta dias para a resposta"), maintype="image", subtype="png",
                           filename="print.png")
        msg.add_attachment(text_png("Rosalva Monforte"), maintype="image", subtype="png", filename="assinatura.png")
        msg.add_attachment(blank_png(), maintype="image", subtype="png", filename="logo.png",
                           disposition="inline", cid="<logo@example.org>")
        msg.add_attachment(text_png("Protocolo recebido em dez de maio, sem pendencias"), maintype="image",
                           subtype="png", filename="tela.png", disposition="inline", cid="<tela@example.org>")
    path.write_bytes(msg.as_bytes())


def attached_files(copy: Path) -> dict[str, bytes]:
    msg = email.message_from_bytes(copy.read_bytes(), policy=policy.default)
    return {part.get_filename(): part.get_payload(decode=True)
            for part in msg.walk() if part.get_filename()}


def test_email_document_attachments_are_kept_and_cleaned(tmp_path, monkeypatch, capsys):
    """Owner 2026-10-31: attachments were dropped and the case lost its
    drafts. A PDF and a Word file now come back cleaned, under a cleaned
    name, and a name that is only inside them is found."""
    src = tmp_path / "minutas.eml"
    make_eml_with_documents(src, tmp_path)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert_no_real_values(out)
    assert "kept 2 attachment" in out and "removed 1 attachment" in out
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    files = attached_files(dst)
    assert sorted(Path(n).suffix for n in files) == [".docx", ".pdf"]
    for name, data in files.items():
        assert "Varella" not in name and "Monforte" not in name
        back = tmp_path / ("back" + Path(name).suffix)
        back.write_bytes(data)
        text = anon.extract_plain(back)
        for word in REAL_WORDS:
            assert word not in text, (name, word[:2])
    body = email.message_from_bytes(dst.read_bytes(), policy=policy.default).get_body(("plain",)).get_content()
    assert ".zip (type not supported)" in body
    import json
    report = json.loads(found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("REPORT "))).read_text())
    assert report["attachments_kept"] == 2 and report["attachments_removed"] == 1


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
def test_email_pictures_are_kept_or_dropped_by_what_they_show(tmp_path, monkeypatch, capsys):
    """A picture with a real value is dropped; an inline picture with no
    text (a logo) is dropped; a screenshot with clean text stays, inline or
    not."""
    src = tmp_path / "prints.eml"
    make_eml_with_documents(src, tmp_path, pictures=True)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert_no_real_values(out)
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    files = attached_files(dst)
    assert "print.png" in files and "tela.png" in files
    assert "assinatura.png" not in files and "logo.png" not in files
    body = email.message_from_bytes(dst.read_bytes(), policy=policy.default).get_body(("plain",)).get_content()
    assert "(a real value was seen in it)" in body and "(logo or icon)" in body
    assert "removed 3 attachment" in out


def test_attachment_that_would_still_leak_is_dropped(tmp_path, monkeypatch, capsys):
    """Real run: an attached PDF kept names in boxes too narrow for a fake.
    When the cleaned attachment fails the leak check it is dropped and the
    email's copy still passes."""
    src = tmp_path / "minutas.eml"
    make_eml_with_documents(src, tmp_path)
    import shutil as sh
    monkeypatch.setattr(anon, "apply_pdf", lambda s, d, m: (sh.copyfile(s, d), [])[1])  # the PDF comes back unchanged
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert_no_real_values(out)
    assert "dropped an attached .pdf that would still show" in out
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    files = attached_files(dst)
    assert sorted(Path(n).suffix for n in files) == [".docx"]
    body = email.message_from_bytes(dst.read_bytes(), policy=policy.default).get_body(("plain",)).get_content()
    assert ".pdf (a real value would remain in it)" in body


def test_lowercase_phrase_of_ordinary_words_is_not_a_person():
    """Real run: "intended recipient" and "reader of this message" from an
    email footer came back as people; a lowercase real name in a chat does
    not."""
    footer = ("If the reader of this message is not the intended recipient, notify the sender. "
              "This message is for the intended recipient only. Any other reader of this message "
              "must delete it. The intended recipient alone may read the message; no other reader may.")
    texts = [footer, "falei com o bartolomeu vasconcelos ontem"]
    hits = {"reader of this message": anon.Hit("reader of this message", "name", 1000.0, 11.0),
            "intended recipient": anon.Hit("intended recipient", "name", 1000.0, 11.0),
            "bartolomeu vasconcelos": anon.Hit("bartolomeu vasconcelos", "name", 1000.0, 11.0),
            "Message": anon.Hit("Message", "name", 1000.0, 11.0)}
    # The phrases came from the local model; "Message" from the capitals rule.
    anon.MODEL_PERSON_TOKENS.update(k for k in hits if " " in k)
    anon.drop_ordinary_words(hits, texts)
    assert set(hits) == {"bartolomeu vasconcelos"}
    anon.MODEL_PERSON_TOKENS.clear()


def test_leak_check_reads_attached_documents(tmp_path):
    """A real value left inside an attachment must fail the copy."""
    pdf = tmp_path / "anexo.pdf"
    make_pdf(pdf)
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = "x"
    msg.set_content("limpo\n")
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename="anexo.pdf")
    copy = tmp_path / "ANON-copy.eml"
    copy.write_bytes(msg.as_bytes())
    assert anon.verify(copy, ["Quintino", "Rosalva"]) == ["Quintino", "Rosalva"]
    pics, unreadable = anon.output_pictures(copy)
    assert pics and unreadable == 0 and pics[0][0].startswith("attached .pdf")


def test_folder_run_shares_fakes_with_attached_documents(tmp_path, monkeypatch, capsys):
    """A person named in a Word file and in a PDF attached to an email gets
    one fake in both copies."""
    folder = tmp_path / "caso"
    folder.mkdir()
    make_eml_with_documents(folder / "minutas.eml", tmp_path)
    make_docx(folder / "contrato.docx")
    code, out = run(monkeypatch, capsys, folder)
    assert code == 0, out
    assert_no_real_values(out)
    assert "SHARED_MAP 2 files" in out
    eml = [p for p in sorted_copies(folder, "minutas") if p.suffix == ".eml"][0]
    docx = [p for p in sorted_copies(folder, "contrato") if p.suffix == ".docx"][0]
    inner = tmp_path / "inner.pdf"
    inner.write_bytes(next(d for n, d in attached_files(eml).items() if n.endswith(".pdf")))
    words_pdf = set(anon.extract_plain(inner).split())
    words_docx = set(anon.extract_plain(docx).split())
    fakes = {w for w in words_pdf & words_docx if w[:1].isupper() and w.isalpha() and len(w) >= 3
             and w.lower() not in anon.KEEP and w not in {"EMPREGADORA", "EMPREGADO", "CNPJ", "CPF", "Testemunha"}}
    assert fakes, "no shared fake between the attached PDF and the Word copy"


def test_same_fake_across_runs(tmp_path, monkeypatch, capsys):
    a = tmp_path / "a.docx"
    b = tmp_path / "b.docx"
    make_docx(a)
    make_docx(b)
    map_a, _ = anon.collect(a)
    map_b, _ = anon.collect(b)
    assert map_a["Quintino"] == map_b["Quintino"]


def test_docx_output_has_no_author(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    with zipfile.ZipFile(dst) as zf:
        core = zf.read("docProps/core.xml").decode()
    assert "Rosalva" not in core


# ---- review findings on PR #1319 -------------------------------------------

def test_id_fakes_are_not_a_fixed_shift():
    """The old fake was digit -> (d + 3 + i) % 10, reversible by anyone."""
    shifted = "".join(str((int(c) + 3 + i) % 10) if c.isdigit() else c for i, c in enumerate(CPF))
    fake = anon.choose_fake(CPF, "cpf", 1000.0, 11.0)
    assert fake != shifted and fake != CPF
    assert anon.shape(fake) == anon.shape(CPF)


def test_pdf_short_name_does_not_take_longer_word(tmp_path):
    import fitz
    src = tmp_path / "short.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), "Analista: Ana Varella, CPF 417.902.385-16.", fontsize=11)
    doc.save(str(src))
    dst = tmp_path / "out.pdf"
    anon.apply_pdf(src, dst, {"Ana": "Eva", "Varella": "Lima"})
    assert "Analista" in anon.extract_plain(dst)


def test_pdf_metadata_comments_and_links_removed(tmp_path, monkeypatch, capsys):
    import fitz
    src = tmp_path / "meta.pdf"
    make_pdf(src)
    doc = fitz.open(str(src))
    doc.set_metadata({"subject": f"Contrato {PEOPLE[0]}", "keywords": "Monforte"})
    page = doc[0]
    annot = page.add_text_annot((300, 400), f"Revisar com {PEOPLE[1]}")
    annot.set_info(title=PEOPLE[1])
    annot.update()
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(72, 100, 200, 110), "uri": f"mailto:{EMAIL}"})
    tmp = tmp_path / "meta2.pdf"
    doc.save(str(tmp))
    code, out = run(monkeypatch, capsys, tmp)
    assert code == 0, out
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    for word in REAL_WORDS:
        assert word not in text


def test_docx_link_target_and_company_field(tmp_path, monkeypatch, capsys):
    src = tmp_path / "links.docx"
    make_docx(src)
    with zipfile.ZipFile(src) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    rels = items["word/_rels/document.xml.rels"].decode()
    rels = rels.replace(
        "</Relationships>",
        '<Relationship Id="rIdX" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        f'Target="mailto:{EMAIL}" TargetMode="External"/></Relationships>',
    )
    items["word/_rels/document.xml.rels"] = rels.encode()
    app = items["docProps/app.xml"].decode()
    items["docProps/app.xml"] = app.replace("</Properties>", "<Company>Monforte Consultoria</Company></Properties>").encode()
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in items.items():
            zf.writestr(n, data)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    raw = anon.docx_archive_text(dst)
    assert EMAIL not in raw
    assert "Monforte" not in raw


def test_shape_hides_any_alphabet():
    assert anon.shape("Łukasz Ğül 王伟") == "Xxxxxx Xxx xx"


def report_for(out: str) -> dict:
    import json
    path = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("REPORT ")))
    return json.loads(path.read_text()), path


def test_report_has_counts_and_no_real_values(tmp_path, monkeypatch, capsys):
    src = tmp_path / "Contrato Quintino Varella.docx"
    make_docx(src, picture=text_png("Rosalva Monforte"))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    report, path = report_for(out)
    raw = path.read_text()
    for word in REAL_WORDS:
        assert word not in raw
    # No label hash: with the secret, a hash lets someone test a guess.
    # script_sha256 and output_file are hex by design; a 4-character hash
    # can sit inside them by chance, so they are left out of this check.
    import json
    rest = json.dumps({k: v for k, v in report.items() if k not in {"script_sha256", "copy_id"}})
    for line in out.splitlines():
        if line.startswith("  ") and "(" in line:
            assert line.split("(")[1][:4] not in rest
    assert report["result"] == "CLEAN"
    assert report["checks"] == {"text": "pass", "ocr": "pass", "ocr_unreadable_pictures": 0, "look": "pass",
                                "map_sync": "pass", "map": "pass", "second_look": "pass"}
    assert sum(report["values_replaced"].values()) == int(next(l for l in out.splitlines() if l.startswith("MAP ")).split()[1])
    assert report["pictures_blanked"] == 1
    assert report["privacy_status"].startswith("pseudonymized")
    assert path.parent == src.parent and path.name.startswith("ANON-")


def test_report_written_when_review_fails(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    first = anon.ollama_identify
    monkeypatch.setattr(anon, "ollama_identify", lambda text: first(text) or [("Liana Tecidos", "company")])
    code, out = run(monkeypatch, capsys, src)
    assert code == 1
    report, _ = report_for(out)
    assert report["result"] == "REVIEW"
    assert report["checks"]["second_look"] == "review"


def test_crash_in_checks_writes_error_report_not_stale_clean(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    first, _ = report_for(out)
    assert first["result"] == "CLEAN"

    def broken_ocr(*a, **k):
        raise TimeoutError("tesseract took too long")

    monkeypatch.setattr(anon, "ocr_check", broken_ocr)
    code, out = run(monkeypatch, capsys, src)
    assert code == 1
    report, _ = report_for(out)
    assert report["result"] == "ERROR"
    assert report["error"] == "TimeoutError"
    assert "Traceback" not in out


def test_crash_while_writing_leaves_no_copy_and_no_report(tmp_path, monkeypatch, capsys):
    src = tmp_path / "contract.docx"
    make_docx(src)

    def broken_apply(src_path, dst, mapping):
        dst.write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(anon, "apply_docx", broken_apply)
    code, out = run(monkeypatch, capsys, src)
    assert code == 1
    assert not list(tmp_path.glob("ANON-*"))


def test_pdf_name_in_caps_and_in_title_case_both_replaced(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: the model listed QUINTINO VARELLA; the same name
    in title case stayed, and the exact-case text check printed nothing."""
    import fitz
    src = tmp_path / "caps.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), "CONTRATADO: QUINTINO VARELLA, CPF 417.902.385-16.", fontsize=11)
    page.insert_text((72, 300), "Assinado por Quintino Varella em 09/09/2026.", fontsize=11)
    page.insert_text((72, 330), "Host: 189-123-45-67.dsl.provedor-exemplo.net IP 189.123.45.67", fontsize=11)
    doc.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda text: [("QUINTINO VARELLA", "person")] if "QUINTINO VARELLA" in text else [])
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    assert "quintino" not in text.casefold() and "varella" not in text.casefold()
    assert "189-123-45-67" not in text and "189.123.45.67" not in text


def test_verify_catches_other_case_and_other_separators(tmp_path):
    from docx import Document
    path = tmp_path / "x.docx"
    d = Document()
    d.add_paragraph("Assinado por Quintino Varella, host 189-123-45-67.example.net")
    d.save(str(path))
    leaked = anon.verify(path, ["QUINTINO", "189.123.45.67"])
    assert set(leaked) == {"QUINTINO", "189.123.45.67"}


def test_case_variant_does_not_eat_ordinary_words(tmp_path, monkeypatch, capsys):
    """Review of #1321: a person named Clara must not turn "declara" or the
    adjective "clara" into a fake, and the run must still pass."""
    from docx import Document
    src = tmp_path / "clara.docx"
    d = Document()
    d.add_paragraph("CONTRATANTE: CLARA BASTOS, engenheira.")
    d.add_paragraph("Clara Bastos declara que a regra e clara e a declaracao vale.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda text: [("CLARA BASTOS", "person")] if "CLARA BASTOS" in text else [])
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    assert "declara que a regra e clara e a declaracao" in text
    assert "Clara Bastos" not in text and "CLARA BASTOS" not in text


def test_one_person_one_fake_in_caps_and_title_case(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: the signature list showed "Karen Irene Sofia Eva"
    and the event log "ALICE NINA SOFIA EVA" for the same signer."""
    import fitz
    src = tmp_path / "cert.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((90, 100), "Quintino Abelardo Varella", fontsize=10)
    page.insert_text((90, 112), "Assinou como parte", fontsize=9)
    page.insert_text((40, 300), "QUINTINO ABELARDO VARELLA Assinou como parte - IP: 189.123.45.67", fontsize=9)
    page.insert_text((40, 312), "static.dk.customer.provedor.net porta: 16258", fontsize=9)
    doc.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda text: [("QUINTINO ABELARDO VARELLA", "person")] if "QUINTINO" in text else [])
    mapping, kinds = anon.collect(src)
    for word in ("Quintino", "Abelardo", "Varella"):
        assert mapping[word].upper() == mapping[word.upper()], word
    assert mapping["Abelardo"] in anon.LASTS
    assert "static.dk.customer.provedor.net" in mapping
    assert "189.123.45" not in mapping  # the IP is not also an RG


def test_review2_caps_variant_accent_fake_and_keep_word_in_name(tmp_path, monkeypatch, capsys):
    """Second review of #1321: CLARA inside DECLARAÇÃO; fake Fabio for real
    Fábio; Carlos kept inside a full name."""
    from docx import Document
    src = tmp_path / "r2.docx"
    d = Document()
    d.add_paragraph("DECLARAÇÃO DE CIÊNCIA")
    d.add_paragraph("Clara Bastos e Fábio Teles assinam com Carlos Eduardo Nogueira em São Carlos.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda text: [
        (p, "person") for p in ("Clara Bastos", "Fábio Teles", "Carlos Eduardo Nogueira") if p in text])
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    dst = found_path(next(line.split(" ", 1)[1] for line in out.splitlines() if line.startswith("OUTPUT ")))
    text = anon.extract_plain(dst)
    assert "DECLARAÇÃO DE CIÊNCIA" in text
    assert "Fabio" not in text and "Fábio" not in text
    assert "Carlos Eduardo" not in text
    assert "São Carlos" in text


def test_folder_mode_one_fake_per_person_across_files(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: one signer was "Karen" in the Word file and the
    email but "Nina" in the PDF, because each file had its own map."""
    import fitz
    folder = tmp_path / "case"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), "ROSALVA TEIXEIRA MONFORTE Assinou como parte", fontsize=9)
    page.insert_text((72, 140), "Quintino Varella", fontsize=9)
    doc.save(str(folder / "certificado.pdf"))

    def model(text):
        # The model finds Quintino only in the Word file, Rosalva in both.
        found = []
        if "Quintino Abelardo Varella" in text:
            found.append(("Quintino Abelardo Varella", "person"))
        for form in ("Rosalva Teixeira Monforte", "ROSALVA TEIXEIRA MONFORTE"):
            if form in text:
                found.append((form, "person"))
        return found

    monkeypatch.setattr(anon, "ollama_identify", model)
    code, out = run(monkeypatch, capsys, folder)
    assert code == 0, out
    assert "SHARED_MAP 2 files" in out
    assert_no_real_values(out)
    outputs = [found_path(l.split(" ", 1)[1]) for l in out.splitlines() if l.startswith("OUTPUT ")]
    assert len(outputs) == 2
    texts = {p.suffix: anon.extract_plain(p) for p in outputs}
    # Found only in the Word file, replaced in the PDF too.
    assert "Quintino" not in texts[".pdf"] and "Varella" not in texts[".pdf"]
    # Same fake for Rosalva in both copies.
    import json
    map_path = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("MAPFILE ")))
    rows = json.loads(map_path.read_text())["map"]
    fake = next(r["fake"] for r in rows.values() if r["real"] == "Rosalva")
    assert fake in texts[".docx"] and fake.upper() in texts[".pdf"]
    reports = [json.loads(p.with_name(p.name + ".report.json").read_text()) for p in outputs]
    assert all(r["shared_map"].startswith("one map for 2 files") for r in reports)


def test_spelling_variant_is_one_company_and_legal_form_stays(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: one company spelled two ways one letter apart
    became 'ALFA IPE' and 'Atlas IPE'; 'IPE' had replaced 'S/A'."""
    from docx import Document
    src = tmp_path / "variant.docx"
    d = Document()
    d.add_paragraph("CONTRATANTE: PIRILAMPOS S/A, CNPJ 62.814.530/0001-77.")
    d.add_paragraph("Recebemos hoje da Pirilampus S/A o documento assinado.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda text: [
        (c, "company") for c in ("PIRILAMPOS S/A", "Pirilampus S/A") if c in text])
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    mapping, _ = anon.collect(src)
    assert mapping["PIRILAMPOS"].casefold() == mapping["Pirilampus"].casefold()
    assert "S/A" not in mapping
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(dst)
    assert text.count("S/A") == 2


def test_folder_review_findings(tmp_path, monkeypatch, capsys):
    """Review of #1322: Word lock file and a broken file must not print a
    path or stop the folder; a file where the model finds nothing still gets
    a copy but never CLEAN; report counts include names from other files;
    Fernanda and Fernando stay two people."""
    import json
    folder = tmp_path / "Caso Quintino Varella"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    (folder / "~$ntrato.docx").write_bytes(b"lock")
    (folder / "quebrado Rosalva.pdf").write_bytes(b"not a pdf")
    from docx import Document
    d = Document()
    d.add_paragraph("Recibo assinado por Quintino Varella. Fernanda e Fernando Souza testemunham.")
    d.save(str(folder / "recibo.docx"))

    def model(text):
        found = [(p, "person") for p in PEOPLE if p in text]
        if "Fernanda" in text:
            found += [("Fernanda Souza", "person"), ("Fernando Souza", "person")]
        if "Recibo" in text:
            return []  # the model lists nothing in the receipt
        return found

    monkeypatch.setattr(anon, "ollama_identify", model)
    monkeypatch.setattr(anon, "FOLDER_MODE", False)
    code, out = run(monkeypatch, capsys, folder)
    err = capsys.readouterr().err
    assert "Rosalva" not in out + err and "Quintino" not in out + err
    assert "Traceback" not in out + err
    assert "ERROR PackageNotFoundError" not in out  # the lock file is skipped, not read
    assert any(l.endswith("this file was skipped and has no copy") for l in out.splitlines())
    outputs = {found_path(l.split(" ", 1)[1]).suffix: found_path(l.split(" ", 1)[1]) for l in out.splitlines() if l.startswith("OUTPUT ")}
    assert len([l for l in out.splitlines() if l.startswith("OUTPUT ")]) == 2
    texts = [anon.docx_xml_text(found_path(l.split(" ", 1)[1])) for l in out.splitlines() if l.startswith("OUTPUT ")]
    receipt = next(t for t in texts if "Recibo" in t)
    assert "Quintino" not in receipt and "Varella" not in receipt
    reports = [json.loads(found_path(l.split(" ", 1)[1]).read_text()) for l in out.splitlines() if l.startswith("REPORT ")]
    receipt_report = next(r for r in reports if r["local_model_found"] == {"person": 0, "company": 0, "cnpj": 0})
    assert receipt_report["result"] == "REVIEW"
    assert receipt_report["values_replaced"].get("PERSON", 0) >= 2
    mapping, _ = anon.collect(folder / "originals" / "contrato.docx")
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Fernanda Souza", "person"), ("Fernando Souza", "person")])
    hits = {}
    fam = {"name:fernanda": ["Fernanda"], "name:fernando": ["Fernando"]}
    anon.merge_spelling_variants(fam)
    assert len(fam) == 2


def test_one_edit_apart():
    assert anon.one_edit_apart("pirilampos", "pirilampus")
    assert anon.one_edit_apart("souza", "sousa")
    assert anon.one_edit_apart("silva", "silvas")
    assert not anon.one_edit_apart("silva", "silveira")


def test_folder_fake_fits_pdf_box_for_value_found_in_other_file(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: the email's model listed a 3-letter company word
    that the PDF's run did not; its 7-letter fake could not fit 24 PDF boxes."""
    import fitz
    folder = tmp_path / "caso"
    folder.mkdir()
    doc = fitz.open()
    page = doc.new_page()
    for i in range(5):
        page.insert_text((72, 100 + 20 * i), f"Item {i}: Zyx confirma o pagamento.", fontsize=11)
    doc.save(str(folder / "relatorio.pdf"))
    from docx import Document
    d = Document()
    d.add_paragraph("A empresa Zyx enviou o contrato.")
    d.save(str(folder / "carta.docx"))
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda text: [("Zyx", "company")] if "enviou" in text else [("Pessoa Teste", "person")] if False else [])
    code, out = run(monkeypatch, capsys, folder)
    assert "skip-tight" not in out, out
    pdf_copy = next(p for p in sorted_copies(folder, "ANON-*.pdf"))
    assert "Zyx" not in anon.extract_plain(pdf_copy)


def test_heading_in_caps_is_not_a_name(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21 (26 files): heading words in capitals were taken
    for names; their lowercase use failed every copy."""
    from docx import Document
    src = tmp_path / "termo.docx"
    d = Document()
    d.add_paragraph("TERMO DE QUITACAO PARCIAL")
    d.add_paragraph(f"O contratante Quintino Varella assina o termo. A quitacao parcial vale hoje; outra quitacao vem depois.")
    d.add_paragraph("Contato: quintino.varella@pirilampo-eng.com.br e quintino.varella@pirilampo-eng.com.br")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Quintino Varella", "person")] if "Quintino" in t else [])
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    text = anon.docx_xml_text(found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT "))))
    assert "QUITACAO" in text and "quitacao" in text
    assert "Quintino" not in text and "quintino" not in text  # lowercase only in addresses: still a name


def test_buried_name_box_is_measured_and_never_left(tmp_path, monkeypatch, capsys):
    """Real run: 5-letter names inside longer words got an 8-letter fake that
    fit none of their boxes, and the real value stayed."""
    import fitz
    src = tmp_path / "laudo.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for i in range(4):
        page.insert_text((72, 100 + 20 * i), f"Assinado: Bastos-ME em {i}/9.", fontsize=11)
    doc.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Bastos", "person")] if "Bastos" in t else [])
    code, out = run(monkeypatch, capsys, src)
    assert "skip-tight" not in out, out
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    assert "Bastos" not in anon.extract_plain(copy)


def test_pdf_value_in_a_box_too_tight_for_any_fake_is_blanked(tmp_path, monkeypatch, capsys):
    """Real run: a footer's company name in a tiny box stayed in the copy."""
    import fitz
    src = tmp_path / "rodape.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"Contrato com {PEOPLE[0]}.", fontsize=11)
    doc.save(str(src))
    # Every fake measures far wider than any box, as in the real footer.
    monkeypatch.setattr(anon, "text_width", lambda text, size, font=None: 500.0 if text else 0.0)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "blanked tight box" in out and "skip-tight" not in out
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    assert "Varella" not in anon.extract_plain(copy)


def test_phrase_words_become_tokens_of_their_own():
    """Real run: company names found only as whole phrases stayed in PDFs,
    because the PDF writer replaces one word box at a time."""
    hits = {"Pirilampo Engenharia Verde": anon.Hit("Pirilampo Engenharia Verde", "org", 1000.0, 11.0),
            "Rosalva Teixeira Monforte": anon.Hit("Rosalva Teixeira Monforte", "name", 1000.0, 11.0)}
    anon.add_phrase_words(hits)
    assert hits["Pirilampo"].kind == "org" and hits["Verde"].kind == "org" and "Engenharia" not in hits
    assert hits["Rosalva"].kind == "name" and hits["Teixeira"].kind == "last" and hits["Monforte"].kind == "last"
    anon.MODEL_TOKENS.clear()


def test_pdf_phone_written_as_several_boxes_is_replaced(tmp_path, monkeypatch, capsys):
    import fitz
    src = tmp_path / "contato.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"Contato: {PEOPLE[0]}, telefone {PHONE}.", fontsize=11)
    doc.save(str(src))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.extract_plain(copy)
    assert "98877" not in text and "1234" not in text
    assert re.search(r"\+\d\d \(\d\d\) \d{5}-\d{4}", text), "a phone of the same shape is written"


def test_pdf_short_value_glued_to_a_mark_is_replaced(tmp_path, monkeypatch, capsys):
    import fitz
    src = tmp_path / "sigla.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"Fornecedor ZRX/DK e {PEOPLE[0]}.", fontsize=11)
    doc.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda text: ([(PEOPLE[0], "person")] if PEOPLE[0] in text else [])
                        + ([("ZRX", "company")] if "ZRX" in text else []))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    text = anon.extract_plain(found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT "))))
    assert "ZRX" not in text and "/DK" in text


def test_pdf_link_broken_over_two_lines_is_cleared(tmp_path):
    import fitz
    src = tmp_path / "link.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Veja https://portal.pirilampo-eng.com.br/docs/", fontsize=11)
    page.insert_text((72, 116), "proc/-12.345678901 para detalhes.", fontsize=11)
    doc.save(str(src))
    dst = tmp_path / "ANON-link.pdf"
    anon.apply_pdf(src, dst, {"https://portal.pirilampo-eng.com.br/docs/proc/-12.345678901": "https://example.com"})
    text = anon.extract_plain(dst)
    assert "345678901" not in text and "pirilampo" not in text and "example.com" in text


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
def test_pdf_scanned_page_has_its_names_painted_over(tmp_path):
    """Real run: scanned signature pages and letterheads showed names to OCR
    after every text box was clean. The words OCR reads are covered."""
    import fitz
    src = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page()
    big = fitz.open()
    bp = big.new_page(width=600, height=800)
    bp.insert_text((40, 100), f"Assinado por {PEOPLE[1]}", fontsize=22)
    bp.insert_text((40, 160), "Prazo de trinta dias para resposta.", fontsize=22)
    page.insert_image(page.rect, stream=bp.get_pixmap(dpi=150).tobytes("png"))  # one picture: the whole page
    doc.save(str(src))
    mapping = {PEOPLE[1]: "Elena Lima Prado", "Rosalva": "Elena", "Teixeira": "Lima", "Monforte": "Prado"}
    dst = tmp_path / "ANON-scan.pdf"
    notes = anon.apply_pdf(src, dst, mapping)
    assert any(m.startswith("covered") for m, _t in notes), notes
    hits, _unreadable = anon.ocr_check(dst, mapping)
    assert hits == []
    text = anon.ocr_png(fitz.open(dst)[0].get_pixmap(dpi=200).tobytes("png"), anon.ocr_langs())
    assert "trinta dias" in text and "Rosalva" not in text


def test_docx_python_docx_cannot_open_uses_raw_xml(tmp_path, monkeypatch, capsys):
    """Real run: one Word file listed a part that was missing; python-docx
    raised KeyError and the file was skipped."""
    src = tmp_path / "quebrado.docx"
    make_docx(src)
    with zipfile.ZipFile(src) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    rels = items["word/_rels/document.xml.rels"].decode()
    rels = rels.replace("</Relationships>",
                        '<Relationship Id="rIdZ" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/missing.png"/></Relationships>')
    items["word/_rels/document.xml.rels"] = rels.encode()
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in items.items():
            zf.writestr(n, data)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "raw XML path" in out
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    for word in ("Quintino", "Varella", CPF):
        assert word not in text


def test_style_list_is_not_checked(tmp_path):
    from docx import Document
    path = tmp_path / "x.docx"
    Document().save(str(path))  # styles.xml lists built-in names such as "Caption"
    assert anon.verify(path, ["CAPTION"]) == []


def test_extra_terms_file_replaces_undetected_address(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: a foreign company address was in 12 of 25
    copies, 7 of them CLEAN; no detector finds that format."""
    from docx import Document
    folder = tmp_path / "caso"
    folder.mkdir()
    d = Document()
    d.add_paragraph("PIRILAMPO A/S, com sede em Lindevej 17, 2900 Hellerup, Gentofte, Dinamarca.")
    d.save(str(folder / "contrato.docx"))
    (folder / "_extra_terms.txt").write_text("Lindevej 17\n2900 Hellerup\nGentofte\n", encoding="utf-8")
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("PIRILAMPO A/S", "company")] if "PIRILAMPO" in t else [])
    code, out = run(monkeypatch, capsys, folder)
    copy = next(p for p in sorted_copies(folder, "ANON-*.docx"))
    text = anon.docx_xml_text(copy)
    for real in ("Lindevej", "Hellerup", "Gentofte", "PIRILAMPO"):
        assert real not in text
    assert "A/S" in text and "Dinamarca" in text
    assert not any(p.name.startswith("ANON-_extra") for p in folder.iterdir())


def test_pdf_letterhead_picture_with_real_value_is_blanked(tmp_path, monkeypatch, capsys):
    import fitz
    src = tmp_path / "carta.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(fitz.Rect(72, 40, 400, 90), stream=text_png("Rosalva Monforte"))
    page.insert_text((72, 140), f"Prezados, segue o contrato de {PEOPLE[1]}.", fontsize=11)
    doc.save(str(src))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "blanked picture on page 1" in out


def test_folder_sorted_into_originals_and_anonymized_and_rerun(tmp_path, monkeypatch, capsys):
    """Owner 2026-10-21: at the end, originals go into originals/ and copies
    with reports into anonymized/. A rerun reads originals/ and keeps the
    earlier copies under a dated name instead of overwriting them."""
    import fitz
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    make_pdf(folder / "certificado.pdf")
    (folder / "notas.txt").write_text("not a document type the script reads")
    code, out = run(monkeypatch, capsys, folder)
    assert "SORTED originals 2 into originals/, CLEAN copies 2 into anonymized/, other copies 0 into needs-review/" in out
    assert sorted(p.name for p in (folder / "originals").iterdir()) == ["certificado.pdf", "contrato.docx"]
    assert len([p for p in (folder / "anonymized").iterdir() if not p.name.endswith(".report.json")]) == 2
    assert (folder / "notas.txt").exists()  # other files stay where they are
    assert not any(p.name.startswith("ANON-") for p in folder.iterdir())
    # Rerun on the sorted folder.
    code2, out2 = run(monkeypatch, capsys, folder)
    assert "FILE 2/2" in out2
    assert len(list(folder.glob("anonymized-previous-*"))) == 1
    assert len([p for p in (folder / "anonymized").iterdir() if not p.name.endswith(".report.json")]) == 2
    assert sorted(p.name for p in (folder / "originals").iterdir()) == ["certificado.pdf", "contrato.docx"]


def test_surname_that_is_also_a_word_is_still_replaced(tmp_path, monkeypatch, capsys):
    """Review of #1327: "Rosa" is a surname and the word "rosa"; the fence
    dropped it and the copy said CLEAN with the surname visible."""
    from docx import Document
    src = tmp_path / "Contrato Rosa.docx"
    d = Document()
    d.add_paragraph("CONTRATANTE: Quintina Rosa, CPF 417.902.385-16.")
    d.add_paragraph("A Sra. Rosa declara que a cor rosa e a fita rosa foram entregues.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Quintina Rosa", "person")] if "Quintina" in t else [])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    assert "Rosa" not in text and "Rosa" not in copy.name
    assert "cor rosa" in text  # the ordinary word stays


def test_common_words_taken_for_names_are_kept(tmp_path, monkeypatch, capsys):
    """Real run 2026-10-21: The, Agreement, Por, Este were flagged as names;
    the Title-case rule would have replaced them in every copy."""
    from docx import Document
    src = tmp_path / "acordo.docx"
    d = Document()
    d.add_paragraph("THE AGREEMENT signed by Quintino Varella.")
    d.add_paragraph("The Agreement is valid. Por este termo, the agreement ends. Este acordo vale por um ano.")
    d.save(str(src))
    # The capitals rule flags THE and AGREEMENT; the model lists only the person.
    monkeypatch.setattr(anon, "ollama_identify",
                        lambda t: [("Quintino Varella", "person")] if "Quintino" in t else [])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    assert "The Agreement is valid. Por este termo" in text
    assert "Quintino" not in text


def test_names_that_are_also_words_stay_protected(tmp_path, monkeypatch, capsys):
    """Review of #1331: Power, Will, a surname listed alone (Campos), and
    honorific forms (Sr.ª, Dr(a)., Senhora, SRA.) must never be dropped."""
    from docx import Document
    src = tmp_path / "ata.docx"
    d = Document()
    d.add_paragraph("Presentes: Samantha Power e Campos.")
    d.add_paragraph("Power signed the deed. Campos assinou. Os campos obrigatorios e outros campos.")
    d.add_paragraph("A Sr.ª Leal, a Dr(a). Mota, a Senhora Rocha e a SRA. PINTO confirmaram.")
    d.add_paragraph("O leal, o leal, a mota, a mota, a rocha, a rocha, o pinto, o pinto e o power, o power.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [
        ("Samantha Power", "person"), ("Campos", "person")] if "Samantha" in t else [])
    for word in ("Leal", "Mota", "Rocha", "PINTO"):
        assert word.lower() in {m.group(1).lower() for m in anon.HONORIFIC_NAME_RE.finditer(
            "A Sr.ª Leal, a Dr(a). Mota, a Senhora Rocha e a SRA. PINTO")}
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    assert "Power signed" not in text and "Samantha" not in text
    assert "Campos assinou" not in text


def test_surname_from_email_header_is_protected(tmp_path, monkeypatch, capsys):
    """Second review of #1331: From "Ana Campos", body uses "campos" twice as
    a word and is signed "Campos"; the model listed nobody."""
    from email.message import EmailMessage
    from email import policy
    msg = EmailMessage(policy=policy.default)
    msg["From"] = "Ana Campos <ana@example.org>"
    msg["To"] = "Equipe <equipe@example.org>"
    msg["Subject"] = "Formulario"
    msg.set_content("Preencha os campos obrigatorios e os campos opcionais da Pirilampo.\nAtt., Campos\n")
    src = tmp_path / "msg.eml"
    src.write_bytes(msg.as_bytes())
    # The model lists an unrelated company, not the person.
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Pirilampo", "company")] if "Pirilampo" in t else [])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.mail_plain(copy)
    assert "Att., Campos" not in text and "Ana Campos" not in text


def gliner_stub(spans):
    """Stand-in for GLiNER: returns the given (span, label) pairs found in the text."""
    import re

    def fake(text):
        flat = re.sub(r"\s+", " ", text)
        return [(s, lab) for s, lab in spans if s in flat]
    return fake


def test_context_rejects_heading_word_and_keeps_company_form(tmp_path, monkeypatch, capsys):
    """Owner 2026-10-21: SAIBAM became ZOE; one company appeared as Alfa Brasil,
    Alfa Norte, and Alfa IPE. With context, SAIBAM stays, and only the
    distinctive word of the company changes: Brasil, Ltda, A/S stay."""
    from docx import Document
    src = tmp_path / "procuracao.docx"
    d = Document()
    d.add_paragraph("SAIBAM quantos este instrumento virem que PIRILAMPOS BRASIL LTDA. outorga poderes.")
    d.add_paragraph("A Pirilampos Brasil, controlada pela Pirilampos A/S, nomeia Quintino Varella.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    monkeypatch.setattr(anon, "gliner_entities", gliner_stub([
        ("PIRILAMPOS BRASIL LTDA.", "organization"), ("Pirilampos Brasil", "organization"),
        ("Pirilampos A/S", "organization"), ("Quintino Varella", "person")]))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Quintino Varella", "person")] if "Quintino" in t else [])
    # The local model's yes/no answer, without Ollama (a busy model timed out
    # and kept SAIBAM as a name).
    monkeypatch.setattr(anon, "llm_says_not_a_name", lambda word, text: word.upper() == "SAIBAM")
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    assert "SAIBAM quantos" in text
    assert "Pirilampos" not in text and "PIRILAMPOS" not in text and "Quintino" not in text
    import re
    fake = re.search(r"que (\w+) BRASIL LTDA", text).group(1)
    assert f"A {fake.title()} Brasil, controlada pela {fake.title()} A/S" in text


def test_gliner_finds_what_the_model_missed(tmp_path, monkeypatch, capsys):
    """GLiNER found a first-name variant, two addresses, and a foreign phone
    the pipeline had left in a real 26-file case."""
    from docx import Document
    src = tmp_path / "carta.docx"
    d = Document()
    d.add_paragraph("Dear Ms. Kamillo, CONTRATANTE: QUINTINO VARELLA.")
    d.add_paragraph("Office: Lindevej 17, DK-2900 Hellerup. Phone +56 9 4075 9809.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    monkeypatch.setattr(anon, "gliner_entities", gliner_stub([
        ("Ms. Kamillo", "person"), ("QUINTINO VARELLA", "person"),
        ("Lindevej 17, DK-2900 Hellerup", "street address"), ("+56 9 4075 9809", "phone number"),
        ("CONTRATANTE", "person")]))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("QUINTINO VARELLA", "person")] if "QUINTINO" in t else [])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    for real in ("Kamillo", "Lindevej", "Hellerup", "4075 9809", "QUINTINO"):
        assert real not in text, real
    assert "CONTRATANTE" in text  # a role GLiNER called a person stays


def test_gliner_second_look_flags_leftover_person(tmp_path, monkeypatch, capsys):
    from docx import Document
    src = tmp_path / "ata.docx"
    d = Document()
    d.add_paragraph("Presente o Sr. Quintino Varella e a sra rosalva.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    stub = gliner_stub([("Quintino Varella", "person")])

    def gl(text):
        out = stub(text)
        if "Bartolomeu Teixeira" in text:  # a name the first pass never saw, seen only in the copy
            out.append(("Bartolomeu Teixeira", "person"))
        return out

    monkeypatch.setattr(anon, "gliner_entities", gl)
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Quintino Varella", "person")] if "Quintino" in t else [])
    real_apply = anon.apply_docx

    def apply_and_inject(src_path, dst, mapping):
        notes = real_apply(src_path, dst, mapping)
        anon.patch_docx_xml(dst, {"Presente": "Presente Bartolomeu Teixeira e"})
        return notes

    monkeypatch.setattr(anon, "apply_docx", apply_and_inject)
    code, out = run(monkeypatch, capsys, src)
    assert code == 1 and "REVIEW" in out


def test_gliner_chunks_never_exceed_its_reading_window(monkeypatch):
    """Review of #1332: GLiNER ignores words past about 384 per chunk."""
    seen = []

    class FakeModel:
        def predict_entities(self, chunk, labels, threshold):
            seen.append(len(chunk.split()))
            return []

    monkeypatch.setattr(anon, "_GLINER", [FakeModel()])
    anon.gliner_entities(" ".join(f"w{i}" for i in range(1000)))
    assert seen and max(seen) <= 300 and sum(seen) >= 1000
    # ID-dense text: GLiNER counts every dot and dash; windows stay under its limit.
    import re
    seen_units = []

    class UnitModel:
        def predict_entities(self, chunk, labels, threshold):
            seen_units.append(len(re.findall(r"\w+(?:[-_]\w+)*|\S", chunk)))
            return []

    monkeypatch.setattr(anon, "_GLINER", [UnitModel()])
    anon.gliner_entities(" ".join(["CPF: 123.456.789-00; RG: 12.345.678; Data: 01/02/2026;"] * 120))
    assert seen_units and max(seen_units) <= 300


def test_review_1332_names_never_dropped(tmp_path, monkeypatch, capsys):
    """Review of #1332: ANA CÂMARA (names that were on a word list), and a
    name after Sr. deep in a long paragraph that the context judges did not
    confirm, must be replaced."""
    from docx import Document
    src = tmp_path / "recibo.docx"
    d = Document()
    filler = " ".join(["o valor foi pago conforme combinado"] * 70)
    d.add_paragraph(f"Recebi de ANA CÂMARA a quantia de dez reais. {filler} ao Sr. Bartolomeu Vasconcelos hoje.")
    d.add_paragraph("CONTRATANTE: PIRILAMPO LTDA.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    monkeypatch.setattr(anon, "gliner_entities", gliner_stub([("ANA CÂMARA", "person")]))
    monkeypatch.setattr(anon, "llm_says_not_a_name", lambda word, text: True)  # worst case: judge says no
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("PIRILAMPO", "company")] if "PIRILAMPO" in t else [])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    text = anon.docx_xml_text(copy)
    for real in ("ANA", "CÂMARA", "Bartolomeu", "Vasconcelos", "PIRILAMPO"):
        assert real not in text, real


def test_surname_only_in_file_name_is_replaced(tmp_path, monkeypatch, capsys):
    """Second review of #1330: the word appears in lowercase in the text and
    as a surname only in the file name."""
    from docx import Document
    src = tmp_path / "Contrato Rosa.docx"
    d = Document()
    d.add_paragraph("A fita rosa e a caixa rosa foram entregues.")
    d.save(str(src))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Rosa", "person")])
    code, out = run(monkeypatch, capsys, src)
    copy = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    assert "Rosa" not in copy.name


def test_sort_moves_only_this_runs_copies_and_parks_failed_ones(tmp_path, monkeypatch, capsys):
    """Review of #1330: an old ANON- file with no report, and copies that did
    not pass, must not land in the folder to share."""
    import json
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    from docx import Document
    d = Document()
    d.add_paragraph("Documento sem nomes que o modelo veja.")
    d.save(str(folder / "vazio.docx"))
    (folder / "ANON-antigo Quintino.docx").write_bytes(b"old copy, no report")
    code, out = run(monkeypatch, capsys, folder)
    shared = sorted(p.name for p in (folder / "anonymized").iterdir())
    parked = sorted(p.name for p in (folder / "needs-review").iterdir())
    assert not any("antigo" in n for n in shared + parked)
    assert (folder / "ANON-antigo Quintino.docx").exists()
    assert any(n.startswith("ANON-vazio") for n in parked)  # REVIEW: model listed nothing
    assert all(json.loads((folder / "anonymized" / n).read_text())["result"] == "CLEAN"
               for n in shared if n.endswith(".report.json"))


def test_digit_check_ignores_unrelated_long_numbers(tmp_path):
    from docx import Document
    path = tmp_path / "n.docx"
    d = Document()
    d.add_paragraph("Processo 0001894123456700001 e protocolo 12345678")
    d.save(str(path))
    assert anon.verify(path, ["189.123.45.67"]) == []


def test_copy_keeps_input_name_with_names_replaced(tmp_path, monkeypatch, capsys):
    """Owner 2026-10-21: the copy is ANON- plus the input's name. Real names
    in that name get the same fakes as the content, the output never prints
    the name, and the leak check reads it."""
    src = tmp_path / "Contrato de Trabalho (Quintino Varella) v1.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "Contrato de Trabalho" not in out and "Quintino" not in out
    copies = [p for p in tmp_path.glob("ANON-*") if not p.name.endswith(".report.json")]
    assert len(copies) == 1
    name = copies[0].name
    assert name.startswith("ANON-Contrato de Trabalho (") and name.endswith(") v1.docx")
    assert "Quintino" not in name and "Varella" not in name
    assert (tmp_path / (name + ".report.json")).exists()


def test_file_name_underscores_lowercase_long_and_collisions(tmp_path, monkeypatch, capsys):
    """Review of #1323: QUINTINO_VARELLA and lowercase names in a file name;
    a name only in the file name; a 250-byte name; two inputs whose names map
    to the same copy name."""
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "QUINTINO_VARELLA_contrato.docx")
    make_docx(folder / "recibo rosalva monforte.docx")
    make_docx(folder / ("Contrato " + "x" * 240 + ".docx"))
    from docx import Document
    d = Document()
    d.add_paragraph("Documento sem nomes no texto.")
    d.save(str(folder / "Declaracao Abelardo Teles.docx"))

    def model(text):
        found = [(p, "person") for p in PEOPLE if p in text]
        if "Abelardo Teles" in text:
            found.append(("Abelardo Teles", "person"))
        return found or [("Rosalva Teixeira Monforte", "person")]

    monkeypatch.setattr(anon, "ollama_identify", model)
    code, out = run(monkeypatch, capsys, folder)
    err = capsys.readouterr().err
    assert "Traceback" not in out + err
    names = sorted(p.name for p in sorted_copies(folder, "ANON-*") if not p.name.endswith(".report.json"))
    assert len(names) == 4, names
    joined = " ".join(names).casefold()
    for real in ("quintino", "varella", "rosalva", "monforte", "abelardo", "teles"):
        assert real not in joined, real
    assert any("_contrato.docx" in n for n in names)  # underscores kept
    assert all(len(n.encode()) + len(".report.json") <= 255 for n in names)
    assert all(next(iter(sorted_copies(folder, n + ".report.json"))).exists() for n in names)


def test_two_inputs_same_copy_name_both_kept(tmp_path):
    """joao@ and pedro@ both become "ana": two inputs, one copy name."""
    anon.COPY_NAMES_TAKEN.clear()
    a = anon.output_path_for(tmp_path / "Contrato joao.pdf", None, {"joao": "ana", "pedro": "ana"})
    b = anon.output_path_for(tmp_path / "Contrato pedro.pdf", None, {"joao": "ana", "pedro": "ana"})
    assert a.name == "ANON-Contrato ana.pdf"
    assert b.name != a.name and b.name.startswith("ANON-Contrato ana (")
    # Second review of #1323: two long names cut to the same text.
    long_a = anon.output_path_for(tmp_path / ("Contrato " + "x" * 240 + " v1.docx"), None, {})
    long_b = anon.output_path_for(tmp_path / ("Contrato " + "x" * 240 + " v2.docx"), None, {})
    assert long_a.name != long_b.name
    assert all(len(p.name.encode()) + len(".report.json") <= 255 for p in (long_a, long_b))


def test_leak_check_reads_the_copy_name(tmp_path):
    from docx import Document
    path = tmp_path / "ANON-Recibo Quintino.docx"
    d = Document()
    d.add_paragraph("texto limpo")
    d.save(str(path))
    assert anon.verify(path, ["Quintino"]) == ["Quintino"]


def test_same_stem_pdf_and_docx_get_two_reports(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "Contrato.docx")
    make_pdf(folder / "Contrato.pdf")
    code, out = run(monkeypatch, capsys, folder)
    assert sorted(p.name for p in sorted_copies(folder, "ANON-*")) == [
        "ANON-Contrato.docx", "ANON-Contrato.docx.report.json",
        "ANON-Contrato.pdf", "ANON-Contrato.pdf.report.json",
    ]


def test_attachment_type_never_prints_file_name_text():
    assert anon.attachment_type("CV maria.souza") == "other"
    assert anon.attachment_type("contrato.PDF") == ".pdf"


# ---- Checks added after the owner's hand review of a 26-file case (2026-10-21).

def test_pdf_fake_keeps_color_and_weight(tmp_path, monkeypatch, capsys):
    """Owner: in one copy the fake company word was black and BRAZIL next to
    it red. The original heading was red bold; the writer drew every fake in
    black regular Helvetica."""
    import fitz
    import json
    src = tmp_path / "heading.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "PIRILAMPO ENGENHARIA BRAZIL", fontname="hebo", fontsize=14, color=(0.62, 0, 0.05))
    page.insert_text((72, 140), f"Empregado: {PEOPLE[0]}.", fontsize=11)
    doc.save(str(src))
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    spans = [s for s in anon.page_spans(fitz.open(dst)[0]) if s["bbox"][1] < 120]
    assert spans and all(f"{s['color']:06x}" == "9e000c" or f"{s['color']:06x}" == "9f000d" or
                         abs(((s["color"] >> 16) & 255) - 158) <= 2 for s in spans), [f"{s['color']:06x}" for s in spans]
    assert all(anon.span_is_bold(s) for s in spans)
    report = json.loads(found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("REPORT "))).read_text())
    assert report["checks"]["look"] == "pass"


def test_docx_replacement_keeps_each_runs_format(tmp_path, monkeypatch, capsys):
    """Before: the whole paragraph went into its first run, so a red word next
    to a name took the name's color."""
    from docx import Document
    from docx.shared import RGBColor
    src = tmp_path / "runs.docx"
    d = Document()
    p = d.add_paragraph("CONTRATANTE: ")
    name = p.add_run("PIRILAMPO ENGENHARIA")
    name.bold = True
    red = p.add_run(" BRAZIL")
    red.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    d.save(str(src))
    code, out = run(monkeypatch, capsys, src)
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    runs = Document(str(dst)).paragraphs[0].runs
    assert runs[0].text == "CONTRATANTE: "
    assert runs[1].bold and "PIRILAMPO" not in runs[1].text and runs[1].text.strip()
    assert runs[2].text == " BRAZIL" and str(runs[2].font.color.rgb) == "C00000"
    assert "LOOK" not in out


def test_look_check_flags_lost_color(tmp_path):
    import fitz
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    for path, color in ((a, (1, 0, 0)), (b, (0, 0, 0))):
        doc = fitz.open()
        doc.new_page().insert_text((72, 100), "TITULO VERMELHO", fontsize=14, color=color)
        doc.save(str(path))
    assert anon.pdf_look_changes(a, b) == 1
    assert anon.pdf_look_changes(a, a) == 0


def test_tracked_deletion_is_found_and_replaced(tmp_path, monkeypatch, capsys):
    """Text deleted with Track Changes is still in the file; Word shows it
    with All Markup."""
    from docx import Document
    src = tmp_path / "tracked.docx"
    d = Document()
    d.add_paragraph("Contrato de trabalho.")
    d.save(str(src))
    with zipfile.ZipFile(src) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    xml = items["word/document.xml"].decode()
    deleted = ('<w:p><w:del w:id="1" w:author="x" w:date="2026-01-01T00:00:00Z"><w:r>'
               f'<w:delText>{PEOPLE[0]}</w:delText></w:r></w:del></w:p>')
    xml = xml.replace("<w:sectPr", deleted + "<w:sectPr", 1)
    items["word/document.xml"] = xml.encode()
    with zipfile.ZipFile(src, "w") as zf:
        for n, data in items.items():
            zf.writestr(n, data)
    code, out = run(monkeypatch, capsys, src)
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    assert "Quintino" not in anon.docx_archive_text(dst)


def test_fake_never_contains_a_real_name():
    """Real run: the fake "Helena" held the real first name "Helen"."""
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("Helen", "Elen", "Clar")}
    mapping, kinds = anon.build_map(hits)
    for fake in mapping.values():
        assert not any(r in fake.casefold() for r in ("helen", "elen", "clar")), fake
    assert anon.check_map({"Helen": "Helena"}, {"Helen": "name"}) == {"Helen": "fake_has_real"}


def test_no_fake_is_shared_even_when_the_pool_runs_out():
    """Real run: 46 first names for about 100 people; "Clara" stood for 5
    different real words, and "IPE" for two companies."""
    import itertools
    words = ["".join(p).title() for p in itertools.islice(itertools.product("bcdfgk", "aeiou", "lmnr", "aeiou"), 400)]
    hits = {w: anon.Hit(w, "org" if i % 2 else "name", 1000.0, 11.0) for i, w in enumerate(words)}
    hits.update({w.upper(): anon.Hit(w.upper(), "org", 1000.0, 11.0) for w in words[:50]})
    mapping, kinds = anon.build_map(hits)
    problems = anon.check_map(mapping, kinds)
    assert not [k for k in problems.values() if k == "shared_fake"]
    folded = [f.casefold() for t, f in mapping.items() if t.isalpha() and t.istitle()]
    assert len(folded) == len(set(folded))


def test_one_family_one_fake_even_in_a_tight_box():
    """Karen and Nina: the capitals form did not fit the box and got its own
    fake. Now the family shares one fake and the PDF writer shrinks it."""
    hits = {"Rosalva": anon.Hit("Rosalva", "name", 1000.0, 11.0),
            "ROSALVA": anon.Hit("ROSALVA", "name", 22.0, 11.0)}
    mapping, kinds = anon.build_map(hits)
    assert mapping["Rosalva"].casefold() == mapping["ROSALVA"].casefold()
    assert not anon.check_map(mapping, kinds)


def test_word_found_as_person_and_company_is_one_company():
    """Real run: the counterparty company in capitals became "Irene"; a
    firm partner's surname was "Leandro" and "KAPPA"."""
    hits = {"Monforte": anon.Hit("Monforte", "last", 1000.0, 11.0),
            "MONFORTE": anon.Hit("MONFORTE", "org", 1000.0, 11.0),
            "Monforte Advogados": anon.Hit("Monforte Advogados", "org", 1000.0, 11.0)}
    mapping, kinds = anon.build_map(hits)
    assert mapping["Monforte"].casefold() == mapping["MONFORTE"].casefold()
    assert mapping["Monforte"] in anon.ORGS
    assert mapping["Monforte Advogados"] == mapping["Monforte"] + " Advogados"
    assert not anon.check_map(mapping, kinds)


def test_capitals_word_inside_a_gliner_company_gets_a_company_fake(tmp_path, monkeypatch, capsys):
    from docx import Document
    src = tmp_path / "contraparte.docx"
    d = Document()
    d.add_paragraph("A contraparte PIRILAMPO BRASIL assina este termo com Quintino Varella.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    monkeypatch.setattr(anon, "gliner_entities", gliner_stub([
        ("PIRILAMPO BRASIL", "organization"), ("Quintino Varella", "person")]))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("Quintino Varella", "person")] if "Quintino" in t else [])
    code, out = run(monkeypatch, capsys, src)
    mapping, kinds = anon.collect(src)
    assert kinds["PIRILAMPO"] == "org"
    assert mapping["PIRILAMPO"].title() in anon.ORGS


def test_company_made_only_of_kept_words_is_not_in_the_map():
    """Real run: "National Mining Agency" had a "fake" equal to itself and
    failed the text check in every copy."""
    hits = {"National Mining Agency": anon.Hit("National Mining Agency", "org", 1000.0, 11.0)}
    mapping, kinds = anon.build_map(hits)
    assert "National Mining Agency" not in mapping


def test_public_bodies_and_template_words_are_not_names():
    """Real case 2026-10-21: ANVISA, ANM, ANA, CNAE, ABNT, INSS, CPF and deed
    words (QUANTOS, VIREM, PRESENTS) got fakes; the legal opinion lost its
    agencies. The person in capitals next to them is still replaced."""
    lines = [
        "the Brazilian Health Regulatory Agency (ANVISA) and the ANM rules".split(),
        "the National Water and Sanitation Agency (ANA) and CNAE codes".split(),
        "certified under ABNT NBR 15784 with INSS and IRRF withheld".split(),
        "SAIBAM TODOS QUANTOS ESTE INSTRUMENTO VIREM que ROSALVA assina".split(),
        "inscrita no CPF sob o nº 1 representada pela OUTORGANTE".split(),
    ]
    hits = {}
    anon.scan_word_lines(lines, hits)
    # The local model lists agencies as companies; those come in as org hits.
    for word in ("ANVISA", "ANM", "Siscomex", "CONAMA", "IBAMA"):
        hits[word] = anon.Hit(word, "org", 1000.0, 11.0)
    texts = [" ".join(" ".join(l) for l in lines)]
    anon.drop_public_terms(hits, texts)
    assert "ROSALVA" in hits
    for word in ("ANVISA", "ANM", "ANA", "CNAE", "ABNT", "INSS", "IRRF", "QUANTOS",
                 "VIREM", "OUTORGANTE", "Siscomex", "CONAMA", "IBAMA"):
        assert word not in hits, word
    more = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("GRANTEE", "COVENANTED", "BPTO", "LATAM", "CONTRATANTES")}
    anon.drop_public_terms(more, texts)
    assert more == {}


def test_ana_is_a_name_unless_the_water_agency_is_named():
    hits = {"ANA": anon.Hit("ANA", "name", 1000.0, 11.0)}
    anon.drop_public_terms(hits, ["Contrato com ANA ROSALVA, brasileira"])
    assert "ANA" in hits
    anon.drop_public_terms(hits, ["Agência Nacional de Águas (ANA)"])
    assert "ANA" not in hits


def test_name_is_not_replaced_inside_a_longer_word():
    """Real case 2026-10-21: the name CLAUS was replaced inside CLAUSE
    ("SALESE 7.1"), which showed the real name; "Environment" inside
    "Environmental". A name glued to text in another case still goes."""
    mapping = {"CLAUS": "TOMAS", "Claus": "Tomas", "Environment": "Estrela", "Silva": "Prado"}
    text = "CLAUSE 7.1 binds CLAUS. Clause 2 names Claus. Environmental Environment. MariaSilva"
    out = anon.replace_tokens(text, mapping)
    assert out == "CLAUSE 7.1 binds TOMAS. Clause 2 names Tomas. Environmental Estrela. MariaPrado"
    assert anon.verify_text("CLAUSE 7.1 and Environmental work", ["CLAUS", "Environment"]) == []
    assert anon.verify_text("signed by CLAUS", ["CLAUS"]) == ["CLAUS"]


def test_possessive_is_the_same_person():
    """Real case: Quaresma's became Karen and Quaresma’s became Beatriz, both
    without the 's; one man read as three people."""
    hits = {}
    anon.consider(hits, "Quaresma's", "name", 1000.0, 11.0)
    hits["Quaresma’s"] = anon.Hit("Quaresma’s", "last", 1000.0, 11.0)
    hits["Quaresma"] = anon.Hit("Quaresma", "last", 1000.0, 11.0)
    anon.merge_possessives(hits)
    assert set(hits) == {"Quaresma"}
    mapping, _ = anon.build_map(hits)
    fake = mapping["Quaresma"]
    out = anon.replace_tokens("Mr. Quaresma's wife and Mr. Quaresma’s email, not Mr. Quaresma", mapping)
    assert out == f"Mr. {fake}'s wife and Mr. {fake}’s email, not Mr. {fake}"


def test_law_firm_document_numbers_are_found():
    """Real case: a case number stayed in 63 subjects and iManage numbers in
    6 file names."""
    hits = {}
    anon.scan_text_blobs("Re: Alfa [GED-DOCS.FID1263742] KL/Ref.: 1992/2\n"
                         "Procuracao bilingue(2755099.1) - v1", hits)
    for value in ("FID1263742", "1992/2", "2755099.1", "KL"):
        assert value in hits, value
    mapping, _ = anon.build_map(hits)
    assert all(mapping[v] != v for v in ("FID1263742", "1992/2", "2755099.1", "KL"))
    # The firm's initials before "/Ref" become two other capital letters.
    assert len(mapping["KL"]) == 2 and mapping["KL"].isupper() and mapping["KL"].isalpha()
    assert anon.replace_tokens("KL/Ref.: 1992/2 and KLA", mapping).startswith(mapping["KL"] + "/Ref.: ")
    assert anon.replace_tokens("KL/Ref.: 1992/2 and KLA", mapping).endswith(" KLA")


def test_fake_first_name_matches_the_persons_gender():
    """Real case: two men, called Mr. and Sr. in the originals, read as
    "Mr. Joana" and "Mr. Maia"."""
    texts = ["JOAQUIM PEREIRA SOUTO, brasileiro, casado, empresário. Mr. Bartolomeu signed.",
             "ROSALVA DUTRA, brasileira, casada. Sra. Quitéria agreed. Noel Xavier wrote."]
    anon.learn_genders(texts)
    assert anon.GENDERS == {"joaquim": "m", "bartolomeu": "m", "rosalva": "f", "quiteria": "f"}
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0)
            for w in ("JOAQUIM", "Bartolomeu", "ROSALVA", "Quitéria", "Noel")}
    mapping, _ = anon.build_map(hits)
    for w in ("JOAQUIM", "Bartolomeu"):
        assert anon.title_words(mapping[w]) not in anon.FEMALE_FIRSTS, (w, mapping[w])
    for w in ("ROSALVA", "Quitéria"):
        assert anon.title_words(mapping[w]) not in anon.MALE_FIRSTS, (w, mapping[w])
    anon.GENDERS.clear()


def test_gender_from_signature_pronouns_and_greetings():
    """Real case: a woman whose only clue was "(She/Her)" got the male fake
    Gabriel."""
    anon.learn_genders([
        "Best regards,\nGraziela Pontes Dutra | Lawyer (She/Her)\nT. 555",
        "Bartolomeu Souto\nAdvogado\n",
        "Obrigada, Quiteria\nDe: Rosalvo Lima",
        "Prezado Teodoro, segue o contrato.",
        "Lina Prata\nCEO\nBruno Nunes (he/him)",
    ])
    assert anon.GENDERS["graziela"] == "f"
    assert anon.GENDERS["bartolomeu"] == "m"
    assert anon.GENDERS["quiteria"] == "f"
    assert anon.GENDERS["teodoro"] == "m"
    assert anon.GENDERS["bruno"] == "m"
    # A clue on the next line belongs to the next name, not this one.
    assert "lina" not in anon.GENDERS
    anon.GENDERS.clear()


def test_earlier_fake_of_the_wrong_gender_is_replaced():
    anon.learn_genders(["Mr. Bartolomeu called."])
    kinds = {"Bartolomeu": "name"}
    assert anon.usable_pins({"Bartolomeu": "Joana"}, kinds) == {}
    assert anon.usable_pins({"Bartolomeu": "Jonas"}, kinds) == {"Bartolomeu": "Jonas"}
    anon.GENDERS.clear()


def test_id_numbers_without_dots_are_found():
    """Real case 2026-10-31: an RG after "n°" (degree sign), a Danish company
    number after "Commercial Register No." and an employee number stayed."""
    hits = {}
    anon.scan_text_blobs("RG n° 58214706 expedida pela SSP; Commercial Register No. 41830276; "
                         "sob o nº 41830276; employee no. 000123, CBO 142105; CVR-nr. 30417259", hits)
    for v in ("58214706", "41830276", "000123", "30417259"):
        assert v in hits, v
    assert "142105" not in hits
    mapping, _ = anon.build_map(hits)
    for v in ("58214706", "41830276", "000123"):
        assert mapping[v] != v and len(mapping[v]) == len(v) and mapping[v].isdigit(), v


def test_signature_log_coordinates_are_masked():
    """Real case: the D4Sign log carried the signer's latitude and longitude."""
    hits = {}
    anon.scan_text_blobs("Geolocalização: -23.5505199, -46.6333094 (IP 203.0.113.4). Valor: R$ 1.234,56; taxa 12.5%", hits)
    assert "-23.5505199" in hits and "-46.6333094" in hits
    assert not any(k in hits for k in ("1.234", "12.5"))
    mapping, _ = anon.build_map(hits)
    assert mapping["-23.5505199"] != "-23.5505199" and mapping["-23.5505199"].startswith("-") and len(mapping["-23.5505199"]) == len("-23.5505199")


def test_address_passport_and_register_in_a_translation_are_found():
    """Real case 2026-10-31: a sworn translation kept a home address over
    line breaks, a CEP with a dot, two passports and a register number."""
    text = ("residente e domiciliado à Rua \n Quintiliano da Rocha 1419, apto. \n122, Bairro, "
            "CEP 13.416-253. Passaporte dinamarquês de número 209744210. "
            "Número do Registro Comercial:  70895528. Sede na Avenida Doutor Paulo de Abreu.")
    hits = {}
    anon.scan_text_blobs(text, hits)
    for v in ("Quintiliano", "Rocha", "1419", "122", "13.416-253", "209744210", "70895528", "Abreu"):
        assert v in hits, v
    assert "Doutor" not in hits and "Paulo" not in hits


def test_hash_with_a_typographic_ligature_is_found():
    """A signature log's PDF text wrote "ff" inside a SHA-512 as the
    ligature ﬀ, so the hash was two short runs and stayed in the copy."""
    h = "4746" + "\ufb00" + "a1" * 61
    hits = {}
    anon.scan_text_blobs("Hash do documento original (SHA512):" + h + " fim", hits)
    assert h in hits and hits[h].kind == "hex"


def test_language_names_are_kept():
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("Portuguese", "English", "PoA", "Rosalva")}
    anon.drop_public_terms(hits, ["translated into Portuguese; the PoA"])
    assert set(hits) == {"Rosalva"}


def test_gliner_lowercase_phrase_is_not_a_company():
    assert anon.distinctive_words("procuracao para todos os fins", "organization") == []
    assert anon.distinctive_words("Pirilampo Engenharia", "organization") == ["Pirilampo"]


def test_groups_and_map_check_printed_without_real_values(tmp_path, monkeypatch, capsys):
    src = tmp_path / "c.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    assert code == 0, out
    assert "MAP_CHECK fake_has_real 0, shared_fake 0, two_fakes 0, company_form 0" in out
    assert any(l.startswith("GROUPS ") for l in out.splitlines())
    assert_no_real_values(out)


def test_map_problem_sends_the_copy_to_review(tmp_path, monkeypatch, capsys):
    src = tmp_path / "c.docx"
    make_docx(src)
    monkeypatch.setattr(anon, "check_map", lambda m, k: {"Rosalva": "shared_fake"})
    code, out = run(monkeypatch, capsys, src)
    assert out.strip().endswith("REVIEW")
    assert "one fake stands for two different people or companies" in out
    assert_no_real_values(out)


def test_map_sync_flags_a_copy_edited_by_hand(tmp_path, monkeypatch, capsys):
    """Owner: after hand fixes the map still held the old fakes."""
    from docx import Document
    src = tmp_path / "c.docx"
    make_docx(src)
    code, out = run(monkeypatch, capsys, src)
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    mapping, kinds = anon.collect(src)
    assert anon.missing_fakes(src, dst, mapping, kinds) == []
    d = Document(str(dst))
    for p in d.paragraphs:
        for r in p.runs:
            r.text = r.text.replace(mapping["Rosalva"], "Outra")
    d.save(str(dst))
    assert "Rosalva" in anon.missing_fakes(src, dst, mapping, kinds)


def test_review_1333_lowercase_person_from_gliner_is_still_replaced(tmp_path, monkeypatch, capsys):
    from docx import Document
    src = tmp_path / "nota.docx"
    d = Document()
    d.add_paragraph("oi, falei hoje com o bartolomeu vasconcelos sobre o contrato.")
    d.save(str(src))
    monkeypatch.setattr(anon, "GLINER_MODEL", "stub")
    monkeypatch.setattr(anon, "gliner_entities", gliner_stub([("bartolomeu vasconcelos", "person")]))
    monkeypatch.setattr(anon, "ollama_identify", lambda t: [("PIRILAMPO", "company")])
    code, out = run(monkeypatch, capsys, src)
    dst = found_path(next(l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("OUTPUT ")))
    assert "bartolomeu" not in anon.docx_archive_text(dst)


def test_review_1333_tracked_changes_keep_their_side():
    """A fake must stay in the deleted text or the visible text it replaces."""
    mapping = {"Ana Rocha": "Lara Duarte", "Ana": "Lara", "Rocha": "Duarte"}
    xml = ('<w:p><w:del><w:r><w:delText>Ana </w:delText></w:r></w:del>'
           '<w:r><w:t>Rocha assina</w:t></w:r></w:p>')
    out = anon.rewrite_xml_paragraphs(xml, mapping)
    assert "<w:delText>Lara </w:delText>" in out or "<w:delText xml:space=\"preserve\">Lara </w:delText>" in out
    assert ">Duarte assina</w:t>" in out


def test_review_1333_moved_space_is_preserved():
    mapping = {"Rosalva Monforte": "Lara Duarte"}
    xml = '<w:p><w:r><w:t>Rosalva</w:t></w:r><w:r><w:t>Monforte assina</w:t></w:r></w:p>'
    out = anon.rewrite_xml_paragraphs(xml.replace("Rosalva<", "Rosalva <").replace(">Monforte", ">Monforte"), mapping)
    # "Rosalva " + "Monforte assina": the second run keeps " assina" with its space.
    assert 'xml:space="preserve"> assina</w:t>' in out


def test_review_1333_three_letter_real_name_is_not_inside_a_fake():
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("ANA", "Ana", "Cid", "Ivo")}
    for _ in range(5):
        anon._SECRET = None
        mapping, kinds = anon.build_map(hits)
        for real, fake in mapping.items():
            assert not any(r in fake.casefold() for r in ("ana", "cid", "ivo")), (real, fake)


# ---- Case memory (owner 2026-10-21): a rerun keeps the earlier decisions.

def test_rerun_keeps_fakes_kept_words_and_added_values(tmp_path, monkeypatch, capsys):
    import json
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    code, out = run(monkeypatch, capsys, folder)
    assert code == 0, out
    path = anon.case_memory_path(folder)
    memory = json.loads(path.read_text())
    first_fake = memory["pins"]["Rosalva"]
    # By hand: a word restored, a value added, one fake changed by hand.
    memory["keep"] = ["Liana"]
    memory["extra"] = ["Rua Inventada 77"]
    for token in list(memory["pins"]):
        if token.casefold() == "quintino":
            memory["pins"][token] = anon.same_case("Bruno", token)
    path.write_text(json.dumps(memory))
    from docx import Document
    d = Document(str(folder / "originals" / "contrato.docx"))
    d.add_paragraph("Endereco: Rua Inventada 77.")
    d.save(str(folder / "originals" / "contrato.docx"))
    code, out = run(monkeypatch, capsys, folder)
    assert "MEMORY reused" in out
    assert_no_real_values(out)
    mapping = json.loads(path.read_text())["pins"]
    assert mapping["Rosalva"] == first_fake
    assert mapping["Quintino"] == "Bruno"
    assert "Liana" not in mapping
    copy = next(p for p in sorted_copies(folder, "contrato.docx") if p.suffix == ".docx")
    text = anon.docx_xml_text(copy)
    assert "Inventada" not in text and "Liana Tecidos" in text and "Bruno" in text


def test_rerun_splits_a_fake_two_people_shared():
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("Rosalva", "Quintino", "Abelardo")}
    pins = {"Rosalva": "Clara", "Quintino": "Clara", "Abelardo": "Bruno"}
    mapping, kinds = anon.build_map(hits, pins)
    assert mapping["Abelardo"] == "Bruno"
    assert [mapping["Rosalva"], mapping["Quintino"]].count("Clara") == 1
    assert not anon.check_map(mapping, kinds)


def test_memory_lives_with_the_maps_and_is_private(tmp_path):
    import os
    import stat
    folder = tmp_path / "caso"
    folder.mkdir()
    anon.save_case_memory(folder, {"pins": {}, "keep": [], "extra": []})
    path = anon.case_memory_path(folder)
    assert folder not in path.parents
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert anon.case_memory_path(folder / "originals") == path


def test_rerun_keeps_a_fake_holding_another_persons_short_name():
    """Simulated on the real case: "Joana" was about to become "Jaime"
    because another person is a real "Ana". The lawyer had read "Joana"."""
    hits = {w: anon.Hit(w, "name", 1000.0, 11.0) for w in ("Rosalva", "Ana")}
    mapping, kinds = anon.build_map(hits, {"Rosalva": "Joana"})
    assert mapping["Rosalva"] == "Joana"
    assert "ana" not in mapping["Ana"].casefold()


def test_firm_named_after_a_person_keeps_a_person_fake(monkeypatch):
    monkeypatch.setattr(anon, "MODEL_PERSON_TOKENS", {"Rosalva Monforte"})
    hits = {"Monforte": anon.Hit("Monforte", "last", 1000.0, 11.0),
            "MONFORTE": anon.Hit("MONFORTE", "org", 1000.0, 11.0),
            "Monforte Advogados": anon.Hit("Monforte Advogados", "org", 1000.0, 11.0)}
    mapping, kinds = anon.build_map(hits, {"MONFORTE": "GRANITO"})
    assert mapping["Monforte"] in anon.LASTS
    assert mapping["MONFORTE"] == mapping["Monforte"].upper()
    assert mapping["Monforte Advogados"] == mapping["Monforte"] + " Advogados"
    assert not anon.check_map(mapping, kinds)


def test_stale_full_name_pin_is_rebuilt():
    hits = {"Monforte": anon.Hit("Monforte", "org", 1000.0, 11.0),
            "Rosalva": anon.Hit("Rosalva", "org", 1000.0, 11.0),
            "Monforte Rosalva": anon.Hit("Monforte Rosalva", "org", 1000.0, 11.0)}
    pins = {"Monforte": "Helix", "Rosalva": "Coral", "Monforte Rosalva": "Granito Leste"}
    mapping, kinds = anon.build_map(hits, pins)
    assert mapping["Monforte Rosalva"] == "Helix Coral"


def test_firm_name_reuses_the_partners_fake(monkeypatch):
    monkeypatch.setattr(anon, "MODEL_PERSON_TOKENS", {"Rosalva Monforte"})
    hits = {"Monforte": anon.Hit("Monforte", "last", 1000.0, 11.0),
            "Monforte Advogados": anon.Hit("Monforte Advogados", "org", 1000.0, 11.0)}
    mapping, kinds = anon.build_map(hits)
    assert mapping["Monforte Advogados"] == mapping["Monforte"] + " Advogados"
    assert not anon.check_map(mapping, kinds)
    assert anon.check_map({"Monforte": "Duarte", "Monforte Advogados": "Helix Advogados"},
                          {"Monforte": "last", "Monforte Advogados": "org"}) == {"Monforte Advogados": "two_fakes"}


def test_review_1334_pin_equal_to_a_new_real_name_is_dropped():
    hits = {w: anon.Hit(w, k, 1000.0, 11.0) for w, k in (("Rosalva", "name"), ("Léo", "name"), ("IPE", "org"), ("Quintino", "name"))}
    mapping, kinds = anon.build_map(hits, {"Rosalva": "Leo", "Quintino": "Ipe"})
    assert mapping["Rosalva"] != "Leo" and mapping["Quintino"] != "Ipe"
    assert not anon.check_map(mapping, kinds)


def test_review_1334_kept_word_stays_only_in_company_names(tmp_path):
    hits = {"Clara Santos": anon.Hit("Clara Santos", "name", 1000.0, 11.0),
            "Santos": anon.Hit("Santos", "last", 1000.0, 11.0),
            "Porto Santos": anon.Hit("Porto Santos", "org", 1000.0, 11.0),
            "Porto": anon.Hit("Porto", "org", 1000.0, 11.0)}
    anon.drop_kept_terms(hits, ["Santos"])
    mapping, kinds = anon.build_map(hits)
    assert "Santos" not in mapping["Clara Santos"]
    assert mapping["Porto Santos"].endswith(" Santos")


def test_review_1334_broken_memory_stops_and_is_not_overwritten(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    path = anon.case_memory_path(folder)
    path.write_text('{"pins": {}, "keep": null}')
    with pytest.raises(SystemExit):
        run(monkeypatch, capsys, folder)
    assert path.read_text() == '{"pins": {}, "keep": null}'


def test_review_1334_dry_run_saves_nothing_and_unseen_pins_survive(tmp_path, monkeypatch, capsys):
    import json
    folder = tmp_path / "caso"
    folder.mkdir()
    make_docx(folder / "contrato.docx")
    anon.save_case_memory(folder, {"pins": {"Zeferino": "Bruno"}, "keep": [], "extra": []})
    run(monkeypatch, capsys, folder, "--dry-run")
    assert json.loads(anon.case_memory_path(folder).read_text())["pins"] == {"Zeferino": "Bruno"}
    run(monkeypatch, capsys, folder)
    pins = json.loads(anon.case_memory_path(folder).read_text())["pins"]
    assert pins["Zeferino"] == "Bruno" and "Rosalva" in pins
