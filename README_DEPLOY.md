# ICL Chemical Manager — Original Website Deploy Package

This package is the production website build with the merged V8 OCR flow and the restored ICL UI/UX.

## Streamlit Cloud entrypoint
- `streamlit_app.py`

## Included
- Production website code
- ICL logo assets and theme resources
- `.streamlit/config.toml`
- `pyproject.toml` / `uv.lock`
- License

## Secrets
No credentials are included in this archive. Reuse the existing Streamlit Cloud Secrets for the production app, including the database, authentication, and OCR backend configuration already used by the site.

Relevant application-level keys used by this build include `SUPABASE_DB_URL`, `OCR_API_KEY`, `OCR_V8_API_URL` (optional if using the coded endpoint), and `BOOTSTRAP_ADMIN_EMAIL`, plus the existing Streamlit authentication configuration.

## Deployment
Upload the contents of this folder to the deployment repository root and keep `streamlit_app.py` as the Streamlit entrypoint.
