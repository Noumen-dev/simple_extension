PYTHON ?= python3
SERVER_DIR := $(abspath ../server)

.PHONY: check test merge-deps fetch-binary image

check:
	node scripts/check-mail-excel.mjs

test: check
	$(PYTHON) -m unittest discover -s tests -v

merge-deps:
	$(PYTHON) scripts/merge_node_requirements.py

# Первый репо собирает бинарь в Docker и кладёт его сюда.
fetch-binary:
	$(MAKE) -C $(SERVER_DIR) artifact

# Образ расширения: свой Dockerfile, context = extension/.
image: fetch-binary
	docker build -t mail-excel-nodes:0.2.2 .
