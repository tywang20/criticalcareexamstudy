# Critical Care Qbank

Personal practice app for the questions in *SCCM Self-Assessment in Adult Multiprofessional Critical Care* (9th ed.).

- `scripts/extract.py` parses the PDF into `app/questions.json` and crops figures into `app/img/` (requires `pip install pymupdf`).
- `app/index.html` is the practice page, published as a private claude.ai artifact. Progress (answers and current question) is stored in the artifact's database so it syncs across devices.

The extracted questions and images are git-ignored on purpose.
