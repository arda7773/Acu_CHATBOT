# ACU ChatBot

ACU ChatBot is a Django-based AI chatbot web application built for answering questions about Acibadem University using real university data.

The system uses:
- Django 5 for the web application
- PostgreSQL + pgvector for storage and semantic retrieval
- Ollama for running the LLM locally
- Selenium for JavaScript-heavy pages during scraping
- Docker Compose for orchestration
- GitHub Actions for CI
- Render blueprint config for cloud deployment

## Features

- Chat interface for asking questions about Acibadem University
- Local LLM integration through Ollama
- Chat history stored in PostgreSQL
- Django admin panel for chat logs and scraped content
- Scraping pipeline for `acibadem.edu.tr` and Bologna pages
- Semantic retrieval with embeddings and pgvector
- Source links shown with answers
- Session-based multi-chat history

## Project Structure

```text
ACU_ChatBot/
├── docker-compose.yml
├── .env.example
├── README.md
├── docs/
└── webapp/
    ├── Dockerfile
    ├── entrypoint.sh
    ├── manage.py
    ├── requirements.txt
    ├── config/
    ├── chat/
    ├── scraper/
    ├── llm/
    ├── templates/
    └── static/
```

## System Architecture

The project runs as separate containers:

- `webapp`: Django application and REST/chat endpoints
- `db`: PostgreSQL database with `pgvector`
- `ollama`: local LLM service
- `selenium`: browser automation service for dynamic page scraping

Typical flow:

1. The user asks a question from the chat UI.
2. Django retrieves relevant context from scraped university data.
3. Semantic search uses embeddings stored in PostgreSQL/pgvector.
4. The selected context is sent to the local LLM through Ollama.
5. The answer and source URLs are returned to the UI and stored in chat history.

## Requirements

Before running the project, make sure you have:

- Docker
- Docker Compose

No paid external AI API is required.

## Environment Variables

Create a `.env` file in the project root. Example:

```env
POSTGRES_DB=acuchatbot
POSTGRES_USER=acuuser
POSTGRES_PASSWORD=acupassword
SECRET_KEY=change-this-to-a-random-secret-key
DEBUG=True
OLLAMA_MODEL=llama3.1:8b
```

You can start from `.env.example`.

## Setup and Run

From the project root:

```bash
docker compose up --build
```

This starts:

- PostgreSQL
- Ollama
- Selenium
- Django web application

The app will be available at:

- `http://localhost:8000`

Admin panel:

- `http://localhost:8000/admin`

## CI/CD

This repository includes a GitHub Actions workflow at:

```text
.github/workflows/ci.yml
```

The workflow currently does two safe checks on every push / pull request:

- installs Python dependencies
- runs Python syntax compilation and `manage.py check`
- builds the Django Docker image

This gives you a lightweight CI pipeline without changing runtime behavior.

## Cloud Deployment (Render)

The project now includes a Render blueprint file:

```text
render.yaml
```

It defines:

- `acu-chatbot-web` as the Django web service
- `acu-chatbot-db` as PostgreSQL
- `acu-chatbot-ollama` as a private Ollama service
- `acu-chatbot-selenium` as a private Selenium service

Important notes:

- Render deployment is configured as deploy-ready infrastructure, not as a guaranteed production-tuned setup.
- Ollama and Selenium on cloud instances can require more memory / paid plans than a free environment allows.
- The web service uses environment variables like `DATABASE_URL`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `OLLAMA_URL`, and `SELENIUM_URL`.

### Deploy on Render

1. Push the repository to GitHub.
2. In Render, create a new Blueprint instance from the repository.
3. Select `ACU_ChatBot/render.yaml`.
4. Review service plans and environment values.
5. Deploy the stack.

If you prefer to skip slow startup steps in cloud environments, you can override:

```env
LOAD_SEED_DATA=false
PULL_MODELS_ON_START=false
```

You can then run data loading / model pulling manually after deployment.

## First-Time Data Preparation

After the containers are running, you can load or scrape data and generate embeddings.

Open a shell in the Django container:

```bash
docker compose exec webapp bash
```

### Option 1: Load existing JSON seed data

```bash
python manage.py load_acu_data
```

### Option 2: Index sitemap and scrape live ACU pages

```bash
python manage.py index_sitemap
python manage.py scrape_acu_pages
```

Optional examples:

```bash
python manage.py scrape_acu_pages --limit 20
python manage.py scrape_acu_pages --url-contains bilgisayar
```

### Option 3: Scrape Bologna content

```bash
python manage.py scrape_bologna
```

### Generate embeddings for semantic search

```bash
python manage.py embed_content --source all
```

If you want to rebuild embeddings from scratch:

```bash
python manage.py embed_content --source all --reset
```

## Main Management Commands

```bash
python manage.py load_acu_data
python manage.py index_sitemap
python manage.py scrape_acu_pages
python manage.py scrape_bologna
python manage.py embed_content --source all
python manage.py pull_model
```

## Local LLM

The chatbot uses Ollama as a local model server.

Configured model:

- `llama3.1:8b` by default

Embedding model:

- `nomic-embed-text`

The startup flow also attempts to pull the configured models automatically.

## Admin Panel

The Django admin panel can be used to inspect:

- Chat sessions
- Chat messages
- Indexed URLs
- Scraped ACU pages
- Bologna programs

If needed, create an admin user:

```bash
docker compose exec webapp python manage.py createsuperuser
```

## Notes on Scraping

- Data is collected from publicly accessible university pages
- Selenium is used for JavaScript-heavy pages when required
- The scraper includes request delays to avoid aggressive traffic
- The chatbot is designed to answer from retrieved university data instead of generic model knowledge

## Demo Checklist

For a working demo, make sure these are ready:

- Containers are up with Docker Compose
- At least some ACU and Bologna data has been loaded
- Embeddings have been generated
- The chat interface is accessible
- Admin panel is accessible
- The chatbot can answer sample university-related questions with sources

## Current Scope

Implemented:

- Core Django web app
- Dockerized architecture
- Local LLM integration
- Chat history
- Scraping pipeline
- pgvector-based semantic retrieval
- Responsive chat UI
- GitHub Actions CI workflow
- Render deployment blueprint

Still expected separately from this repository:

- Final technical report
- Presentation/demo script
- Team contribution summary if required by the instructor
