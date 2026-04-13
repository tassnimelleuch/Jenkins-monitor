import os
from dotenv import load_dotenv

load_dotenv()  
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret')

    JENKINS_URL      = os.getenv('JENKINS_URL')
    JENKINS_USERNAME = os.getenv('JENKINS_USERNAME')
    JENKINS_TOKEN    = os.getenv('JENKINS_TOKEN')
    JENKINS_JOB      = os.getenv('JENKINS_JOB')

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


    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_HOST = "127.0.0.1"
    CACHE_REDIS_PORT = 6379
    CACHE_REDIS_DB = 0
    CACHE_DEFAULT_TIMEOUT = 1800
