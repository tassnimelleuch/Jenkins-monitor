import os
from dotenv import load_dotenv

load_dotenv()  

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret')

    JENKINS_URL      = os.getenv('JENKINS_URL')
    JENKINS_USERNAME = os.getenv('JENKINS_USERNAME')
    JENKINS_TOKEN    = os.getenv('JENKINS_TOKEN')
    JENKINS_JOB      = os.getenv('JENKINS_JOB')