# Feedback log

One line per fix that came from a person checking a copy. No real values.

| Date | Checklist # | Cause | Test |
|---|---|---|---|
| 2026-10-21 | 1 | A name in capitals was replaced; the same name in normal case was not, and the text check matched exact case only | `test_pdf_name_in_caps_and_in_title_case_both_replaced` |
| 2026-10-21 | 2 | Signature list and event log got different fakes for one signer: each case form picked its own fake | `test_one_person_one_fake_in_caps_and_title_case` |
| 2026-10-21 | 2 | One signer was Karen in the Word file and the email, Nina in the PDF: each file had its own map | `test_folder_mode_one_fake_per_person_across_files` |
| 2026-10-21 | 2 | One company spelled two ways, one letter apart, became Alfa and Atlas | `test_spelling_variant_is_one_company_and_legal_form_stays` |
| 2026-10-21 | 1 | An IP stayed in hyphenated form inside a provider host name | `test_pdf_name_in_caps_and_in_title_case_both_replaced` |
| 2026-10-21 | 5 | A word found only in the email got a fake too wide for the PDF's boxes | `test_folder_fake_fits_pdf_box_for_value_found_in_other_file` |
| 2026-10-21 | 6 | Copies must keep the input's file name; names in it are replaced | `test_copy_keeps_input_name_with_names_replaced` |
| 2026-10-21 | 1 | 56 heading and role words in capitals were taken for names (26-file case) | `test_heading_in_caps_is_not_a_name` |
| 2026-10-21 | 5 | Word's built-in style list matched a capitalized word in 9 files | `test_style_list_is_not_checked` |
| 2026-10-21 | 1 | Names inside longer words in PDFs got fakes too wide for their boxes | `test_buried_name_box_is_measured_and_never_left` |
| 2026-10-21 | 5 | A damaged Word file (missing part, corrupt part) had no copy | `test_docx_python_docx_cannot_open_uses_raw_xml` |
| 2026-10-21 | 1 | A foreign company address was in 12 copies, 7 marked CLEAN | `test_extra_terms_file_replaces_undetected_address` |
| 2026-10-21 | 1 | A letterhead picture in a PDF showed an address, phones, and a name | `test_pdf_letterhead_picture_with_real_value_is_blanked` |
| 2026-10-21 | 3 | The fake HELENA contained the real name Helen; A/S was taken for a company | hand fix; `a/s` added to legal forms |
| 2026-10-21 | 3 | SAIBAM (opening word of a power of attorney) became a fake name: the capitals rule has no context | `test_context_rejects_heading_word_and_keeps_company_form` |
| 2026-10-21 | 2 | One company appeared as Alfa Brasil, Alfa Norte, Alfa IPE: generic words got fakes; two companies shared IPE | `test_context_rejects_heading_word_and_keeps_company_form` |
| 2026-10-21 | 1 | GLiNER found a first-name variant, two addresses, a foreign phone, a company left in shared copies | `test_gliner_finds_what_the_model_missed` |
| 2026-10-21 | 1 | A fake company word was black next to a red "BRAZIL": the PDF writer drew every fake in black regular Helvetica, and the Word writer put a whole paragraph in its first run (199 lines changed in the case) | `test_pdf_fake_keeps_color_and_weight`, `test_docx_replacement_keeps_each_runs_format`, `LOOK` check |
| 2026-10-21 | 1 | "Joana", "Eva", "ALFA A/S": is this one person or two? Checked by hand in the map | `GROUP` lines |
| 2026-10-21 | many | The fake pools ran out (46 first names for about 100 people): "Clara" stood for 5 real words, "Irene" for 2 | larger pools, case-blind reuse check, `test_no_fake_is_shared_even_when_the_pool_runs_out`, `MAP_CHECK` |
| 2026-10-21 | 1 | The counterparty company in capitals became the fake person "Irene" | `test_capitals_word_inside_a_gliner_company_gets_a_company_fake` |
| 2026-10-21 | many | After hand fixes the map still held the old fakes | `MAP_SYNC` check |
| 2026-10-21 | all | A rerun picked new fakes, so the lawyer's copies and the new ones did not match; hand decisions were lost | case memory (`MEMORY` line), `_keep_terms.txt`, `test_rerun_keeps_fakes_kept_words_and_added_values` |
| 2026-10-21 | many | Simulated rerun: "Joana" would become "Jaime" because another person is "Ana"; a firm partner would get a company fake | own-name rule, person evidence decides the merge, `test_rerun_keeps_a_fake_holding_another_persons_short_name`, `test_firm_named_after_a_person_keeps_a_person_fake` |
| 2026-10-21 | many | GLiNER run: a lowercase legal phrase was taken for a company; "National Mining Agency" got a fake equal to itself | `test_gliner_lowercase_phrase_is_not_a_company`, `test_company_made_only_of_kept_words_is_not_in_the_map` |
| 2026-10-31 | 1 | Public bodies (ANVISA, ANM, ANA, CNAE, ABNT, INSS, CPF) and deed and email template words (QUANTOS, VIREM, PRESENTS, OUTORGANTE, CAUTION) got fakes; a legal opinion lost its agencies | `test_public_bodies_and_template_words_are_not_names`, `test_ana_is_a_name_unless_the_water_agency_is_named` |
| 2026-10-31 | 1 | A 5-letter name was replaced inside CLAUSE (103 times) and "Environment" inside "Environmental"; the damaged word showed the real name | `test_name_is_not_replaced_inside_a_longer_word` |
| 2026-10-31 | 2 | Possessive forms (X's, X’s) got their own fakes and lost the 's: one man read as three people | `test_possessive_is_the_same_person` |
| 2026-10-31 | 1 | The law firm's case number (FID…), iManage numbers in file names and its KL/Ref number were not replaced | `test_law_firm_document_numbers_are_found` |
| 2026-10-31 | 1 | "Portuguese" and "PoA" were taken for names | `test_language_names_are_kept` |
| 2026-10-31 | 1 | Checked against the originals: GRANTEE, COVENANTED, BPTO, DBE, DDHS, LATAM and plural CONTRATANTES also got fakes | `test_public_bodies_and_template_words_are_not_names` |
| 2026-10-31 | 2 | Two men (Mr./Sr. in the originals) got the female fakes Joana and Maia: the fake pool had no gender | `test_fake_first_name_matches_the_persons_gender`, `test_earlier_fake_of_the_wrong_gender_is_replaced` |
| 2026-10-31 | 2 | A woman whose only gender clue was "(She/Her)" in her signature got the male fake Gabriel | `test_gender_from_signature_pronouns_and_greetings` |
| 2026-10-31 | 1 | Checked against the originals: an RG without dots after "n°", a Danish CVR number after "Commercial Register No.", an employee number and a SHA-512 hash written with the ligature ﬀ stayed in the copies | `test_id_numbers_without_dots_are_found`, `test_hash_with_a_typographic_ligature_is_found` |
| 2026-10-31 | 1 | The initials before "/Ref" (the firm's) stayed in three copies and named the firm | `test_law_firm_document_numbers_are_found` |
| 2026-10-31 | 6 | Every email attachment was dropped, so a case lost its drafts, the counterparty's documents and the screenshots | `test_email_document_attachments_are_kept_and_cleaned`, `test_email_pictures_are_kept_or_dropped_by_what_they_show`, `test_leak_check_reads_attached_documents`, `test_folder_run_shares_fakes_with_attached_documents` |
| 2026-10-31 | 1 | Attached PDFs still leaked: phones written as several boxes, company phrases found only as a whole, short values glued to a mark, links broken over two lines, and names in letterheads and scanned pages readable by OCR | `test_pdf_phone_written_as_several_boxes_is_replaced`, `test_phrase_words_become_tokens_of_their_own`, `test_pdf_short_value_glued_to_a_mark_is_replaced`, `test_pdf_link_broken_over_two_lines_is_cleared`, `test_pdf_scanned_page_has_its_names_painted_over`, `test_attachment_that_would_still_leak_is_dropped`, `test_lowercase_phrase_of_ordinary_words_is_not_a_person` |
| 2026-10-31 | 1 | An e-signature log carried the signers' latitude and longitude; names in scanned pages were still readable after one OCR pass | `test_signature_log_coordinates_are_masked`, `test_pdf_scanned_page_has_its_names_painted_over` |
| 2026-10-31 | 1 | A sworn translation attached to an email kept a home address written over line breaks, a CEP with a dot, two passport numbers, a register number, and ID numbers ending a sentence | `test_address_passport_and_register_in_a_translation_are_found` |
