# Vietnamese style guide

Normative rules for `src/i18n/locales/vi.json`. Mechanically checkable rules are
enforced by `catalogParity.test.ts`, `glossary.test.ts` and `qa.test.ts`; the rest
are for translation reviewers.

---

## 1. Punctuation

| rule | example |
|---|---|
| No space before `:` `;` `?` `!` | `Bạn có chắc không?` |
| Ellipsis: `…` (U+2026) | `Đang tải…` |
| No trailing period on buttons/labels | `Lưu`, not `Lưu.` |
| Quotation marks: `"…"` | |

---

## 2. Spacing

Words are separated by spaces, one per syllable. Use a space between Vietnamese
text and Latin words or numbers (`3 tệp`, `kết nối GitHub`).

---

## 3. Do not translate

Product names stay as-is. The list is in `glossary.json` under `dnt`.

---

## 4. Register and tone

- Address the user as **bạn**; refer to the product as itself, never "tôi/chúng tôi"
  unless English says "we".
- Buttons and labels use bare verbs: `Lưu`, `Hủy`, `Kết nối`, `Xóa`.
- Use the modern spelling: `hòa`, `thủy`, `Hủy` (tone mark on the second vowel is not
  used; follow the new-style placement).
- Keep common technical terms Vietnamese developers use untranslated: `token`,
  `webhook`, `cron`, `prompt`, `agent`, `API`, `session` may be `phiên`.

---

## 5. Plurals

Vietnamese has **one** CLDR plural category, `other`. Translate only the `_other`
key; never emit `_one`. Classifiers are optional: `3 tệp`, `5 phiên`.

---

## 6. Gender

Vietnamese has no grammatical gender. Avoid gendered kinship pronouns.

---

## 7. Diacritics

Tone marks and diacritics are mandatory and distinguish meaning (`ma`, `má`, `mả`,
`mã`, `mạ`). Always use precomposed Unicode (NFC). A missing diacritic is an error.

---

## 8. Terminology

Resume = `Tiếp tục lại` (pick up), Continue = `Tiếp tục` (next step) must stay
distinct: use `Khôi phục chạy` / `Chạy tiếp` for Resume and `Tiếp tục` for Continue.

---

## 9. What is mechanically enforced

| rule | gate |
|---|---|
| placeholder parity with English | `catalogParity.test.ts` |
| correct CLDR plural categories (1) | `catalogParity.test.ts` |
| do-not-translate terms present | `glossary.test.ts` |
| balanced delimiters, no stray whitespace | `qa.test.ts` |
