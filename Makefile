prepare-venv:
	bash ./prepare_venv.sh

requirements:
	bash ./install_requirements.sh

lint:
	python -m flake8 . --extend-exclude=env/ --count --select=E9,F63,F7,F82 --show-source --statistics
	python -m flake8 . --extend-exclude=env/ --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

test:
	bash ./run_tests.sh

publish:
	rm -rf dist
	poetry build
	twine upload dist/*

