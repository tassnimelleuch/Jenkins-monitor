import os
import importlib.util
from dotenv import load_dotenv

load_dotenv()  


def _require_postgres_driver():
    if importlib.util.find_spec('psycopg2') is None:
        raise RuntimeError(
            'PostgreSQL is required, but the psycopg2 driver is not installed in the active Python environment.'
        )


def _build_database_uri():
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        if not database_url.startswith('postgresql://'):
            raise RuntimeError(
                'Only PostgreSQL is supported. Set DATABASE_URL to a postgresql:// URL.'
            )
        _require_postgres_driver()
        return database_url

    host = os.getenv('POSTGRES_HOST')
    db_name = os.getenv('POSTGRES_DB')
    user = os.getenv('POSTGRES_USER')
    password = os.getenv('POSTGRES_PASSWORD')
    port = os.getenv('POSTGRES_PORT', '5432')

    if all([host, db_name, user, password]):
        _require_postgres_driver()
        return f'postgresql://{user}:{password}@{host}:{port}/{db_name}'

    raise RuntimeError(
        'PostgreSQL configuration is required. Set DATABASE_URL or POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, and POSTGRES_PASSWORD.'
    )


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret')
    SQLALCHEMY_DATABASE_URI = _build_database_uri()
    SQLALCHEMY_ENGINE_OPTIONS = {
        'connect_args': {
            'options': '-c timezone=UTC',
        },
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin')

    JENKINS_URL      = os.getenv('JENKINS_URL')
    JENKINS_USERNAME = os.getenv('JENKINS_USERNAME')
    JENKINS_TOKEN    = os.getenv('JENKINS_TOKEN')
    JENKINS_JOB      = os.getenv('JENKINS_JOB')
    JENKINS_BRANCH   = os.getenv('JENKINS_BRANCH', 'main')
    PIPELINE_STORE_SELECTED_BRANCH_ONLY = os.getenv(
        'PIPELINE_STORE_SELECTED_BRANCH_ONLY',
        'true',
    ).strip().lower() in ('1', 'true', 'yes', 'on')

    AZURE_SUBSCRIPTION_ID = os.getenv('AZURE_SUBSCRIPTION_ID')
    AKS_RESOURCE_GROUP    = os.getenv('AKS_RESOURCE_GROUP')
    AKS_CLUSTER_NAME      = os.getenv('AKS_CLUSTER_NAME')
    PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

    SONARCLOUD_BASE_URL = os.getenv("SONARCLOUD_BASE_URL", "https://sonarcloud.io/api")
    SONARCLOUD_PROJECT_KEY = os.getenv("SONARCLOUD_PROJECT_KEY", "tassnimelleuch_Django-app")
    SONARCLOUD_TOKEN = os.getenv("SONARCLOUD_TOKEN")

    GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")
    GITHUB_OWNER = os.getenv("GITHUB_OWNER")
    GITHUB_REPO = os.getenv("GITHUB_REPO")
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    DOCKERHUB_API_URL = os.getenv("DOCKERHUB_API_URL", "https://hub.docker.com/v2")
    DOCKERHUB_IMAGE = os.getenv("DOCKERHUB_IMAGE")
    DOCKERHUB_USERNAME = os.getenv("DOCKERHUB_USERNAME")
    DOCKERHUB_TAG = os.getenv("DOCKERHUB_TAG")
    DOCKERHUB_TOKEN = os.getenv("DOCKERHUB_TOKEN")
    DOCKERHUB_BUILD_TAG_SUFFIX = os.getenv("DOCKERHUB_BUILD_TAG_SUFFIX", "build-{build_number}")

    OLLAMA_BASE_URL = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    OLLAMA_CHAT_ENDPOINT = os.getenv('OLLAMA_CHAT_ENDPOINT', '/api/chat')
    OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen')
    OLLAMA_TIMEOUT = int(os.getenv('OLLAMA_TIMEOUT', '60'))
    EMBEDDING_MODEL = os.getenv(
        'EMBEDDING_MODEL',
        'sentence-transformers/all-MiniLM-L6-v2',
    )
    EMBEDDING_DEVICE = os.getenv('EMBEDDING_DEVICE', 'cpu')
    EMBEDDING_BATCH_SIZE = int(os.getenv('EMBEDDING_BATCH_SIZE', '32'))
    EMBEDDING_NORMALIZE = os.getenv(
        'EMBEDDING_NORMALIZE',
        'true',
    ).strip().lower() in ('1', 'true', 'yes', 'on')
    CHROMA_PERSIST_DIR = os.getenv(
        'CHROMA_PERSIST_DIR',
        os.path.join(os.getcwd(), '.chroma'),
    )
    FINOPS_CHROMA_COLLECTION = os.getenv(
        'FINOPS_CHROMA_COLLECTION',
        'finops_daily_analysis',
    )
    FINOPS_CHUNK_SIZE = int(os.getenv('FINOPS_CHUNK_SIZE', '900'))
    FINOPS_CHUNK_OVERLAP = int(os.getenv('FINOPS_CHUNK_OVERLAP', '120'))
    DASHBOARD_KPI_CHROMA_COLLECTION = os.getenv(
        'DASHBOARD_KPI_CHROMA_COLLECTION',
        'dashboard_kpi_documents',
    )
    DASHBOARD_KPI_CHUNK_SIZE = int(os.getenv('DASHBOARD_KPI_CHUNK_SIZE', '1100'))
    DASHBOARD_KPI_CHUNK_OVERLAP = int(os.getenv('DASHBOARD_KPI_CHUNK_OVERLAP', '140'))

    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_HOST = "127.0.0.1"
    CACHE_REDIS_PORT = 6379
    CACHE_REDIS_DB = 0
    CACHE_DEFAULT_TIMEOUT = 1800
    FINOPS_SYNC_INTERVAL_SECONDS = int(os.getenv('FINOPS_SYNC_INTERVAL_SECONDS', '1800'))
