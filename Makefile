.PHONY: demo test

# Backend + frontend with model warm-up; opens http://localhost:5173/demo. Ctrl-C stops both.
demo:
	scripts/demo.sh

test:
	$(MAKE) -C backend test lint
	cd frontend && npm run typecheck
