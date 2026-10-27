.PHONY: install demo test lint eval eval-claude replay live sample

install:
	pip install -r requirements.txt ruff

demo:
	streamlit run app/streamlit_app.py

test:
	pytest -q

lint:
	ruff check .

eval:
	python -m evaluation.run_eval --update-readme

eval-claude:
	python -m evaluation.run_eval --mode claude

replay:
	python -m radar replay

# pull real public sources now (openFDA works without a key; set OPENFDA_API_KEY for a higher quota)
live:
	python -m radar run hs_troponin_poc_live

sample:
	python scripts/make_sample_upload.py
