import azure.functions as func
import logging
import json

# Define the function app
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="processTelemetry", methods=["POST"])  # Name it based on your Azure route setup
def process_telemetry(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Processing telemetry data.')

    try:
        # Attempt to read the JSON payload from the request
        telemetry_data = req.get_json()
        logging.info(f"Received telemetry data: {json.dumps(telemetry_data)}")

        # You could add additional processing logic here if needed
        # For example, saving it to a database or triggering another function

        # Return a success response
        return func.HttpResponse(
            "Telemetry data processed successfully.",
            status_code=200
        )
    except ValueError as e:
        # Log the error if JSON parsing fails
        logging.error(f"Failed to parse JSON: {e}")
        return func.HttpResponse(
            "Invalid JSON received.",
            status_code=400
        )
    except Exception as e:
        # Log any other errors that occur
        logging.error(f"An error occurred: {e}")
        return func.HttpResponse(
            "An error occurred while processing telemetry data.",
            status_code=500
        )