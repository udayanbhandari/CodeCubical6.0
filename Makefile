.PHONY: dev dev-backend dev-frontend test demo docker-up docker-down

dev:
	@echo "Starting ProofData Backend and Frontend..."
	powershell -Command "Start-Process powershell -ArgumentList '-NoExit', '-Command', '.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --app-dir backend'; Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd frontend; npm run dev'"

dev-backend:
	.\venv\Scripts\python -m uvicorn app.main:app --reload --port 8000 --app-dir backend

dev-frontend:
	cd frontend && npm run dev

test:
	.\venv\Scripts\pytest backend/tests

demo:
	.\venv\Scripts\python backend/app/demo_seed.py

docker-up:
	docker-compose up --build

docker-down:
	docker-compose down
