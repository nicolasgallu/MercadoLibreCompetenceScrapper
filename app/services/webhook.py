import os
from google.cloud import run_v2 # The modern Cloud Run API
from app.settings.config import SECRET_GUIAS, PROJECT_ID, REGION, JOB_NAME
from flask import Blueprint, request, Response, jsonify
from app.utils.logger import logger

scrapping_event = Blueprint("scrapping_init", __name__, url_prefix="/webhooks/start_scrapping")

@scrapping_event.route("", methods=["POST"], strict_slashes=False)
def main():
    response = request.json
    
    if SECRET_GUIAS != response.get('secret'):
        return Response(status=401)
    
    logger.info(f"Triggering Cloud Job: {JOB_NAME}")

    try:
        # 1. Initialize the Cloud Run Client
        client = run_v2.JobsClient()

        # 2. Prepare the request
        # Format: projects/{project}/locations/{location}/jobs/{job}
        job_path = client.job_path(PROJECT_ID, REGION, JOB_NAME)
        
        # 3. Trigger the execution
        # Note: This is non-blocking. It tells Google to start the job and returns immediately.
        operation = client.run_job(name=job_path)
        
        logger.info(f"Cloud Job started successfully. Operation: {operation.operation.name}")
        
        return jsonify({
            "status": "accepted", 
            "message": "Cloud Job triggered",
            "job_id": operation.operation.name
        }), 202

    except Exception as e:
        logger.error(f"Failed to trigger Cloud Job: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500