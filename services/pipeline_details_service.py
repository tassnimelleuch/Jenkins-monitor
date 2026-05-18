from collectors.jenkins_collector import get_pipeline_details


def get_pipeline_details_summary():
    details = get_pipeline_details()
    if not details:
        return {
            'connected': False,
            'message': 'Could not fetch pipeline details from Jenkins. Verify JENKINS_URL, JENKINS_JOB, and credentials.'
        }
    return details
